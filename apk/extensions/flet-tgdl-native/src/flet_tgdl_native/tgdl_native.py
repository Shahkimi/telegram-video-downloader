from typing import Optional

import flet as ft

__all__ = ["TgdlNative"]

SHORT = 15.0       # seconds; plain platform calls answer immediately
USER_ACTION = 180.0  # seconds; permission prompts wait for a person


@ft.control("TgdlNative")
class TgdlNative(ft.Service):
    """Android-only helpers for TG Downloader. Construct it once, while a page exists."""

    on_shared: Optional[ft.ControlEventHandler["TgdlNative"]] = None
    """
    Something was shared to the app. The event carries no text on purpose: call :meth:`take_pending`.
    Text that arrives while Python is not listening stays queued, so nothing is lost.
    """

    def before_update(self):
        super().before_update()
        if self.page.platform != ft.PagePlatform.ANDROID:
            raise ft.FletUnsupportedPlatformException("TgdlNative only works on Android.")

    async def take_pending(self) -> list[str]:
        """Texts shared to the app since the last call. The list is emptied."""
        result = await self._invoke_method("take_pending", timeout=SHORT)
        return [str(item) for item in (result or [])]

    async def start_keep_alive(self, title: str, text: str) -> bool:
        """Start the foreground service that keeps downloads running with the screen off."""
        return bool(await self._invoke_method("start_keep_alive", {"title": title, "text": text}, timeout=SHORT))

    async def update_keep_alive(self, text: str, progress: int = -1) -> None:
        """Change the notification text. `progress` is 0-100, or -1 for an unknown amount."""
        await self._invoke_method("update_keep_alive", {"text": text, "progress": int(progress)}, timeout=SHORT)

    async def stop_keep_alive(self) -> None:
        await self._invoke_method("stop_keep_alive", timeout=SHORT)

    async def public_downloads_dir(self) -> Optional[str]:
        return await self._invoke_method("public_downloads_dir", timeout=SHORT)

    async def scan_file(self, path: str) -> None:
        """Tell Android's media scanner about a new file so galleries show it."""
        await self._invoke_method("scan_file", {"path": path}, timeout=SHORT)

    async def scan_files(self, paths: list[str]) -> int:
        """Ask the media scanner to look at many files again (after a .nomedia marker changed)."""
        return int(await self._invoke_method("scan_files", {"paths": list(paths)}, timeout=SHORT) or 0)

    async def has_all_files_access(self) -> bool:
        return bool(await self._invoke_method("has_all_files_access", timeout=SHORT))

    async def request_all_files_access(self) -> str:
        """`granted`, `asked` (Android's settings screen was opened) or `failed`."""
        return str(await self._invoke_method("request_all_files_access", timeout=USER_ACTION) or "failed")

    async def sdk_int(self) -> int:
        return int(await self._invoke_method("sdk_int", timeout=SHORT) or 0)

    async def request_permission(self, name: str) -> bool:
        """Ask for a runtime permission such as `android.permission.POST_NOTIFICATIONS`. True when granted."""
        return bool(await self._invoke_method("request_permission", {"name": name}, timeout=USER_ACTION))

    async def request_ignore_battery_optimizations(self) -> str:
        """`granted` (already exempt), `asked` (system dialog opened) or `failed`."""
        result = await self._invoke_method("request_ignore_battery_optimizations", timeout=USER_ACTION)
        return str(result or "failed")
