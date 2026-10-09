package io.github.shahkimi.tgdl.nativeext

import android.app.Activity
import android.content.Intent
import android.os.Bundle

/**
 * Invisible activity behind the share sheet, the text-selection menu and "Open with" for t.me links.
 * It only stores what it was given and brings the real app to the front.
 */
class ShareTargetActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store(intent)
        openApp()
        finish()
    }

    override fun onNewIntent(intent: Intent?) {
        super.onNewIntent(intent)
        store(intent)
        openApp()
        finish()
    }

    private fun store(intent: Intent?) {
        if (intent == null) return
        val text: CharSequence? = when (intent.action) {
            Intent.ACTION_SEND -> intent.getCharSequenceExtra(Intent.EXTRA_TEXT)
                ?: intent.getCharSequenceExtra(Intent.EXTRA_SUBJECT)
            Intent.ACTION_PROCESS_TEXT -> intent.getCharSequenceExtra(Intent.EXTRA_PROCESS_TEXT)
            Intent.ACTION_VIEW -> intent.dataString
            else -> null
        }
        if (text != null) SharedInbox.add(text.toString())
    }

    private fun openApp() {
        val launch = packageManager.getLaunchIntentForPackage(packageName) ?: return
        launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)
        try {
            startActivity(launch)
        } catch (_: Exception) {
            // Nothing sensible to do: the text is already in the inbox for the next start.
        }
    }
}
