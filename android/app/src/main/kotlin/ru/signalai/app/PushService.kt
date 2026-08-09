package ru.signalai.app

import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import io.flutter.FlutterInjector
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.embedding.engine.dart.DartExecutor
import io.flutter.plugin.common.MethodChannel

/**
 * Persistent, low-latency server → device notification channel.
 *
 * The service does not calculate signals and never sends broker orders.  Its
 * only job is to keep one authenticated SSE connection to the SignalAI VPS,
 * hand server events to Android notifications, and reconnect after VPN/Wi-Fi
 * changes.  The existing exact-alarm monitor remains an independent fallback.
 */
class PushService : Service() {

    companion object {
        private const val NOTIFICATION_ID = 43
        private const val CHANNEL = "ru.signalai.app/native"
        private const val ENTRYPOINT_LIBRARY = "package:signalai/monitor/push_entrypoint.dart"
        private const val ENTRYPOINT = "signalaiPushMain"

        @Volatile
        var running: Boolean = false
            private set

        fun start(context: Context) {
            if (!Notifications.hasPermission(context) || running) return
            val intent = Intent(context, PushService::class.java)
            try {
                if (Build.VERSION.SDK_INT >= 26) {
                    context.startForegroundService(intent)
                } else {
                    context.startService(intent)
                }
            } catch (_: IllegalStateException) {
                // The exact-alarm monitor remains active as the durable
                // fallback.  Opening the app again will retry the push service.
            } catch (_: SecurityException) {
                // Missing FGS permission must not crash the trading client.
            }
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, PushService::class.java))
        }
    }

    private val shared by lazy { NativeChannel(applicationContext) }
    private var engine: FlutterEngine? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        startInForeground("Подключаю server push…")
        startEngine()
        running = true
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (engine == null) startEngine()
        running = true
        return START_STICKY
    }

    private fun startInForeground(text: String) {
        val notification = Notifications.ongoing(this, text)
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE,
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun startEngine() {
        if (engine != null) return
        val loader = FlutterInjector.instance().flutterLoader()
        loader.startInitialization(applicationContext)
        loader.ensureInitializationComplete(applicationContext, null)

        val flutterEngine = FlutterEngine(applicationContext)
        engine = flutterEngine

        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CHANNEL)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "pushReport" -> {
                        val summary = call.argument<String>("summary") ?: "Server push активен"
                        val manager = getSystemService(NOTIFICATION_SERVICE) as android.app.NotificationManager
                        manager.notify(NOTIFICATION_ID, Notifications.ongoing(this, summary))
                        result.success(true)
                    }
                    else -> shared.handle(call, result)
                }
            }

        flutterEngine.dartExecutor.executeDartEntrypoint(
            DartExecutor.DartEntrypoint(
                loader.findAppBundlePath(),
                ENTRYPOINT_LIBRARY,
                ENTRYPOINT,
            ),
        )
    }

    override fun onDestroy() {
        running = false
        engine?.destroy()
        engine = null
        super.onDestroy()
    }
}
