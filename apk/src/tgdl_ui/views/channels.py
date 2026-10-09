"""Channels tab: pick one of your chats (or type an id) and download every video in it."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import flet as ft

from tgdl.config import parse_channel
from tgdl.telegram.dialogs import ChatCategory, DialogInfo, list_dialogs
from tgdl.telegram.resolve import MediaFilter, scan_channel
from tgdl.telegram.session import LoginError
from tgdl.util import format_size

from ..widgets import banner, muted, title

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger("tgdl.ui.channels")

CATEGORY_ICON = {
    ChatCategory.PRIVATE_CHANNEL: ft.Icons.LOCK,
    ChatCategory.PRIVATE_GROUP: ft.Icons.GROUP,
    ChatCategory.PUBLIC_CHANNEL: ft.Icons.CAMPAIGN,
    ChatCategory.PUBLIC_GROUP: ft.Icons.FORUM,
}


class ChannelsView:
    def __init__(self, app: "App"):
        self.app = app
        self.category: ChatCategory | None = None
        self.loading = False
        self._scanning = False

        self.login_banner = ft.Container(visible=False)
        self.search = ft.TextField(hint_text="Search your chats", prefix_icon=ft.Icons.SEARCH, dense=True, on_change=self._on_search)
        self.only_videos = ft.Switch(label="Videos only", value=True)
        self.chips = ft.Row(wrap=True, spacing=6, run_spacing=6)
        self.manual = ft.TextField(label="Channel id or username", hint_text="-1001234567890 or @name", dense=True, expand=True)
        self.status = muted("")
        self.list = ft.ListView(spacing=0, height=420)

        self.root = ft.Column(
            expand=True,
            scroll=ft.ScrollMode.AUTO,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            spacing=10,
            controls=[
                title("Channels"),
                self.login_banner,
                muted("Download every video from a channel or group you are in.", size=13),
                self.only_videos,
                ft.Row([self.manual, ft.Button("Scan", on_click=self._on_manual_scan)], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Divider(),
                ft.Row([ft.Text("Your chats", weight=ft.FontWeight.BOLD), ft.IconButton(ft.Icons.REFRESH, tooltip="Reload", on_click=self._on_reload)],
                       alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                self.chips,
                self.search,
                self.status,
                self.list,
            ],
        )
        self._build_chips()

    # ---- chips ------------------------------------------------------------------
    def _build_chips(self) -> None:
        options: list[tuple[ChatCategory | None, str]] = [(None, "All")] + [(c, c.label + "s") for c in ChatCategory]
        self.chips.controls = [
            ft.Chip(
                label=ft.Text(label),
                selected=(self.category is cat),
                on_select=lambda e, cat=cat: self._select_category(cat),
            )
            for cat, label in options
        ]

    def _select_category(self, cat: ChatCategory | None) -> None:
        self.category = cat
        self._build_chips()
        self._render_list()
        self._update()

    def _on_search(self, e: ft.Event) -> None:
        self._render_list()
        self._update()

    def _update(self) -> None:
        try:
            self.root.update()
        except Exception:  # noqa: BLE001
            pass

    # ---- showing the tab ----------------------------------------------------------
    def on_show(self) -> None:
        self.app.page.run_task(self._show)

    async def _show(self) -> None:
        st = self.app.state
        ok, error = await st.session.check()
        if not ok:
            if error is not None and error.kind == "network":
                self.login_banner.content = banner("Cannot reach Telegram. Check your internet connection.", "warn")
            else:
                self.login_banner.content = banner(
                    "Log in to Telegram to see your channels and groups.", "info",
                    ft.TextButton("Log in", on_click=lambda e: self.app.open_login()))
            self.login_banner.visible = True
            self.list.controls = []
            self._update()
            return
        self.login_banner.visible = False
        if st.dialogs is None:
            await self.reload()
        else:
            self._render_list()
            self._update()

    def _on_reload(self, e: ft.Event) -> None:
        self.app.page.run_task(self.reload)

    async def reload(self) -> None:
        if self.loading:
            return
        st = self.app.state
        self.loading = True
        self.status.value = "Loading your chats..."
        self._update()
        try:
            client = await st.session.ensure_connected()
            st.dialogs = await list_dialogs(client)
            self.status.value = ""
        except LoginError as exc:
            self.status.value = exc.message
        except Exception as exc:  # noqa: BLE001
            log.exception("loading chats failed")
            self.status.value = f"Could not load chats: {exc}"
        finally:
            self.loading = False
        self._render_list()
        self._update()

    def _render_list(self) -> None:
        dialogs = self.app.state.dialogs or []
        needle = (self.search.value or "").strip().lower()
        shown = [d for d in dialogs
                 if (self.category is None or d.category is self.category)
                 and (not needle or needle in d.name.lower())]
        if dialogs and not shown:
            self.status.value = "No chats match."
        elif self.status.value == "No chats match.":
            self.status.value = ""
        self.list.controls = [
            ft.ListTile(
                leading=ft.Icon(CATEGORY_ICON[d.category]),
                title=ft.Text(d.name.replace("\n", " "), max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                subtitle=ft.Text(f"{d.category.label}  -  {d.id}", size=12),
                on_click=lambda e, d=d: self.app.page.run_task(self.scan, d.entity, d.name),
            )
            for d in shown[:300]
        ]

    # ---- scanning --------------------------------------------------------------------
    def _on_manual_scan(self, e: ft.Event) -> None:
        raw = (self.manual.value or "").strip().lstrip("@")
        if not raw:
            self.app.toast("Type a channel id or username")
            return
        self.app.page.run_task(self.scan_peer, parse_channel(raw))

    async def scan_peer(self, peer: Any) -> None:
        try:
            if not await self.app.ensure_login():
                return
            client = await self.app.state.session.ensure_connected()
            entity = await client.get_entity(peer)
        except Exception as exc:  # noqa: BLE001
            self.app.toast(f"Cannot open {peer}: {exc}", error=True)
            return
        await self.scan(entity, getattr(entity, "title", str(peer)))

    async def scan(self, entity: Any, name: str) -> None:
        if self._scanning:
            return
        self._scanning = True
        st = self.app.state
        page = self.app.page
        media = MediaFilter.VIDEO if self.only_videos.value else MediaFilter.ANY
        cancel = asyncio.Event()
        counter = ft.Text("Starting...", size=13)
        wait = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"Scanning {name}"[:60]),
            content=ft.Row([ft.ProgressRing(width=22, height=22, stroke_width=3), counter], spacing=14),
            actions=[ft.TextButton("Stop", on_click=lambda e: cancel.set())],
        )

        def progress(scanned: int, found: int, size: int) -> None:
            counter.value = f"Looked at {scanned} messages, found {found} ({format_size(size)})"
            try:
                wait.update()
            except Exception:  # noqa: BLE001
                pass

        page.show_dialog(wait)
        try:
            client = await st.session.ensure_connected()
            result = await scan_channel(client, entity, media, st.cfg.max_total_size_bytes, on_progress=progress, cancel=cancel)
        except LoginError as exc:
            page.pop_dialog()
            self.app.toast(exc.message, error=True)
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("scan failed")
            page.pop_dialog()
            self.app.toast(f"Scan failed: {exc}", error=True)
            return
        finally:
            self._scanning = False
        page.pop_dialog()

        if not result.messages:
            self.app.toast("Nothing to download in this chat" + (" (scan stopped)" if result.cancelled else ""))
            return
        self._confirm(entity, name, result)

    def _confirm(self, entity: Any, name: str, result) -> None:
        page = self.app.page
        kind = "video" if self.only_videos.value else "file"
        lines = [ft.Text(f"{len(result.messages)} {kind}{'s' if len(result.messages) != 1 else ''}, {format_size(result.total_size)}")]
        if result.limit_reached:
            lines.append(muted("Newer files were left out to stay under your size limit (Settings).", size=12))
        if result.cancelled:
            lines.append(muted("The scan was stopped early.", size=12))

        def start(e: ft.Event) -> None:
            page.pop_dialog()
            items = self.app.state.manager.enqueue_messages(result.messages, entity, name)
            self.app.toast(f"Added {len(items)} downloads", action="Queue", on_action=lambda ev: self.app.show("queue"))
            self.app.show("queue")

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(name[:60]),
            content=ft.Column(lines, tight=True, spacing=6),
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
                ft.Button("Download all", icon=ft.Icons.DOWNLOAD, on_click=start),
            ],
        )
        page.show_dialog(dialog)
