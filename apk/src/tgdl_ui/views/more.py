"""More tab: quick switches, the download queue, settings and the other tools (Tachiyomi's "More")."""
from __future__ import annotations

from typing import TYPE_CHECKING

import flet as ft

from tgdl import __version__
from tgdl.engine.models import ItemState

from ..widgets import muted, safe_update

if TYPE_CHECKING:
    from ..app import App


class MoreView:
    def __init__(self, app: "App"):
        self.app = app
        cfg = app.state.cfg
        self.appbar = None   # Tachiyomi shows a big logo here instead of a bar

        self.account = muted("Checking...", size=13)
        self.hide_switch = ft.Switch(value=cfg.nomedia, on_change=self._on_hide)
        self.queue_line = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self.queue_tile = ft.ListTile(leading=ft.Icon(ft.Icons.DOWNLOAD), title=ft.Text("Download queue"), subtitle=self.queue_line,
                                      on_click=lambda e: app.open_queue())
        self.root = ft.ListView(
            expand=True,
            spacing=0,
            controls=[
                ft.Container(
                    padding=ft.Padding.only(top=28, bottom=18),
                    alignment=ft.Alignment.CENTER,
                    content=ft.Column(horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=6, tight=True, controls=[
                        ft.Icon(ft.Icons.DOWNLOAD_FOR_OFFLINE, size=56, color=ft.Colors.PRIMARY),
                        ft.Text("TG Downloader", size=18, weight=ft.FontWeight.BOLD),
                        self.account,
                    ]),
                ),
                ft.Divider(height=1),
                ft.ListTile(leading=ft.Icon(ft.Icons.VISIBILITY_OFF_OUTLINED), title=ft.Text("Hide downloads from gallery"),
                            subtitle=ft.Text("Puts .nomedia in the download folder", size=12), trailing=self.hide_switch,
                            on_click=lambda e: self._flip_hide()),
                ft.Divider(height=1),
                self.queue_tile,
                ft.ListTile(leading=ft.Icon(ft.Icons.SETTINGS_OUTLINED), title=ft.Text("Settings"), on_click=lambda e: app.open_settings()),
                ft.ListTile(leading=ft.Icon(ft.Icons.RULE), title=ft.Text("Link rules"), subtitle=ft.Text("Teach the app new kinds of links", size=12),
                            on_click=lambda e: app.open_rules()),
                ft.ListTile(leading=ft.Icon(ft.Icons.BUG_REPORT_OUTLINED), title=ft.Text("Diagnostics"),
                            subtitle=ft.Text("Speed check, versions, logs", size=12), on_click=lambda e: app.open_diagnostics()),
                ft.ListTile(leading=ft.Icon(ft.Icons.INFO_OUTLINE), title=ft.Text("About"), subtitle=ft.Text(f"Version {__version__}", size=12),
                            on_click=lambda e: self._about()),
            ],
        )

    def on_show(self) -> None:
        self.hide_switch.value = self.app.state.cfg.nomedia
        self.refresh_queue_line()
        safe_update(self.root)
        self.app.page.run_task(self.refresh_account)

    async def refresh_account(self) -> None:
        session = self.app.state.session
        ok, error = await session.check()
        if ok:
            me = await session.me()
            self.account.value = f"Logged in as {me.label}" if me else "Logged in"
        elif error is not None and error.kind == "network":
            self.account.value = "Offline"
        else:
            self.account.value = "Not logged in"
        safe_update(self.account)

    def refresh_queue_line(self) -> None:
        manager = self.app.state.manager
        items = manager.items
        active = sum(1 for i in items if i.state is ItemState.DOWNLOADING)
        waiting = sum(1 for i in items if i.state is ItemState.QUEUED)
        paused = sum(1 for i in items if i.state is ItemState.PAUSED)
        parts = []
        if manager.paused:
            parts.append("paused")
        if active:
            parts.append(f"{active} downloading")
        if waiting:
            parts.append(f"{waiting} waiting")
        if paused:
            parts.append(f"{paused} on hold")
        text = ", ".join(parts) or "Nothing downloading"
        if self.queue_line.value != text:
            self.queue_line.value = text
            safe_update(self.queue_line)

    def _flip_hide(self) -> None:
        self.hide_switch.value = not self.hide_switch.value
        safe_update(self.hide_switch)
        self.app.page.run_task(self.app.set_download_root_hidden, bool(self.hide_switch.value))

    def _on_hide(self, e: ft.Event) -> None:
        self.app.page.run_task(self.app.set_download_root_hidden, bool(self.hide_switch.value))

    def _about(self) -> None:
        page = self.app.page
        page.show_dialog(ft.AlertDialog(
            title=ft.Text("TG Downloader"),
            content=ft.Text(
                f"Version {__version__}\n\nDownloads videos and files from your Telegram chats and from other websites "
                "with yt-dlp. Open source under the MIT License.", size=13),
            actions=[ft.TextButton("Close", on_click=lambda e: page.pop_dialog())],
        ))
