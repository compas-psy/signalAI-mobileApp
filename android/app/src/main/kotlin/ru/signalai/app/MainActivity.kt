package ru.signalai.app

import android.Manifest
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private val channelName = "ru.signalai.app/native"
    private val notificationRequest = 1001

    private val shared by lazy { NativeChannel(applicationContext) }
    private val biometrics by lazy { Biometrics(this) }

    private var pendingPayload: String? = null
    private var exactPromptLaunched = false

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        readPayload(intent)
    }

    override fun onResume() {
        super.onResume()
        // Возврат со страницы special access: новый exact alarm ставим сразу,
        // не ждём завершения ранее поставленного degraded fallback.
        if (MonitorAlarm.exactAllowed(this)) {
            MonitorAlarm.remembered(this)?.let {
                MonitorAlarm.schedule(this, it, MonitorAlarm.DEFAULT_MINUTES)
            }
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == notificationRequest) requestExactAlarmAccessIfNeeded()
    }

    private fun readPayload(intent: Intent?) {
        val payload = intent?.getStringExtra(Notifications.PAYLOAD)
        if (!payload.isNullOrEmpty()) pendingPayload = payload
    }

    private fun ensureSignalPermissions() {
        if (Build.VERSION.SDK_INT >= 33 && !Notifications.hasPermission(this)) {
            requestPermissions(
                arrayOf(Manifest.permission.POST_NOTIFICATIONS),
                notificationRequest,
            )
            return
        }
        requestExactAlarmAccessIfNeeded()
    }

    private fun requestExactAlarmAccessIfNeeded() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S ||
            MonitorAlarm.exactAllowed(this) || exactPromptLaunched
        ) {
            return
        }
        exactPromptLaunched = true
        try {
            startActivity(
                Intent(
                    Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM,
                    Uri.parse("package:$packageName"),
                ),
            )
        } catch (_: Exception) {
            // Не роняем приложение на OEM-прошивке без стандартной страницы.
            startActivity(
                Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                    .setData(Uri.parse("package:$packageName")),
            )
        }
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        readPayload(intent)

        // В personal thin-client фоновый монитор — часть базового продукта,
        // а не скрытая опция. Один запуск приложения включает его и сохраняет
        // восстановление после reboot.
        MonitorAlarm.remember(this, MonitorService.MODE_PERSISTENT)
        if (!MonitorService.running) {
            MonitorService.start(this, MonitorService.MODE_PERSISTENT)
        }
        ensureSignalPermissions()

        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, channelName)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "requestNotificationPermission" -> {
                        if (Build.VERSION.SDK_INT >= 33 &&
                            !Notifications.hasPermission(this)
                        ) {
                            requestPermissions(
                                arrayOf(Manifest.permission.POST_NOTIFICATIONS),
                                notificationRequest,
                            )
                        } else {
                            requestExactAlarmAccessIfNeeded()
                        }
                        result.success(Notifications.hasPermission(this))
                    }

                    "notificationSettings" -> {
                        val intent = Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                            .putExtra(Settings.EXTRA_APP_PACKAGE, packageName)
                            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                        startActivity(intent)
                        result.success(true)
                    }

                    "exactAlarmAllowed" -> result.success(MonitorAlarm.exactAllowed(this))

                    "exactAlarmSettings" -> {
                        exactPromptLaunched = false
                        requestExactAlarmAccessIfNeeded()
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

                    "monitorStart" -> {
                        val mode = call.argument<String>("mode")
                            ?: MonitorService.MODE_PERSISTENT
                        MonitorAlarm.remember(this, mode)
                        if (!MonitorService.running) MonitorService.start(this, mode)
                        MonitorAlarm.schedule(this, mode)
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
