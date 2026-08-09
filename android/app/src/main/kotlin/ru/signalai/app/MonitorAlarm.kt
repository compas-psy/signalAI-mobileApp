package ru.signalai.app

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build

/**
 * Будильник server polling фонового thin-клиента.
 *
 * Это не замена remote push, а страховочный pull-канал персонального sideload:
 * пока FCM не настроен отдельным credential, телефон сам забирает server
 * outbox. Пять минут соответствуют часовому trigger-TF и дают приемлемую
 * задержку без постоянного foreground-service.
 */
object MonitorAlarm {

    const val EXTRA_MODE = "mode"

    private const val REQUEST = 4242
    const val DEFAULT_MINUTES = 5
    private const val PREFS = "signalai.monitor"

    /** [minutes] — через сколько будить; production не ставит чаще 5 минут. */
    fun schedule(context: Context, mode: String, minutes: Int = DEFAULT_MINUTES) {
        val manager = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val at = System.currentTimeMillis() + minutes.coerceIn(5, 720) * 60_000L
        val intent = pending(context, mode)
        if (Build.VERSION.SDK_INT >= 23) {
            manager.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, intent)
        } else {
            manager.set(AlarmManager.RTC_WAKEUP, at, intent)
        }
    }

    fun cancel(context: Context) {
        val manager = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        manager.cancel(pending(context, MonitorService.MODE_PERSISTENT))
    }

    /** Запоминает режим, чтобы приёмник загрузки знал, что восстанавливать. */
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

/** Приёмник будильника: поднимает контур, если тот не работает. */
class MonitorAlarmReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val mode = intent.getStringExtra(MonitorAlarm.EXTRA_MODE)
            ?: MonitorService.MODE_PERSISTENT
        if (MonitorService.running) {
            MonitorAlarm.schedule(context, mode)
            return
        }
        MonitorService.start(context, mode)
    }
}

/** Восстановление контура после перезагрузки. */
class MonitorBootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        val mode = MonitorAlarm.remembered(context) ?: return
        MonitorAlarm.schedule(context, mode)
    }
}
