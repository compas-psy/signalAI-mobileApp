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

    private fun powerState(): Map<String, Any> {
        val status = context.registerReceiver(
            null, IntentFilter(Intent.ACTION_BATTERY_CHANGED),
        )
        val level = status?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
        val scale = status?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1
        val plugged = status?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0
        val power = context.getSystemService(Context.POWER_SERVICE) as PowerManager
        return mapOf(
            "percent" to if (level >= 0 && scale > 0) level * 100 / scale else -1,
            "charging" to (plugged != 0),
            "saver" to (Build.VERSION.SDK_INT >= 21 && power.isPowerSaveMode),
        )
    }

    /**
     * Только значения, которые по протоколу обязаны попасть в Dart, можно
     * читать из Keystore через MethodChannel. Биржевой HMAC-secret всегда
     * хранится как `*.secret` и используется только через vaultSign.
     */
    private fun readableVaultName(name: String): Boolean =
        name.isNotBlank() && !name.endsWith(".secret")

    /** true — метод обработан. */
    fun handle(call: MethodCall, result: MethodChannel.Result): Boolean {
        when (call.method) {
            "filesDir" -> result.success(context.filesDir.absolutePath)

            "appVersion" -> {
                val info = context.packageManager.getPackageInfo(context.packageName, 0)
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

            "cancelNotification" -> result.success(
                Notifications.cancel(context, call.argument<Int>("id") ?: 0),
            )

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

            "vaultGet" -> {
                val name = call.argument<String>("name") ?: ""
                if (!readableVaultName(name)) {
                    result.error("forbidden", "значение этого секрета не экспортируется", null)
                } else {
                    result.success(vault.get(name))
                }
            }

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
