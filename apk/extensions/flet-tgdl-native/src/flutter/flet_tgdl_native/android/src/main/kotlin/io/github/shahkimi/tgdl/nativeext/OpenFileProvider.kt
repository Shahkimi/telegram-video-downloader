package io.github.shahkimi.tgdl.nativeext

import android.content.ContentProvider
import android.content.ContentValues
import android.content.Context
import android.database.Cursor
import android.database.MatrixCursor
import android.net.Uri
import android.os.ParcelFileDescriptor
import android.provider.OpenableColumns
import android.webkit.MimeTypeMap
import java.io.File
import java.io.FileNotFoundException

/**
 * Hands one downloaded file to another app ("Open with"). Android 7+ refuses file:// links between apps, and files in
 * .nomedia folders have no MediaStore entry, so the app serves them itself.
 *
 * Not exported: another app can only read a file it was given with FLAG_GRANT_READ_URI_PERMISSION, and only read it.
 */
class OpenFileProvider : ContentProvider() {
    override fun onCreate(): Boolean = true

    private fun fileOf(uri: Uri): File {
        val path = uri.path ?: throw FileNotFoundException("no path")
        val file = File(path)
        if (!file.isFile) throw FileNotFoundException(path)
        return file
    }

    override fun getType(uri: Uri): String = mimeOf(File(uri.path ?: ""))

    override fun query(
        uri: Uri,
        projection: Array<out String>?,
        selection: String?,
        selectionArgs: Array<out String>?,
        sortOrder: String?,
    ): Cursor {
        val file = fileOf(uri)
        val known = arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE)
        val columns = projection?.filter { it in known }?.toTypedArray() ?: known
        val row = columns.map<String, Any> { if (it == OpenableColumns.DISPLAY_NAME) file.name else file.length() }
        return MatrixCursor(columns, 1).apply { addRow(row.toTypedArray()) }
    }

    override fun openFile(uri: Uri, mode: String): ParcelFileDescriptor {
        if (mode != "r") throw SecurityException("Files are shared read-only")
        return ParcelFileDescriptor.open(fileOf(uri), ParcelFileDescriptor.MODE_READ_ONLY)
    }

    override fun insert(uri: Uri, values: ContentValues?): Uri? = throw UnsupportedOperationException("read-only")

    override fun update(uri: Uri, values: ContentValues?, selection: String?, selectionArgs: Array<out String>?): Int = 0

    override fun delete(uri: Uri, selection: String?, selectionArgs: Array<out String>?): Int = 0

    companion object {
        /** Must match the provider's android:authorities in AndroidManifest.xml. */
        fun authority(context: Context) = "${context.packageName}.tgdl.files"

        fun uriFor(context: Context, file: File): Uri =
            Uri.Builder().scheme("content").authority(authority(context)).path(file.absolutePath).build()

        fun mimeOf(file: File): String =
            MimeTypeMap.getSingleton().getMimeTypeFromExtension(file.extension.lowercase()) ?: "application/octet-stream"
    }
}
