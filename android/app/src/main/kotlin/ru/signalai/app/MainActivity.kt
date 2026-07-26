package ru.signalai.app

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

// Нативный мост приложения. Сознательно без плагинов и androidx-обвязки:
// маленький канал закрывает две потребности — путь для файлового хранилища
// и локальные уведомления о сигналах. Меньше стороннего кода в приложении,
// которое подтверждает сделки.
class MainActivity : FlutterActivity() {
    private val channelName = "ru.signalai.app/native"
    private val notificationChannelId = "signals"

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, channelName)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    // Каталог файлов приложения — для JSON-хранилища настроек.
                    "filesDir" -> result.success(filesDir.absolutePath)

                    // Разрешение на уведомления (Android 13+). До 13 оно не
                    // требуется — отвечаем текущим состоянием сразу.
                    "requestNotificationPermission" -> {
                        if (Build.VERSION.SDK_INT >= 33 && !hasNotificationPermission()) {
                            requestPermissions(
                                arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1001
                            )
                        }
                        result.success(hasNotificationPermission())
                    }

                    // Локальное уведомление о сигнале. Работает, пока процесс
                    // жив; фоновое расписание — задача серверного контура.
                    "notify" -> {
                        val title = call.argument<String>("title") ?: "SignalAI"
                        val body = call.argument<String>("body") ?: ""
                        val id = call.argument<Int>("id") ?: 1
                        result.success(postNotification(id, title, body))
                    }

                    else -> result.notImplemented()
                }
            }
    }

    private fun hasNotificationPermission(): Boolean =
        Build.VERSION.SDK_INT < 33 ||
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) ==
                PackageManager.PERMISSION_GRANTED

    private fun postNotification(id: Int, title: String, body: String): Boolean {
        if (!hasNotificationPermission()) return false
        val manager = getSystemService(NOTIFICATION_SERVICE) as NotificationManager

        val builder = if (Build.VERSION.SDK_INT >= 26) {
            manager.createNotificationChannel(
                NotificationChannel(
                    notificationChannelId,
                    "Сигналы",
                    NotificationManager.IMPORTANCE_HIGH
                ).apply { description = "Новые торговые идеи с высокой оценкой" }
            )
            Notification.Builder(this, notificationChannelId)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this).setPriority(Notification.PRIORITY_HIGH)
        }

        val intent = Intent(this, MainActivity::class.java)
        val pending = PendingIntent.getActivity(
            this, 0, intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        manager.notify(
            id,
            builder
                .setSmallIcon(applicationInfo.icon)
                .setContentTitle(title)
                .setContentText(body)
                .setStyle(Notification.BigTextStyle().bigText(body))
                .setAutoCancel(true)
                .setContentIntent(pending)
                .build()
        )
        return true
    }
}
