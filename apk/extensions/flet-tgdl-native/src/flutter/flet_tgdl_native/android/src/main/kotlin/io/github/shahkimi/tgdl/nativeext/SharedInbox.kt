package io.github.shahkimi.tgdl.nativeext

/**
 * Text that other apps shared to us. It lives in memory only, so a share survives the app being closed or not yet
 * ready, but not the process being killed. [listener] is set while a Flutter engine is attached.
 */
object SharedInbox {
    private val items = ArrayList<String>()

    @Volatile
    var listener: (() -> Unit)? = null

    fun add(text: String) {
        val clean = text.trim()
        if (clean.isEmpty()) return
        synchronized(items) {
            items.add(clean.take(MAX_LENGTH))
            while (items.size > MAX_ITEMS) items.removeAt(0)
        }
        listener?.invoke()
    }

    fun drain(): List<String> = synchronized(items) {
        val copy = ArrayList(items)
        items.clear()
        copy
    }

    private const val MAX_ITEMS = 50
    private const val MAX_LENGTH = 20_000
}
