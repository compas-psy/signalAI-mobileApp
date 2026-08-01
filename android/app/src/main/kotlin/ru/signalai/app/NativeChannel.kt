package ru.signalai.app

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.Build
import android.os.PowerManager
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel

/**
 * Методы канала, одинаковые для интерфейса и фонового контура: путь к файлам,
 * уведомления, хранилище секретов.
 *
 * Общий обработчик, а не две копии, потому что фон и интерфейс обязаны читать
 * одни и те же файлы и подписывать запросы одним и тем же ключом. Разъехавшиеся
 * реализации здесь означали бы разъехавшееся состояние.
 *
 * Чего тут нет: биометрии. Она требует активити и живого человека — в фоне не
 * бывает ни того, ни другого, и подтверждать сделку там нечем.
 */
class NativeChannel(private val context: Context) {

    private val vault by lazy { Vault(context) }

    /**
     * Заряд, питание и режим энергосбережения.
     *
     * Читается через sticky-broadcast: подписка на изменения здесь не нужна
     * и вредна — контур спрашивает раз в час, а живой приёмник будил бы
     * процесс на каждый процент заряда.
     */
    private fun powerState(): Map<String, Any> {
        val status = context.registerReceiver(
            null, IntentFilter(Intent.ACTION_BATTERY_CHANGED),
        )
        val level = status?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
        val scale = status?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1
        val plugged = status?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0
        val power = context.getSystemService(Context.POWER_SERVICE) as PowerManager
        return mapOf(
            // −1 значит «неизвестно»: подставлять сюда сотню значит решить,
            // что заряд полный, и разрядить телефон на этом допущении.
            "percent" to if (level >= 0 && scale > 0) level * 100 / scale else -1,
            "charging" to (plugged != 0),
            "saver" to (Build.VERSION.SDK_INT >= 21 && power.isPowerSaveMode),
        )
    }

    /** true — метод обработан. */
    fun handle(call: MethodCall, result: MethodChannel.Result): Boolean {
        when (call.method) {
            "filesDir" -> result.success(context.filesDir.absolutePath)

            // Версия берётся у системы, а не хардкодится в интерфейсе: в
            // карточке «О приложении» должно стоять то, что реально стоит на
            // устройстве, иначе она бесполезна при разборе «что за сборка».
            "appVersion" -> {
                val info = context.packageManager.getPackageInfo(context.packageName, 0)
                // versionCode, а не longVersionCode: последний появился в API 28,
                // а минимальная версия приложения — 24.
                @Suppress("DEPRECATION")
                val code = info.versionCode
                result.success("${info.versionName} ($code)")
            }

            "notify" -> result.success(
                Notifications.post(
                    context,
                    call.argument<Int>("id") ?: 1,
                    call.argument<String>("title") ?: "SignalAI",
                    call.argument<String>("body") ?: "",
                    call.argument<String>("payload") ?: "",
                ),
            )

            // Снятие уведомления. Отдельная команда, а не побочный эффект
            // показа: отработавшая идея должна исчезать из шторки, даже
            // если нового уведомления вместо неё не будет.
            "cancelNotification" -> result.success(
                Notifications.cancel(context, call.argument<Int>("id") ?: 0),
            )

            // Состояние питания. Фоновый контур решает по нему, как часто
            // просыпаться: круглосуточный часовой пересчёт на телефоне с
            // 15% заряда — это не «постоянное наблюдение», а разряженный к
            // вечеру телефон.
            "powerState" -> result.success(powerState())

            "vaultAvailable" -> result.success(vault.isAvailable())

            "vaultPut" -> {
                val name = call.argument<String>("name")
                val value = call.argument<String>("value")
                if (name == null || value == null) {
                    result.error("args", "нужны name и value", null)
                } else {
                    vault.put(name, value)
                    result.success(true)
                }
            }

            "vaultHas" -> result.success(vault.has(call.argument<String>("name") ?: ""))

            "vaultGet" -> result.success(vault.get(call.argument<String>("name") ?: ""))

            "vaultDelete" -> {
                vault.delete(call.argument<String>("name") ?: "")
                result.success(true)
            }

            "vaultClear" -> {
                vault.clear()
                result.success(true)
            }

            "vaultSign" -> result.success(
                vault.signHmac(
                    call.argument<String>("name") ?: "",
                    call.argument<String>("payload") ?: "",
                ),
            )

            else -> {
                result.notImplemented()
                return false
            }
        }
        return true
    }
}
