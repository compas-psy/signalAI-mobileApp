package ru.signalai.app

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build

/**
 * Уведомления приложения.
 *
 * Каналов два, и это принципиально: событие по сделке обязано звенеть, а
 * служебная строка «фон работает» — нет. Один канал на оба назначения означал
 * бы либо молчащие сигналы, либо звенящую каждый час служебку.
 */
object Notifications {

    /** События: новые сигналы, срабатывание стопа и тейка. */
    const val SIGNALS = "signals"

    /** Служебная строка фонового контура. Без звука и без вибрации. */
    const val MONITOR = "monitor"

    fun hasPermission(context: Context): Boolean =
        Build.VERSION.SDK_INT < 33 ||
            context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) ==
                PackageManager.PERMISSION_GRANTED

    /** Ключ, под которым уведомление несёт адрес открываемого экрана. */
    const val PAYLOAD = "ru.signalai.app.payload"

    /**
     * Показывает уведомление о событии. false — разрешения нет.
     *
     * [payload] — что открыть по нажатию: идентификатор идеи. Пустая строка
     * значит «просто открыть приложение». Уведомление без адреса приводит
     * владельца на тот экран, где он был вчера, и заставляет искать идею
     * руками — то есть делает пуш бесполезным ровно в тот момент, ради
     * которого он и посылался.
     */
    fun post(
        context: Context,
        id: Int,
        title: String,
        body: String,
        payload: String = "",
    ): Boolean {
        if (!hasPermission(context)) return false
        manager(context).notify(
            id,
            build(context, SIGNALS, title, body, payload, id).build(),
        )
        return true
    }

    /** Уведомление, с которым живёт foreground-сервис. */
    fun ongoing(context: Context, text: String): Notification =
        build(context, MONITOR, "SignalAI следит за рынком", text)
            .setOngoing(true)
            .build()

    private fun build(
        context: Context,
        channelId: String,
        title: String,
        body: String,
        payload: String = "",
        requestCode: Int = 0,
    ): Notification.Builder {
        val builder = if (Build.VERSION.SDK_INT >= 26) {
            ensureChannel(context, channelId)
            Notification.Builder(context, channelId)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(context).setPriority(
                if (channelId == SIGNALS) Notification.PRIORITY_HIGH else Notification.PRIORITY_MIN,
            )
        }

        val intent = Intent(context, MainActivity::class.java)
            // SINGLE_TOP: приложение уже открыто — оно обязано получить адрес
            // через onNewIntent, а не подняться второй копией поверх себя.
            .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_NEW_TASK)
            .putExtra(PAYLOAD, payload)
        // Код запроса свой у каждого уведомления. С общим нулём система
        // считает намерения одинаковыми и переиспользует первое: все пуши
        // открывали бы идею, о которой пришёл самый первый из них.
        val pending = PendingIntent.getActivity(
            context, requestCode, intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return builder
            .setSmallIcon(context.applicationInfo.icon)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(Notification.BigTextStyle().bigText(body))
            .setAutoCancel(channelId == SIGNALS)
            .setContentIntent(pending)
    }

    private fun ensureChannel(context: Context, channelId: String) {
        if (Build.VERSION.SDK_INT < 26) return
        val channel = when (channelId) {
            MONITOR -> NotificationChannel(
                MONITOR,
                "Фоновая работа",
                NotificationManager.IMPORTANCE_MIN,
            ).apply { description = "Служебная строка: контур следит за позициями" }

            else -> NotificationChannel(
                SIGNALS,
                "Сигналы",
                NotificationManager.IMPORTANCE_HIGH,
            ).apply { description = "Новые идеи и события по открытым сделкам" }
        }
        manager(context).createNotificationChannel(channel)
    }

    private fun manager(context: Context): NotificationManager =
        context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
}
