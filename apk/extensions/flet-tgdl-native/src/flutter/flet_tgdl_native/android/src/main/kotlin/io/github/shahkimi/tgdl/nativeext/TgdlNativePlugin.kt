package io.github.shahkimi.tgdl.nativeext

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.MediaScannerConnection
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.provider.Settings
import io.flutter.embedding.engine.plugins.FlutterPlugin
import io.flutter.embedding.engine.plugins.activity.ActivityAware
import io.flutter.embedding.engine.plugins.activity.ActivityPluginBinding
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import io.flutter.plugin.common.PluginRegistry

/** Platform half of the Flet `TgdlNative` service. The channel name must match lib/src/tgdl_native.dart. */
class TgdlNativePlugin :
    FlutterPlugin,
    MethodChannel.MethodCallHandler,
    ActivityAware,
    PluginRegistry.RequestPermissionsResultListener {

    private lateinit var channel: MethodChannel
    private lateinit var context: Context
    private val main = Handler(Looper.getMainLooper())

    private var activity: Activity? = null
    private var binding: ActivityPluginBinding? = null
    private var pendingPermission: MethodChannel.Result? = null

    // ---- FlutterPlugin ------------------------------------------------------------------------------------
    override fun onAttachedToEngine(flutterPluginBinding: FlutterPlugin.FlutterPluginBinding) {
        context = flutterPluginBinding.applicationContext
        channel = MethodChannel(flutterPluginBinding.binaryMessenger, CHANNEL)
        channel.setMethodCallHandler(this)
        SharedInbox.listener = {
            main.post { channel.invokeMethod("shared", null) }
        }
    }

    override fun onDetachedFromEngine(binding: FlutterPlugin.FlutterPluginBinding) {
        SharedInbox.listener = null
        channel.setMethodCallHandler(null)
    }

    // ---- ActivityAware --------------------------------------------------------------------------------------
    override fun onAttachedToActivity(binding: ActivityPluginBinding) {
        activity = binding.activity
        this.binding = binding
        binding.addRequestPermissionsResultListener(this)
    }

    override fun onDetachedFromActivityForConfigChanges() = detach()

    override fun onReattachedToActivityForConfigChanges(binding: ActivityPluginBinding) = onAttachedToActivity(binding)

    override fun onDetachedFromActivity() = detach()

    private fun detach() {
        binding?.removeRequestPermissionsResultListener(this)
        binding = null
        activity = null
    }

    // ---- calls from Python ----------------------------------------------------------------------------------
    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        try {
            when (call.method) {
                "take_pending" -> result.success(SharedInbox.drain())

                "start_keep_alive" -> {
                    val title = call.argument<String>("title") ?: "TG Downloader"
                    val text = call.argument<String>("text") ?: ""
                    try {
                        KeepAliveService.start(context, title, text)
                        result.success(true)
                    } catch (e: Exception) {
                        // Android refuses foreground services started from the background; downloads still run while visible.
                        result.success(false)
                    }
                }
                "update_keep_alive" -> {
                    KeepAliveService.update(call.argument<String>("text") ?: "", call.argument<Int>("progress") ?: -1)
                    result.success(null)
                }
                "stop_keep_alive" -> {
                    KeepAliveService.stop(context)
                    result.success(null)
                }

                "public_downloads_dir" ->
                    result.success(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS).absolutePath)

                "scan_file" -> {
                    val path = call.argument<String>("path")
                    if (path != null) MediaScannerConnection.scanFile(context, arrayOf(path), null, null)
                    result.success(null)
                }
                // Re-index many files at once, e.g. after a .nomedia marker was added or removed. The scanner then hides
                // or shows them in galleries. Nothing is deleted.
                "scan_files" -> {
                    val paths = call.argument<List<String>>("paths") ?: emptyList()
                    if (paths.isNotEmpty()) MediaScannerConnection.scanFile(context, paths.toTypedArray(), null, null)
                    result.success(paths.size)
                }

                "has_all_files_access" -> result.success(hasAllFilesAccess())
                "request_all_files_access" -> result.success(requestAllFilesAccess())

                "sdk_int" -> result.success(Build.VERSION.SDK_INT)

                "request_permission" -> requestPermission(call.argument<String>("name"), result)

                "request_ignore_battery_optimizations" -> result.success(requestBatteryExemption())

                else -> result.notImplemented()
            }
        } catch (e: Exception) {
            result.error("tgdl_native", e.message ?: e.javaClass.simpleName, null)
        }
    }

    private fun requestPermission(name: String?, result: MethodChannel.Result) {
        if (name == null) {
            result.success(false)
            return
        }
        if (Build.VERSION.SDK_INT < 23 || context.checkSelfPermission(name) == PackageManager.PERMISSION_GRANTED) {
            result.success(true)
            return
        }
        val current = activity
        if (current == null || pendingPermission != null) {
            result.success(false)
            return
        }
        pendingPermission = result
        current.requestPermissions(arrayOf(name), PERMISSION_REQUEST_CODE)
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray): Boolean {
        if (requestCode != PERMISSION_REQUEST_CODE) return false
        val granted = grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED
        pendingPermission?.success(granted)
        pendingPermission = null
        return true
    }

    /** Android 11+: "All files access" (needed to save outside Download/). Older: the classic storage permission. */
    private fun hasAllFilesAccess(): Boolean =
        if (Build.VERSION.SDK_INT >= 30) Environment.isExternalStorageManager()
        else Build.VERSION.SDK_INT < 23 ||
            context.checkSelfPermission(android.Manifest.permission.WRITE_EXTERNAL_STORAGE) == PackageManager.PERMISSION_GRANTED

    /** "granted" when already allowed, "asked" when the settings screen was opened, "failed" otherwise. */
    private fun requestAllFilesAccess(): String {
        if (hasAllFilesAccess()) return "granted"
        if (Build.VERSION.SDK_INT < 30) return "failed"  // below 11 the runtime permission is asked with request_permission
        val starter: Context = activity ?: context
        val direct = Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION, Uri.parse("package:${context.packageName}"))
        val list = Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION)
        for (intent in listOf(direct, list)) {
            try {
                if (starter !is Activity) intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                starter.startActivity(intent)
                return "asked"
            } catch (_: Exception) {
            }
        }
        return "failed"
    }

    /** "granted" when already exempt, "asked" when the system dialog was opened, "failed" otherwise. */
    private fun requestBatteryExemption(): String {
        if (Build.VERSION.SDK_INT < 23) return "granted"
        val power = context.getSystemService(Context.POWER_SERVICE) as PowerManager
        if (power.isIgnoringBatteryOptimizations(context.packageName)) return "granted"
        val starter: Context = activity ?: context
        val direct = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:${context.packageName}"))
        val list = Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)
        for (intent in listOf(direct, list)) {
            try {
                if (starter !is Activity) intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                starter.startActivity(intent)
                return "asked"
            } catch (_: Exception) {
            }
        }
        return "failed"
    }

    companion object {
        private const val CHANNEL = "io.github.shahkimi.tgdl/native"
        private const val PERMISSION_REQUEST_CODE = 7311
    }
}
