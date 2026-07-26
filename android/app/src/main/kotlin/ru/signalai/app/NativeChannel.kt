package ru.signalai.app

import android.content.Context
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

    /** true — метод обработан. */
    fun handle(call: MethodCall, result: MethodChannel.Result): Boolean {
        when (call.method) {
            "filesDir" -> result.success(context.filesDir.absolutePath)

            "notify" -> result.success(
                Notifications.post(
                    context,
                    call.argument<Int>("id") ?: 1,
                    call.argument<String>("title") ?: "SignalAI",
                    call.argument<String>("body") ?: "",
                ),
            )

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
