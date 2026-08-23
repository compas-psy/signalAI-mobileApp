package ru.signalai.app

import android.app.Activity
import android.hardware.biometrics.BiometricManager
import android.hardware.biometrics.BiometricPrompt
import android.os.Build
import android.os.CancellationSignal
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.PrivateKey
import java.security.Signature
import java.security.spec.ECGenParameterSpec

/** Device-bound P-256 owner confirmation signer. */
class OwnerStepUpSigner(private val activity: Activity) {
    fun ensurePublicKeySpkiB64(): String? {
        if (Build.VERSION.SDK_INT < 30) return null
        return try {
            val store = keyStore()
            val existing = store.getCertificate(KEY_ALIAS)?.publicKey
            val publicKey = existing ?: generateKeyPair(true).let {
                keyStore().getCertificate(KEY_ALIAS)?.publicKey ?: it.public
            }
            Base64.encodeToString(publicKey.encoded, Base64.NO_WRAP)
        } catch (_: Throwable) {
            null
        }
    }

    fun signMessage(message: String, onResult: (String?) -> Unit) {
        if (Build.VERSION.SDK_INT < 30 || message.isEmpty()) {
            onResult(null)
            return
        }
        var answered = false
        fun answer(value: String?) {
            if (answered) return
            answered = true
            activity.runOnUiThread { onResult(value) }
        }
        try {
            if (ensurePublicKeySpkiB64() == null) {
                answer(null)
                return
            }
            val key = keyStore().getKey(KEY_ALIAS, null) as? PrivateKey
            if (key == null) {
                answer(null)
                return
            }
            val signature = Signature.getInstance(SIGNATURE_ALGORITHM).apply { initSign(key) }
            val prompt = BiometricPrompt.Builder(activity)
                .setTitle("Подтвердите действие владельца")
                .setSubtitle("SignalAI · защищённое подтверждение")
                .setAllowedAuthenticators(BiometricManager.Authenticators.BIOMETRIC_STRONG)
                .setNegativeButton("Отмена", activity.mainExecutor) { _, _ -> answer(null) }
                .build()
            prompt.authenticate(
                BiometricPrompt.CryptoObject(signature),
                CancellationSignal(),
                activity.mainExecutor,
                object : BiometricPrompt.AuthenticationCallback() {
                    override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult?) {
                        try {
                            val authenticated = result?.cryptoObject?.signature ?: signature
                            authenticated.update(message.toByteArray(Charsets.UTF_8))
                            answer(Base64.encodeToString(authenticated.sign(), Base64.NO_WRAP))
                        } catch (_: Throwable) {
                            answer(null)
                        }
                    }
                    override fun onAuthenticationError(errorCode: Int, errString: CharSequence?) = answer(null)
                    override fun onAuthenticationFailed() = Unit
                },
            )
        } catch (_: Throwable) {
            answer(null)
        }
    }

    fun deleteKey(): Boolean = try {
        keyStore().deleteEntry(KEY_ALIAS)
        true
    } catch (_: Throwable) {
        false
    }

    private fun keyStore(): KeyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }

    private fun generateKeyPair(strongBox: Boolean) = try {
        keyGenerator(strongBox).generateKeyPair()
    } catch (_: Throwable) {
        if (!strongBox) throw
        keyGenerator(false).generateKeyPair()
    }

    private fun keyGenerator(strongBox: Boolean): KeyPairGenerator {
        val generator = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore")
        val builder = KeyGenParameterSpec.Builder(KEY_ALIAS, KeyProperties.PURPOSE_SIGN)
            .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
            .setDigests(KeyProperties.DIGEST_SHA256)
            .setUserAuthenticationRequired(true)
            .setUserAuthenticationParameters(0, KeyProperties.AUTH_BIOMETRIC_STRONG)
            .setInvalidatedByBiometricEnrollment(true)
        if (strongBox && Build.VERSION.SDK_INT >= 28) builder.setIsStrongBoxBacked(true)
        generator.initialize(builder.build())
        return generator
    }

    companion object {
        private const val KEY_ALIAS = "signalai.owner.step_up.p256.v1"
        private const val SIGNATURE_ALGORITHM = "SHA256withECDSA"
    }
}
