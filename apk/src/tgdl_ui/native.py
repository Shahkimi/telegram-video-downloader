"""
Safe wrapper around the optional Android extension (flet_tgdl_native).

On desktop, or when the extension is missing, every method quietly does nothing, so the rest of
the UI never has to check where it is running.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

import flet as ft

log = logging.getLogger("tgdl.native")

try:  # only present in the Android build (and when installed for desktop development)
    from flet_tgdl_native import TgdlNative
except ImportError:  # pragma: no cover - depends on the environment
    TgdlNative = None


class Native:
    def __init__(self, page: ft.Page, on_text: Callable[[str], Any]):
        """`on_text` receives every text another app shared to us."""
        self.page = page
        self.on_text = on_text
        self.service: Any = None
        self.available = False
        if TgdlNative is not None and page.platform == ft.PagePlatform.ANDROID:
            try:
                self.service = TgdlNative(on_shared=self._on_event)
                self.available = True
            except Exception:  # noqa: BLE001
                log.exception("could not start the native extension")

    def _on_event(self, e: Any) -> None:
        # The event is only a nudge; the texts are fetched so none can be lost or delivered twice.
        self.page.run_task(self._drain)

    async def _drain(self) -> None:
        for text in await self.take_pending():
            try:
                self.on_text(text)
            except Exception:  # noqa: BLE001
                log.exception("handling shared text failed")

    async def _call(self, name: str, *args: Any, default: Any = None) -> Any:
        if not self.available:
            return default
        try:
            return await getattr(self.service, name)(*args)
        except Exception:  # noqa: BLE001
            log.exception("native call %s failed", name)
            return default

    async def take_pending(self) -> list[str]:
        return await self._call("take_pending", default=[]) or []

    async def start_keep_alive(self, title: str, text: str) -> bool:
        return bool(await self._call("start_keep_alive", title, text, default=False))

    async def update_keep_alive(self, text: str, progress: int = -1) -> None:
        await self._call("update_keep_alive", text, progress)

    async def stop_keep_alive(self) -> None:
        await self._call("stop_keep_alive")

    async def public_downloads_dir(self) -> str | None:
        return await self._call("public_downloads_dir", default=None)

    async def scan_file(self, path: str) -> None:
        await self._call("scan_file", path)

    async def sdk_int(self) -> int:
        return int(await self._call("sdk_int", default=0) or 0)

    async def request_ignore_battery_optimizations(self) -> str:
        """'granted', 'asked' (the system dialog is open) or 'failed'."""
        return str(await self._call("request_ignore_battery_optimizations", default="failed") or "failed")

    async def request_permission(self, name: str) -> bool:
        """Ask for an Android runtime permission, e.g. android.permission.POST_NOTIFICATIONS. True when granted."""
        return bool(await self._call("request_permission", name, default=False))
