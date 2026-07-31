package ru.signalai.app

import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import io.flutter.FlutterInjector
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.embedding.engine.dart.DartExecutor
import io.flutter.plugin.common.MethodChannel

/**
 * Фоновый контур: сопровождение сделок и поиск идей, пока приложение закрыто.
 *
 * Внутри поднимается отдельный движок Flutter со своей точкой входа — весь
 * расчёт живёт в Dart, и дублировать его на Kotlin было бы вторым источником
 * правды. Ордера отсюда не отправляются: подтверждать сделку биометрией в фоне
 * некому, поэтому канал сервиса биометрию не предоставляет вовсе.
 *
 * Режима два. `persistent` — сервис живёт и спит между прогонами. `burst` —
 * один прогон и остановка. В обоих случаях будильник переставляется на час
 * вперёд: на Android 15+ система останавливает foreground-сервис типа dataSync
 * после шести часов в сутки, и без будильника «постоянный» режим молча
 * переставал бы следить.
 */
class MonitorService : Service() {

    companion object {
        const val EXTRA_MODE = "mode"
        const val MODE_PERSISTENT = "persistent"
        const val MODE_BURST = "burst"

        private const val NOTIFICATION_ID = 42
        private const val CHANNEL = "ru.signalai.app/native"
        private const val ENTRYPOINT_LIBRARY = "package:signalai/monitor/monitor_entrypoint.dart"
        private const val ENTRYPOINT = "signalaiMonitorMain"

        /** Идёт ли сейчас прогон — для экрана настроек. */
        @Volatile
        var running: Boolean = false
            private set

        fun start(context: Context, mode: String) {
            val intent = Intent(context, MonitorService::class.java).putExtra(EXTRA_MODE, mode)
            if (Build.VERSION.SDK_INT >= 26) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, MonitorService::class.java))
        }
    }

    private val shared by lazy { NativeChannel(applicationContext) }
    private var engine: FlutterEngine? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var mode: String = MODE_PERSISTENT

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        mode = intent?.getStringExtra(EXTRA_MODE) ?: MODE_PERSISTENT
        startInForeground("Проверяю позиции…")

        // Будильник ставится всегда и заранее: если система снимет сервис
        // раньше следующего прогона, поднять контур будет уже нечем.
        MonitorAlarm.schedule(this, mode)

        if (engine == null) startEngine() else awake()
        // START_STICKY: система, снявшая сервис из-за нехватки памяти, вернёт
        // его сама. Для burst-режима это безвредно — прогон завершится и
        // сервис остановится снова.
        return START_STICKY
    }

    private fun startInForeground(text: String) {
        val notification = Notifications.ongoing(this, text)
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC,
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    /**
     * Не давать уснуть на время прогона — и ровно на это время.
     *
     * Раньше блокировка бралась один раз на десять минут при старте движка и
     * снималась только вместе с сервисом. В постоянном режиме это значило
     * десять минут запрещённого сна на каждый подъём контура, независимо от
     * того, что прогон занимает секунды. Таймаут остаётся страховкой на
     * случай, если Dart не отзовётся вовсе.
     */
    private fun awake() {
        val lock = wakeLock ?: (getSystemService(Context.POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "SignalAI:monitor")
            .also { it.setReferenceCounted(false); wakeLock = it }
        if (!lock.isHeld) lock.acquire(5 * 60 * 1000L)
    }

    private fun asleep() {
        wakeLock?.let { if (it.isHeld) it.release() }
    }

    private fun startEngine() {
        awake()

        // Загрузчик инициализируется явно: сервис может стартовать в свежем
        // процессе, где ни одна активити ещё не запускалась, и без этого путь
        // к бандлу оказался бы невалидным, а движок упал бы на старте.
        val loader = FlutterInjector.instance().flutterLoader()
        loader.startInitialization(applicationContext)
        loader.ensureInitializationComplete(applicationContext, null)

        val flutterEngine = FlutterEngine(applicationContext)
        engine = flutterEngine

        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CHANNEL)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    // Режим сообщается Dart'у: он решает, спать до следующего
                    // прогона или завершиться.
                    "monitorMode" -> result.success(mode)

                    // Прогон начался — держим процессор до его конца.
                    "monitorWake" -> {
                        awake()
                        result.success(true)
                    }

                    // Прогон закончен, впереди сон: блокировку отпускаем
                    // немедленно, а не ждём её таймаута.
                    "monitorSleep" -> {
                        asleep()
                        val minutes = call.argument<Int>("minutes") ?: 60
                        // Будильник переставляется под фактический интервал:
                        // страховка обязана будить тогда же, когда собирался
                        // проснуться сам контур, а не через фиксированный час.
                        MonitorAlarm.schedule(this, mode, minutes)
                        result.success(true)
                    }

                    // Итог прогона — в служебную строку.
                    "monitorReport" -> {
                        updateNotification(call.argument<String>("summary") ?: "")
                        result.success(true)
                    }

                    // Прогон закончен: в burst-режиме больше делать нечего.
                    "monitorFinished" -> {
                        result.success(true)
                        if (mode == MODE_BURST) stopSelf()
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
        running = true
    }

    private fun updateNotification(summary: String) {
        if (summary.isEmpty()) return
        val manager = getSystemService(NOTIFICATION_SERVICE) as android.app.NotificationManager
        manager.notify(NOTIFICATION_ID, Notifications.ongoing(this, summary))
    }

    override fun onDestroy() {
        running = false
        engine?.destroy()
        engine = null
        asleep()
        wakeLock = null
        super.onDestroy()
    }
}
