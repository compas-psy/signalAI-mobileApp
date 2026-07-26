package ru.signalai.app

import android.Manifest
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

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
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

                    "biometricsAvailable" -> result.success(biometrics.isAvailable())

                    // Не «да/нет», а чем именно: владелец должен видеть, что
                    // сделку подтвердит отпечаток, ПИН — или ничего.
                    "confirmMethod" -> result.success(biometrics.method())

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
