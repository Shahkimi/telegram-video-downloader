# flet-tgdl-native

Small Flet extension used by the TG Downloader Android app. It is only active on Android; on every other platform
`tgdl_ui.native` quietly does nothing.

What it adds:

| Python call | Android side |
|---|---|
| `on_shared` event + `take_pending()` | `ShareTargetActivity` receives "Share", "Select text > Download" and "Open with" for `t.me` links and queues the text in `SharedInbox` |
| `start_keep_alive()` / `update_keep_alive()` / `stop_keep_alive()` | `KeepAliveService`, a `dataSync` foreground service with a progress notification, a partial wake lock and a Wi-Fi lock |
| `scan_file(path)` | `MediaScannerConnection`, so finished files show up in Gallery and Files |
| `public_downloads_dir()` | `Environment.DIRECTORY_DOWNLOADS` |
| `sdk_int()` | `Build.VERSION.SDK_INT` |
| `request_permission(name)` | runtime permission prompt (notifications, legacy storage) |
| `request_ignore_battery_optimizations()` | system dialog that exempts the app from battery optimisation |

## Layout

```
src/flet_tgdl_native/           Python control (TgdlNative)
src/flutter/flet_tgdl_native/   Flutter plugin package
  lib/                          Dart service that forwards Python calls to the platform channel
  android/                      Kotlin plugin, activity, service and manifest
```

The app's `pyproject.toml` points at this folder with `[tool.flet.dev_packages]`, so `flet build apk` installs it from
disk instead of PyPI.

## Share-sheet flow

1. Another app shares text. Android starts `ShareTargetActivity`.
2. The activity stores the text in `SharedInbox`, brings the app to the front and finishes.
3. If the app is running, the plugin tells Dart, which raises `on_shared` in Python. Python then calls `take_pending()`.
4. If the app was closed, nothing is lost: the text waits in `SharedInbox` until Python asks for it at startup.
