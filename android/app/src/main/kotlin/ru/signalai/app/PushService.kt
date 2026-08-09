package ru.signalai.app

import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.TimeZone
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import kotlin.math.min

/**
 * Persistent server → device notification channel.
 *
 * This is intentionally native. The previous implementation started a second
 * FlutterEngine and then tried to locate a Dart entrypoint by string. On the
 * owner's device the foreground service started, but the isolate never reached
 * even its first diagnostic callback: the notification stayed forever at
 * "Подключаю server push…" while the VPS already had an event in its outbox.
 *
 * Native SSE removes that fragile boot layer. The service reads the same engine
 * URL file and the same Android-Keystore bearer token as Flutter, keeps one
 * authenticated HTTPS event stream open, persists the server cursor, and posts
 * Android notifications directly. Exact-alarm polling remains an independent
 * reconciliation fallback.
 */
class PushService : Service() {

    companion object {
        private const val NOTIFICATION_ID = 43
        private const val DEVICE_TOKEN_KEY = "signalai.engine.device_token"
        private const val FALLBACK_API = "https://api.ilyamartynov.ru"
        private const val PREFS = "signalai.push.native"
        private const val CURSOR = "cursor"
        private const val FIRST_RUN_RECENT_SECONDS = 30 * 60L

        @Volatile
        var running: Boolean = false
            private set

        fun start(context: Context) {
            if (!Notifications.hasPermission(context) || running) return
            val intent = Intent(context, PushService::class.java)
            try {
                if (Build.VERSION.SDK_INT >= 26) {
                    context.startForegroundService(intent)
                } else {
                    context.startService(intent)
                }
            } catch (_: IllegalStateException) {
                // Exact-alarm polling remains the durable fallback.
            } catch (_: SecurityException) {
                // Missing FGS permission must not crash the trading client.
            }
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, PushService::class.java))
        }
    }

    private val worker = Executors.newSingleThreadExecutor()
    private val prefs by lazy { getSharedPreferences(PREFS, Context.MODE_PRIVATE) }
    private val vault by lazy { Vault(applicationContext) }

    @Volatile
    private var stopping = false

    @Volatile
    private var activeConnection: HttpURLConnection? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        running = true
        startInForeground("Server push: подключение…")
        worker.execute { consumeForever() }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        running = true
        return START_STICKY
    }

    private fun startInForeground(text: String) {
        val notification = Notifications.ongoing(this, text)
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE,
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun updateStatus(text: String) {
        val manager = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        manager.notify(NOTIFICATION_ID, Notifications.ongoing(this, text))
    }

    private fun engineBaseUrl(): String {
        val file = File(filesDir, "signalai/engine.json")
        val saved = runCatching {
            if (!file.exists()) "" else JSONObject(file.readText()).optString("base_url", "")
        }.getOrDefault("").trim()
        return if (saved.startsWith("https://")) saved.trimEnd('/') else FALLBACK_API
    }

    private fun streamUrl(base: String, cursor: Long): URL =
        URL("${base.trimEnd('/')}/api/v1/notifications/stream?after=$cursor")

    private fun consumeForever() {
        var backoffSeconds = 2L
        while (!stopping) {
            val token = vault.get(DEVICE_TOKEN_KEY).orEmpty()
            if (token.isBlank()) {
                updateStatus("Server push: устройство не привязано")
                sleepInterruptibly(30)
                continue
            }

            try {
                consumeOnce(engineBaseUrl(), token)
                backoffSeconds = 2
            } catch (error: Exception) {
                if (stopping) break
                val short = (error.message ?: error.javaClass.simpleName)
                    .replace(Regex("\\s+"), " ")
                    .take(110)
                updateStatus("Server push: переподключение · $short")
                sleepInterruptibly(backoffSeconds)
                backoffSeconds = min(
                    60L,
                    when {
                        backoffSeconds < 5 -> 5L
                        backoffSeconds < 15 -> 15L
                        backoffSeconds < 30 -> 30L
                        else -> 60L
                    },
                )
            }
        }
    }

    private fun consumeOnce(base: String, token: String) {
        var cursor = prefs.getLong(CURSOR, 0L)
        val firstRun = !prefs.contains(CURSOR)
        val connection = (streamUrl(base, cursor).openConnection() as HttpURLConnection).apply {
            connectTimeout = 12_000
            readTimeout = 45_000
            requestMethod = "GET"
            setRequestProperty("Authorization", "Bearer $token")
            setRequestProperty("Accept", "text/event-stream")
            setRequestProperty("Cache-Control", "no-cache")
            setRequestProperty("Connection", "keep-alive")
        }
        activeConnection = connection

        try {
            connection.connect()
            if (connection.responseCode != HttpURLConnection.HTTP_OK) {
                val body = runCatching { connection.errorStream?.bufferedReader()?.readText() ?: "" }
                    .getOrDefault("")
                    .replace(Regex("\\s+"), " ")
                    .take(160)
                throw IllegalStateException("SSE ${connection.responseCode}: $body")
            }

            updateStatus("Server push: подключён")
            var eventId = 0L
            val data = StringBuilder()
            connection.inputStream.bufferedReader(Charsets.UTF_8).use { reader ->
                while (!stopping) {
                    val line = reader.readLine()
                        ?: throw IllegalStateException("server push stream closed")

                    if (line.isEmpty()) {
                        if (data.isNotEmpty()) {
                            val event = JSONObject(data.toString())
                            data.setLength(0)
                            val id = event.optLong("id", eventId)
                            if (id > cursor) {
                                val shouldNotify = !firstRun || isRecent(event)
                                if (shouldNotify) postEvent(event, id)
                                cursor = id
                                prefs.edit().putLong(CURSOR, cursor).apply()
                            }
                        }
                        eventId = 0L
                        continue
                    }

                    if (line.startsWith(":")) continue
                    when {
                        line.startsWith("id:") -> {
                            eventId = line.substring(3).trim().toLongOrNull() ?: eventId
                        }
                        line.startsWith("data:") -> {
                            if (data.isNotEmpty()) data.append('\n')
                            data.append(line.substring(5).trimStart())
                        }
                    }
                }
            }
        } finally {
            activeConnection = null
            connection.disconnect()
        }
    }

    private fun isRecent(event: JSONObject): Boolean {
        val raw = event.optString("created_at", "")
        if (raw.isBlank()) return true
        // Server emits ISO-8601 with microseconds. java.time is API 26+, while
        // SignalAI still supports older Android, so normalize to milliseconds
        // and parse with the platform date formatter instead of relying on
        // desugaring being enabled in a sideload build.
        val normalized = Regex("(\\.\\d{3})\\d+")
            .replace(raw) { match -> match.groupValues[1] }
            .replace("Z", "+00:00")
        val format = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSSXXX", Locale.US).apply {
            timeZone = TimeZone.getTimeZone("UTC")
        }
        val created = runCatching { format.parse(normalized)?.time }.getOrNull() ?: return true
        return System.currentTimeMillis() - created <= FIRST_RUN_RECENT_SECONDS * 1000
    }

    private fun postEvent(event: JSONObject, id: Long) {
        val key = event.optString("key", "server:$id")
        val title = event.optString("title", "SignalAI")
        val body = event.optString("body", "")
        val payload = event.optString("payload", "")
        Notifications.post(
            this,
            noticeId(key),
            title,
            body,
            payload,
        )
    }

    /** Same stable hash contract as NativeBridge.noticeId(). */
    private fun noticeId(key: String): Int {
        if (key.isEmpty()) return 500
        var hash = 7
        for (unit in key.toCharArray()) {
            hash = (hash * 31 + unit.code) and 0x7fffffff
        }
        return 1000 + hash % 9000
    }

    private fun sleepInterruptibly(seconds: Long) {
        var left = seconds
        while (!stopping && left > 0) {
            try {
                TimeUnit.SECONDS.sleep(1)
            } catch (_: InterruptedException) {
                return
            }
            left--
        }
    }

    override fun onDestroy() {
        stopping = true
        running = false
        activeConnection?.disconnect()
        activeConnection = null
        worker.shutdownNow()
        super.onDestroy()
    }
}
