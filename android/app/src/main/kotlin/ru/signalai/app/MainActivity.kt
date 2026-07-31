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

    /**
     * Адрес, по которому нажали в уведомлении, — до того, как интерфейс за
     * ним придёт.
     *
     * Хранится здесь, а не отдаётся сразу в Dart: при холодном старте
     * приложение получает намерение раньше, чем поднимется движок Flutter,
     * и отправлять некому. Интерфейс забирает адрес сам, когда готов.
     */
    private var pendingPayload: String? = null

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        // Заменяем намерение активити: без этого следующий `takeLaunchPayload`
        // прочитал бы то, с которым приложение запустилось в прошлый раз.
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
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, channelName)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    // Разрешение на уведомления (Android 13+). До 13 оно не
                    // требуется — отвечаем текущим состоянием сразу.
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

                    // Системный экран уведомлений приложения. Нужен, когда
                    // разрешение отклонено дважды: Android больше не покажет
                    // диалог, и единственный путь — включить руками там.
                    "notificationSettings" -> {
                        val intent = android.content.Intent(
                            android.provider.Settings.ACTION_APP_NOTIFICATION_SETTINGS,
                        )
                            .putExtra(android.provider.Settings.EXTRA_APP_PACKAGE, packageName)
                            .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
                        startActivity(intent)
                        result.success(true)
                    }

                    // Адрес из нажатого уведомления. Отдаётся один раз:
                    // повторное открытие той же идеи при каждом возврате в
                    // приложение — это не диплинк, а навязчивость.
                    "takeLaunchPayload" -> {
                        val payload = pendingPayload
                        pendingPayload = null
                        result.success(payload ?: "")
                    }

                    "biometricsAvailable" -> result.success(biometrics.isAvailable())

                    // Не «да/нет», а чем именно: владелец должен видеть, что
                    // сделку подтвердит отпечаток, ПИН — или ничего.
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
                        MonitorService.start(this, mode)
                        result.success(true)
                    }

                    "monitorStop" -> {
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
