package ru.signalai.app

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build

/**
 * Time-critical server polling для personal thin-client.
 *
 * Это fallback до отдельного FCM credential. На Android 12+ именно exact
 * alarm даёт фоновой доставке системное исключение для запуска bounded
 * foreground-service. Если special access не выдан, остаётся degraded
 * inexact fallback и пользователь получает диагностическое уведомление.
 */
object MonitorAlarm {

    const val EXTRA_MODE = "mode"

    private const val REQUEST = 4242
    const val DEFAULT_MINUTES = 5
    private const val DEGRADED_MINUTES = 15
    private const val PREFS = "signalai.monitor"

    fun exactAllowed(context: Context): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return true
        val manager = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        return manager.canScheduleExactAlarms()
    }

    /**
     * В нормальном personal-режиме проверяем server outbox каждые ~5 минут.
     * В Doze Android всё ещё может раздвигать exact-and-idle alarms, но exact
     * PendingIntent разрешено поднимать foreground-service из background.
     */
    fun schedule(context: Context, mode: String, minutes: Int = DEFAULT_MINUTES) {
        val manager = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val exact = exactAllowed(context)
        val delay = if (exact) minutes.coerceIn(5, 720) else maxOf(minutes, DEGRADED_MINUTES)
        val at = System.currentTimeMillis() + delay * 60_000L
        val intent = pending(context, mode)

        when {
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.M && exact ->
                manager.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, intent)
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.M ->
                manager.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, intent)
            else -> manager.setExact(AlarmManager.RTC_WAKEUP, at, intent)
        }
    }

    fun cancel(context: Context) {
        val manager = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        manager.cancel(pending(context, MonitorService.MODE_PERSISTENT))
    }

    fun remember(context: Context, mode: String?) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        if (mode == null) prefs.edit().remove("mode").apply()
        else prefs.edit().putString("mode", mode).apply()
    }

    fun remembered(context: Context): String? =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString("mode", null)

    private fun pending(context: Context, mode: String): PendingIntent = PendingIntent.getBroadcast(
        context,
        REQUEST,
        Intent(context, MonitorAlarmReceiver::class.java).putExtra(EXTRA_MODE, mode),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )
}

class MonitorAlarmReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val mode = intent.getStringExtra(MonitorAlarm.EXTRA_MODE)
            ?: MonitorService.MODE_PERSISTENT
        if (MonitorService.running) {
            MonitorAlarm.schedule(context, mode)
            return
        }
        try {
            MonitorService.start(context, mode)
        } catch (error: IllegalStateException) {
            // Typical Android 12+ failure for a background FGS started from an
            // inexact alarm. Do not silently pretend monitoring is healthy.
            Notifications.post(
                context,
                id = 9042,
                title = "SignalAI: фоновый мониторинг ограничен",
                body = "Откройте SignalAI и разрешите «Будильники и напоминания», " +
                    "чтобы сигналы приходили при закрытом приложении.",
            )
            MonitorAlarm.schedule(context, mode)
        } catch (error: SecurityException) {
            Notifications.post(
                context,
                id = 9042,
                title = "SignalAI: нужен системный доступ",
                body = "Откройте SignalAI и включите точные будильники для фоновых сигналов.",
            )
            MonitorAlarm.schedule(context, mode)
        }
    }
}

class MonitorBootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        val mode = MonitorAlarm.remembered(context) ?: return
        MonitorAlarm.schedule(context, mode)
    }
}
