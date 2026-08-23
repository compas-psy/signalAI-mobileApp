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
    private val ownerStepUpSigner by lazy { OwnerStepUpSigner(this) }

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
        // Persistent SSE is the primary low-latency path.  Re-entering the
        // app is also a safe retry point if an OEM killed the FGS earlier.
        if (Notifications.hasPermission(this)) PushService.start(this)
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == notificationRequest) {
            if (Notifications.hasPermission(this)) PushService.start(this)
            requestExactAlarmAccessIfNeeded()
        }
    }

    private fun readPayload(intent: Intent?) {
        val payload = intent?.getStringExtra(Notifications.PAYLOAD)
        if (!payload.isNullOrEmpty()) {
            pendingPayload = payload
            return
        }
        val uri = intent?.data ?: return
        if (uri.scheme == "signalai" && uri.host == "idea") {
            val ideaId = uri.pathSegments.firstOrNull().orEmpty()
            if (ideaId.isNotEmpty()) pendingPayload = "idea:$ideaId"
        }
    }

    private fun ensureSignalPermissions() {
        if (Build.VERSION.SDK_INT >= 33 && !Notifications.hasPermission(this)) {
            requestPermissions(
                arrayOf(Manifest.permission.POST_NOTIFICATIONS),
                notificationRequest,
            )
            return
        }
        PushService.start(this)
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

        // Exact-alarm polling remains the independent fallback/reconciliation
        // path even after server push is enabled.
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
                            PushService.start(this)
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

                    "ownerStepUpPublicKey" ->
                        result.success(ownerStepUpSigner.ensurePublicKeySpkiB64())

                    "ownerStepUpSign" -> {
                        val message = call.argument<String>("message")
                        if (message.isNullOrEmpty()) {
                            result.error("args", "owner step-up message is required", null)
                        } else {
                            ownerStepUpSigner.signMessage(message) { signature ->
                                result.success(signature)
                            }
                        }
                    }

                    "ownerStepUpDelete" -> result.success(ownerStepUpSigner.deleteKey())

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
                        PushService.start(this)
                        result.success(true)
                    }

                    "monitorStop" -> {
                        // User explicitly disabling monitoring disables both
                        // delivery paths.  Re-enabling starts both again.
                        MonitorAlarm.remember(this, null)
                        MonitorAlarm.cancel(this)
                        MonitorService.stop(this)
                        PushService.stop(this)
                        result.success(true)
                    }

                    "monitorRunning" -> result.success(
                        MonitorService.running || PushService.running,
                    )

                    else -> shared.handle(call, result)
                }
            }
    }
}
