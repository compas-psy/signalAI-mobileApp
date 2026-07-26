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
    fun isAvailable(): Boolean {
        if (Build.VERSION.SDK_INT < 29) {
            return keyguard()?.isDeviceSecure == true
        }
        val manager = activity.getSystemService(Context.BIOMETRIC_SERVICE)
            as? android.hardware.biometrics.BiometricManager ?: return false
        return manager.canAuthenticate() ==
            android.hardware.biometrics.BiometricManager.BIOMETRIC_SUCCESS ||
            keyguard()?.isDeviceSecure == true
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
}
