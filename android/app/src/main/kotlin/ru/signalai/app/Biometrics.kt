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
        // Любой сбой биометрического сервиса деградирует к isDeviceSecure:
        // проверка ПИНа разрешений не требует и не падает. Ровно этот случай
        // уже случился в проде: без USE_BIOMETRIC в манифесте canAuthenticate
        // бросал SecurityException — и приложение говорило «нечем» владельцу
        // с включёнными ПИНом и отпечатком.
        try {
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
        } catch (_: Throwable) {
            // Падение сервиса — не «нечем»: ПИН отвечает за себя сам.
        }
        return if (secure) CREDENTIAL else NONE
    }

    /// Сырые факты детекции — для диагностики, чтобы спор «включено или нет»
    /// решался данными устройства, а не пересказом.
    @Suppress("DEPRECATION")
    fun methodDetails(): String {
        val sdk = Build.VERSION.SDK_INT
        val secure = try {
            keyguard()?.isDeviceSecure
        } catch (e: Throwable) {
            "ошибка: $e"
        }
        val auth = try {
            val manager = activity.getSystemService(Context.BIOMETRIC_SERVICE)
                as? android.hardware.biometrics.BiometricManager
            when {
                manager == null -> "сервис биометрии недоступен"
                sdk >= 30 -> {
                    val strong = manager.canAuthenticate(
                        android.hardware.biometrics.BiometricManager.Authenticators.BIOMETRIC_STRONG,
                    )
                    val credential = manager.canAuthenticate(
                        android.hardware.biometrics.BiometricManager.Authenticators.DEVICE_CREDENTIAL,
                    )
                    "canAuthenticate: strong=$strong, credential=$credential (0 = доступно)"
                }
                else -> "canAuthenticate=${manager.canAuthenticate()} (0 = доступно)"
            }
        } catch (e: Throwable) {
            "canAuthenticate бросил: $e"
        }
        return "SDK $sdk · экран защищён: $secure · $auth"
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

        try {
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
        } catch (_: Throwable) {
            // Диалог не открылся — подтверждения не было. При сомнении ордер
            // не уходит; причина видна в methodDetails диагностики.
            answer(false)
        }
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
