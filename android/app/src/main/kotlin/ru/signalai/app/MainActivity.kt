package ru.signalai.app

import android.Manifest
import android.content.Intent
import android.os.Build
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

// Нативный мост приложения. Сознательно без плагинов и androidx-обвязки:
// в приложении, которое подтверждает сделки, чем меньше стороннего кода, тем
// лучше. Общие с фоновым контуром методы живут в NativeChannel, здесь — только
// то, что требует активити: разрешения, биометрия и управление контуром.
class MainActivity : FlutterActivity() {
    private val channelName = "ru.signalai.app/native"

    private val shared by lazy { NativeChannel(applicationContext) }
    private val biometrics by lazy { Biometrics(this) }

    /** Адрес, по которому нажали в уведомлении, до готовности Flutter UI. */
    private var pendingPayload: String? = null

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        readPayload(intent)
    }

    private fun readPayload(intent: Intent?) {
        val payload = intent?.getStringExtra(Notifications.PAYLOAD)
        if (!payload.isNullOrEmpty()) pendingPayload = payload
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        readPayload(intent)

        // Personal thin-client обязан следить за сервером без отдельного
        // скрытого переключателя. Раньше backgroundEnabled по умолчанию был
        // false, поэтому владелец ожидал push, а Android-monitor физически не
        // запускался. Запоминаем режим сразу: после reboot BootReceiver его
        // восстановит. Если device-token ещё не сохранён, первый poll честно
        // завершится ошибкой, но следующий уже подхватит credential.
        MonitorAlarm.remember(this, MonitorService.MODE_PERSISTENT)
        if (!MonitorService.running) {
            MonitorService.start(this, MonitorService.MODE_PERSISTENT)
        }

        // На Android 13+ без runtime permission канал SIGNALS молчит даже при
        // исправном monitor. Для персонального приложения просим разрешение
        // сразу после первого запуска новой сборки, а не прячем его в Settings.
        if (Build.VERSION.SDK_INT >= 33 && !Notifications.hasPermission(this)) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1001)
        }

        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, channelName)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "requestNotificationPermission" -> {
                        if (Build.VERSION.SDK_INT >= 33 &&
                            !Notifications.hasPermission(this)
                        ) {
                            requestPermissions(
                                arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1001,
                            )
                        }
                        result.success(Notifications.hasPermission(this))
                    }

                    "notificationSettings" -> {
                        val intent = android.content.Intent(
                            android.provider.Settings.ACTION_APP_NOTIFICATION_SETTINGS,
                        )
                            .putExtra(android.provider.Settings.EXTRA_APP_PACKAGE, packageName)
                            .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
                        startActivity(intent)
                        result.success(true)
                    }

                    "takeLaunchPayload" -> {
                        val payload = pendingPayload
                        pendingPayload = null
                        result.success(payload ?: "")
                    }

                    "biometricsAvailable" -> result.success(biometrics.isAvailable())
                    "confirmMethod" -> result.success(biometrics.method())
                    "confirmMethodDetails" -> result.success(biometrics.methodDetails())

                    "biometricConfirm" -> biometrics.confirm(
                        call.argument<String>("title") ?: "Подтвердите сделку",
                        call.argument<String>("subtitle") ?: "",
                    ) { ok -> result.success(ok) }

                    // ── Фоновый контур ────────────────────────────────────
                    "monitorStart" -> {
                        val mode = call.argument<String>("mode")
                            ?: MonitorService.MODE_PERSISTENT
                        MonitorAlarm.remember(this, mode)
                        if (!MonitorService.running) MonitorService.start(this, mode)
                        result.success(true)
                    }

                    "monitorStop" -> {
                        // Остановка из UI остаётся доступной как аварийная
                        // диагностика, но следующий обычный запуск приложения
                        // снова включает personal monitor.
                        MonitorAlarm.remember(this, null)
                        MonitorAlarm.cancel(this)
                        MonitorService.stop(this)
                        result.success(true)
                    }

                    "monitorRunning" -> result.success(MonitorService.running)

                    else -> shared.handle(call, result)
                }
            }
    }
}
