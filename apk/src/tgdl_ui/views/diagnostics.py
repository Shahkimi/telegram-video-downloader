"""Diagnostics screen: what the app is running on, whether decryption is fast, recent log lines."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import flet as ft

from tgdl import crypto_backend, diagnostics, logs

from ..widgets import muted, section

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger("tgdl.ui.diagnostics")


class DiagnosticsView:
    def __init__(self, app: "App"):
        self.app = app
        self.facts = ft.Column(spacing=4)
        self.log_box = ft.Text("", size=11, font_family="monospace", selectable=True)
        self.speed_btn = ft.Button("Run speed test", icon=ft.Icons.SPEED, on_click=self._on_speed)
        self.root = ft.Column(
            expand=True,
            scroll=ft.ScrollMode.AUTO,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            spacing=10,
            controls=[
                section("This device"),
                self.facts,
                self.speed_btn,
                muted("Telegram downloads need fast decryption. If the AES backend says pyaes or cryptography, downloads will be slow.", size=12),
                ft.Divider(),
                section("Recent log"),
                ft.Row([
                    ft.OutlinedButton("Copy", icon=ft.Icons.CONTENT_COPY, on_click=self._on_copy),
                    ft.OutlinedButton("Share", icon=ft.Icons.SHARE, on_click=self._on_share),
                    ft.TextButton("Refresh", icon=ft.Icons.REFRESH, on_click=lambda e: self.refresh()),
                ], wrap=True, spacing=8),
                ft.Container(content=self.log_box, padding=8, border_radius=8, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST),
            ],
        )
        self.refresh()

    def _update(self) -> None:
        try:
            self.root.update()
        except Exception:  # noqa: BLE001
            pass

    def refresh(self) -> None:
        st = self.app.state
        rows = diagnostics.collect(st.paths, st.cfg)
        if self.app.native.available:
            rows.append(("Native extension", "loaded"))
        elif st.paths.is_android:
            rows.append(("Native extension", "missing: sharing from other apps and background downloads are limited"))
        info = crypto_backend.current_info()
        self.facts.controls = [
            ft.Row([
                ft.Text(label, size=12, color=ft.Colors.ON_SURFACE_VARIANT, width=130),
                ft.Text(value, size=12, expand=True, selectable=True,
                        color=ft.Colors.ERROR if (label == "AES-IGE backend" and not info.fast) else None),
            ], vertical_alignment=ft.CrossAxisAlignment.START)
            for label, value in rows
        ]
        self.log_box.value = "\n".join(logs.tail(120)) or "(no log lines yet)"
        self._update()

    async def _on_speed(self, e: ft.Event) -> None:
        self.speed_btn.disabled = True
        self.speed_btn.content = "Testing..."
        self._update()
        try:
            await asyncio.to_thread(crypto_backend.benchmark, 4.0)
        except Exception as exc:  # noqa: BLE001
            self.app.toast(f"Speed test failed: {exc}", error=True)
        self.speed_btn.disabled = False
        self.speed_btn.content = "Run speed test"
        self.refresh()

    async def _on_copy(self, e: ft.Event) -> None:
        await self.app.clipboard.set(self.log_box.value or "")
        self.app.toast("Log copied")

    async def _on_share(self, e: ft.Event) -> None:
        try:
            await self.app.share.share_text(self.log_box.value or "", title="TG Downloader log")
        except Exception:  # noqa: BLE001
            await self.app.clipboard.set(self.log_box.value or "")
            self.app.toast("Sharing is not available here, the log was copied instead")
