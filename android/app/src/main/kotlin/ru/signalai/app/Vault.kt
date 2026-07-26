package ru.signalai.app

import android.content.Context
import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.Mac
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/**
 * Хранилище торговых секретов поверх Android Keystore.
 *
 * Ключ шифрования создаётся внутри Keystore и оттуда не извлекается: сами
 * секреты лежат на диске только в зашифрованном виде, а расшифровать их может
 * лишь это приложение на этом устройстве.
 *
 * Главное решение здесь — [signHmac]. Секрет биржи никогда не возвращается в
 * Dart: подпись запроса считается тут же, нативно, и наружу уходит только hex
 * подписи. Значит, секрет не появляется ни в куче Dart, ни в логах, ни в
 * дампе памяти интерпретатора — его нельзя случайно напечатать.
 */
class Vault(context: Context) {

    private val prefs = context.getSharedPreferences("signalai.vault", Context.MODE_PRIVATE)

    private companion object {
        const val KEY_ALIAS = "signalai.vault.v1"
        const val GCM_TAG_BITS = 128
        const val IV_BYTES = 12
    }

    /** Доступен ли Keystore на этом устройстве. */
    fun isAvailable(): Boolean = Build.VERSION.SDK_INT >= 23 && runCatching { secretKey() }.isSuccess

    fun has(name: String): Boolean = prefs.contains(name)

    fun names(): List<String> = prefs.all.keys.sorted()

    /** Кладёт секрет. Пустое значение равносильно удалению. */
    fun put(name: String, value: String) {
        if (value.isEmpty()) {
            delete(name)
            return
        }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, secretKey())
        val encrypted = cipher.doFinal(value.toByteArray(Charsets.UTF_8))
        // IV хранится рядом с шифротекстом: он не секрет, но обязан быть
        // уникальным на каждое шифрование, иначе GCM теряет стойкость.
        val blob = cipher.iv + encrypted
        prefs.edit().putString(name, Base64.encodeToString(blob, Base64.NO_WRAP)).apply()
    }

    fun delete(name: String) = prefs.edit().remove(name).apply()

    fun clear() = prefs.edit().clear().apply()

    /**
     * Читает секрет. Применяется только к некритичным значениям вроде
     * идентификатора ключа API — сам секрет наружу не отдаётся, см. [signHmac].
     */
    fun get(name: String): String? {
        val stored = prefs.getString(name, null) ?: return null
        return runCatching {
            val blob = Base64.decode(stored, Base64.NO_WRAP)
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(
                Cipher.DECRYPT_MODE,
                secretKey(),
                GCMParameterSpec(GCM_TAG_BITS, blob, 0, IV_BYTES),
            )
            String(cipher.doFinal(blob, IV_BYTES, blob.size - IV_BYTES), Charsets.UTF_8)
        }.getOrNull()
    }

    /**
     * HMAC-SHA256 сообщения [payload] секретом [name], hex в нижнем регистре.
     *
     * null означает «секрета нет» — подпись без ключа не изобретается.
     */
    fun signHmac(name: String, payload: String): String? {
        val secret = get(name) ?: return null
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(secret.toByteArray(Charsets.UTF_8), "HmacSHA256"))
        return mac.doFinal(payload.toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }
    }

    private fun secretKey(): SecretKey {
        val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (keyStore.getEntry(KEY_ALIAS, null) as? KeyStore.SecretKeyEntry)?.let { return it.secretKey }

        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(
            KeyGenParameterSpec.Builder(
                KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                // Аутентификация пользователя на самом ключе не требуется
                // намеренно: подтверждение сделки берётся биометрией в момент
                // отправки ордера, а сопровождение уже открытой позиции должно
                // работать и с погашенным экраном. Ключ всё равно не покидает
                // устройство и привязан к подписи приложения.
                .setUserAuthenticationRequired(false)
                .build(),
        )
        return generator.generateKey()
    }
}
