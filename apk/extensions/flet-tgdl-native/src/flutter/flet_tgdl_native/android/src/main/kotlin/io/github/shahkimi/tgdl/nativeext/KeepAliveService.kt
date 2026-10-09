package io.github.shahkimi.tgdl.nativeext

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.os.IBinder
import android.os.PowerManager

/**
 * Foreground service that keeps the process (and so the Python downloads) alive while the screen is off.
 * It does no work itself. It shows a progress notification and holds a CPU wake lock and a Wi-Fi lock.
 */
class KeepAliveService : Service() {
    private var wakeLock: PowerManager.WakeLock? = null
    private var wifiLock: WifiManager.WifiLock? = null
    private var title = "TG Downloader"
    private var text = "Downloading..."
    private var progress = -1

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        instance = this
        createChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            shutDown()
            return START_NOT_STICKY
        }
        title = intent?.getStringExtra(EXTRA_TITLE) ?: title
        text = intent?.getStringExtra(EXTRA_TEXT) ?: text
        progress = -1
        goForeground()
        acquireLocks()
        return START_NOT_STICKY // after a crash there is nothing left to keep alive, so do not restart
    }

    /** Android 15 limits dataSync services to about 6 hours a day; stop cleanly instead of being killed. */
    override fun onTimeout(startId: Int, fgsType: Int) {
        shutDown()
    }

    override fun onDestroy() {
        releaseLocks()
        instance = null
        super.onDestroy()
    }

    // ---- notification ----------------------------------------------------------------------------
    private fun createChannel() {
        if (Build.VERSION.SDK_INT < 26) return
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (manager.getNotificationChannel(CHANNEL_ID) == null) {
            val channel = NotificationChannel(CHANNEL_ID, "Downloads", NotificationManager.IMPORTANCE_LOW)
            channel.description = "Shows progress while downloads run in the background"
            channel.setShowBadge(false)
            manager.createNotificationChannel(channel)
        }
    }

    private fun buildNotification(): Notification {
        val builder = if (Build.VERSION.SDK_INT >= 26) Notification.Builder(this, CHANNEL_ID) else Notification.Builder(this)
        builder
            .setContentTitle(title)
            .setContentText(text)
            .setSmallIcon(android.R.drawable.stat_sys_download)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
        if (Build.VERSION.SDK_INT < 26) builder.setPriority(Notification.PRIORITY_LOW)
        if (progress in 0..100) builder.setProgress(100, progress, false) else builder.setProgress(0, 0, true)

        val launch = packageManager.getLaunchIntentForPackage(packageName)
        if (launch != null) {
            val pending = PendingIntent.getActivity(this, 0, launch, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            builder.setContentIntent(pending)
        }
        return builder.build()
    }

    private fun goForeground() {
        val notification = buildNotification()
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun update(newText: String, newProgress: Int) {
        text = newText
        progress = newProgress
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.notify(NOTIFICATION_ID, buildNotification())
    }

    // ---- locks ---------------------------------------------------------------------------------------
    private fun acquireLocks() {
        try {
            if (wakeLock == null) {
                val power = getSystemService(Context.POWER_SERVICE) as PowerManager
                wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "tgdl:downloads").apply {
                    setReferenceCounted(false)
                    acquire(MAX_HOLD_MS)
                }
            }
            if (wifiLock == null) {
                val wifi = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
                val mode = if (Build.VERSION.SDK_INT >= 29) WifiManager.WIFI_MODE_FULL_LOW_LATENCY else WifiManager.WIFI_MODE_FULL_HIGH_PERF
                wifiLock = wifi.createWifiLock(mode, "tgdl:downloads").apply {
                    setReferenceCounted(false)
                    acquire()
                }
            }
        } catch (_: Exception) {
            // A missing lock only makes downloads less reliable with the screen off; it must not stop the service.
        }
    }

    private fun releaseLocks() {
        try {
            wakeLock?.let { if (it.isHeld) it.release() }
            wifiLock?.let { if (it.isHeld) it.release() }
        } catch (_: Exception) {
        }
        wakeLock = null
        wifiLock = null
    }

    private fun shutDown() {
        releaseLocks()
        if (Build.VERSION.SDK_INT >= 24) stopForeground(STOP_FOREGROUND_REMOVE) else stopForeground(true)
        stopSelf()
    }

    companion object {
        private const val CHANNEL_ID = "tgdl_downloads"
        private const val NOTIFICATION_ID = 4711
        private const val MAX_HOLD_MS = 6L * 60 * 60 * 1000

        const val ACTION_START = "io.github.shahkimi.tgdl.KEEP_ALIVE_START"
        const val ACTION_STOP = "io.github.shahkimi.tgdl.KEEP_ALIVE_STOP"
        const val EXTRA_TITLE = "title"
        const val EXTRA_TEXT = "text"

        @Volatile
        private var instance: KeepAliveService? = null

        fun start(context: Context, title: String, text: String) {
            val intent = Intent(context, KeepAliveService::class.java)
                .setAction(ACTION_START)
                .putExtra(EXTRA_TITLE, title)
                .putExtra(EXTRA_TEXT, text)
            if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(intent) else context.startService(intent)
        }

        /** Safe to call at any time; does nothing when the service is not running. */
        fun update(text: String, progress: Int) {
            instance?.update(text, progress)
        }

        fun stop(context: Context) {
            if (instance == null) return
            context.startService(Intent(context, KeepAliveService::class.java).setAction(ACTION_STOP))
        }
    }
}
