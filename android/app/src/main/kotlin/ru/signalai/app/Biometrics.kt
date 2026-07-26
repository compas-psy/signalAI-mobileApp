package ru.signalai.app

import android.app.Activity
import android.app.KeyguardManager
import android.content.Context
import android.hardware.biometrics.BiometricPrompt
import android.os.Build
import android.os.CancellationSignal

/**
 * Подтверждение сделки отпечатком или лицом.
 *
 * Используется системный [BiometricPrompt] (Android 9+), а не androidx: в
 * приложении, которое отправляет ордера, чем меньше стороннего кода, тем лучше.
 * Ниже Android 9 биометрия не спрашивается — там подтверждение падает на
 * блокировку устройства, а если и её нет, торговля просто не разрешается.
 */
class Biometrics(private val activity: Activity) {

    /** Есть ли на устройстве чем подтверждать. */
    fun isAvailable(): Boolean = method() != NONE

    /**
     * Чем именно можно подтвердить сделку прямо сейчас.
     *
     * Различать способы важно: «недоступно» без причины владельцу ничего не
     * говорит, а «нет блокировки экрана» — прямое указание, что включить.
     * Отсутствие сервиса биометрии не означает отказ: ПИН и графический ключ
     * подтверждают сделку не хуже отпечатка, и раньше этот случай терялся.
     */
    @Suppress("DEPRECATION")
    fun method(): String {
        // Ниже Android 9 системного диалога нет вовсе — подтверждать нечем,
        // и [confirm] это честно повторяет отказом.
        if (Build.VERSION.SDK_INT < 28) return NONE
        val secure = keyguard()?.isDeviceSecure == true
        val manager = activity.getSystemService(Context.BIOMETRIC_SERVICE)
            as? android.hardware.biometrics.BiometricManager
        if (manager != null && Build.VERSION.SDK_INT >= 30) {
            val strong = manager.canAuthenticate(
                android.hardware.biometrics.BiometricManager.Authenticators.BIOMETRIC_STRONG,
            )
            if (strong == android.hardware.biometrics.BiometricManager.BIOMETRIC_SUCCESS) {
                return BIOMETRICS
            }
            val credential = manager.canAuthenticate(
                android.hardware.biometrics.BiometricManager.Authenticators.DEVICE_CREDENTIAL,
            )
            if (credential == android.hardware.biometrics.BiometricManager.BIOMETRIC_SUCCESS) {
                return CREDENTIAL
            }
            return if (secure) CREDENTIAL else NONE
        }
        if (manager != null && Build.VERSION.SDK_INT >= 29) {
            if (manager.canAuthenticate() ==
                android.hardware.biometrics.BiometricManager.BIOMETRIC_SUCCESS
            ) {
                return BIOMETRICS
            }
        }
        return if (secure) CREDENTIAL else NONE
    }

    /**
     * Показывает диалог подтверждения. [onResult] — true, если пользователь
     * подтвердил. Отказ, отмена и ошибка одинаково означают «не подтверждено»:
     * при сомнении ордер не уходит.
     */
    fun confirm(title: String, subtitle: String, onResult: (Boolean) -> Unit) {
        if (Build.VERSION.SDK_INT < 28) {
            onResult(false)
            return
        }
        var answered = false
        fun answer(value: Boolean) {
            if (answered) return
            answered = true
            activity.runOnUiThread { onResult(value) }
        }

        val builder = BiometricPrompt.Builder(activity)
            .setTitle(title)
            .setSubtitle(subtitle)
        if (Build.VERSION.SDK_INT >= 30) {
            // Разрешаем ПИН/графический ключ как запасной способ: иначе на
            // устройстве без настроенной биометрии подтвердить сделку нечем.
            builder.setAllowedAuthenticators(
                android.hardware.biometrics.BiometricManager.Authenticators.BIOMETRIC_STRONG or
                    android.hardware.biometrics.BiometricManager.Authenticators.DEVICE_CREDENTIAL,
            )
        } else {
            @Suppress("DEPRECATION")
            builder.setDeviceCredentialAllowed(true)
        }

        builder.build().authenticate(
            CancellationSignal(),
            activity.mainExecutor,
            object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(
                    result: BiometricPrompt.AuthenticationResult?,
                ) = answer(true)

                override fun onAuthenticationError(code: Int, message: CharSequence?) =
                    answer(false)

                override fun onAuthenticationFailed() {
                    // Неудачная попытка — не отказ: диалог остаётся открытым,
                    // окончательный ответ придёт в onAuthenticationError.
                }
            },
        )
    }

    private fun keyguard(): KeyguardManager? =
        activity.getSystemService(Context.KEYGUARD_SERVICE) as? KeyguardManager

    companion object {
        /** Отпечаток или лицо. */
        const val BIOMETRICS = "biometrics"

        /** ПИН, пароль или графический ключ устройства. */
        const val CREDENTIAL = "credential"

        /** Подтверждать нечем: блокировка экрана не настроена. */
        const val NONE = "none"
    }
}
