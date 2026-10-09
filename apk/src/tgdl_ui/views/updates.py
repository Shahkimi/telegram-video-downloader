"""Updates tab: new media posted in Library chats since you last looked, newest first, grouped by day."""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

import flet as ft

from tgdl.engine.models import DownloadItem, ItemState
from tgdl.library import LibraryEntry
from tgdl.telegram.browse import MediaEntry, fetch_media, newest_message_id
from tgdl.telegram.resolve import MediaFilter, ResolvedMsg
from tgdl.telegram.session import LoginError

from ..widgets import cover, empty_state, muted, safe_update
from .channel import describe_entry, relative_day

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger("tgdl.ui.updates")

PER_CHAT = 50
RUNNING = (ItemState.QUEUED, ItemState.RESOLVING, ItemState.DOWNLOADING, ItemState.PAUSED)


class UpdatesView:
    def __init__(self, app: "App"):
        self.app = app
        self.found: list[tuple[LibraryEntry, Any, MediaEntry]] = []   # (library entry, entity, media)
        self.checking = False
        self.last_check = 0.0
        self._buttons: dict[tuple[int, int], ft.Container] = {}

        self.appbar = ft.AppBar(
            title=ft.Text("Updates"),
            actions=[
                ft.IconButton(ft.Icons.DOWNLOAD, tooltip="Download all new", on_click=lambda e: self.download_all()),
                ft.IconButton(ft.Icons.REFRESH, tooltip="Check now", on_click=lambda e: app.page.run_task(self.refresh)),
            ],
        )
        self.status = muted("")
        self.progress = ft.ProgressBar(visible=False)
        self.list = ft.ListView(expand=True, spacing=0, padding=ft.Padding.only(bottom=24))
        self.root = ft.Column(expand=True, spacing=6, controls=[self.status, self.progress, self.list])

    # ---- showing ---------------------------------------------------------------------------
    def on_show(self) -> None:
        self.render()
        if not self.checking and time.time() - self.last_check > 15 * 60 and len(self.app.state.library):
            self.app.page.run_task(self.refresh)

    def render(self) -> None:
        self._buttons = {}
        st = self.app.state
        if not len(st.library):
            self.list.controls = [empty_state(ft.Icons.NEW_RELEASES_OUTLINED, "Add chats to your library to see their new posts here.",
                                              ft.Button("Browse chats", icon=ft.Icons.EXPLORE, on_click=lambda e: self.app.show("browse")))]
        elif not self.found:
            text = "Checking your library..." if self.checking else "No new media. Pull the refresh button to check again."
            self.list.controls = [empty_state(ft.Icons.DONE_ALL, text)]
        else:
            rows: list[ft.Control] = []
            day = None
            for entry, entity, media in sorted(self.found, key=lambda t: (t[2].date.timestamp() if t[2].date else 0), reverse=True):
                label = relative_day(media.date)
                if label != day:
                    day = label
                    rows.append(ft.Container(ft.Text(label, size=13, weight=ft.FontWeight.BOLD, color=ft.Colors.PRIMARY),
                                             padding=ft.Padding.only(top=12, bottom=4)))
                rows.append(self._row(entry, entity, media))
            self.list.controls = rows
        when = time.strftime("%H:%M", time.localtime(self.last_check)) if self.last_check else "never"
        new = len(self.found)
        self.status.value = f"{new} new  -  last checked {when}" if len(st.library) else ""
        self._refresh_buttons()
        safe_update(self.root)

    def _row(self, entry: LibraryEntry, entity: Any, media: MediaEntry) -> ft.Control:
        button = ft.Container(width=48, height=48, alignment=ft.Alignment.CENTER)
        self._buttons[(entry.chat_id, media.msg_id)] = button
        slot = ft.Container(content=cover(None, entry.title, width=40, height=40, radius=20, text_size=14), width=40, height=40)
        self.app.page.run_task(self._fill_cover, slot, entry)
        return ft.Container(
            ink=True,
            padding=ft.Padding.symmetric(vertical=6),
            on_click=lambda e: self.app.open_channel(entry.chat_id, entry.title, entity=entity, username=entry.username,
                                                     category=entry.category),
            content=ft.Row(spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                slot,
                ft.Column(spacing=2, expand=True, controls=[
                    ft.Text(entry.title, size=13, weight=ft.FontWeight.BOLD, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(media.caption or media.name, size=13, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                    muted(describe_entry(media), size=11),
                ]),
                button,
            ]),
        )

    async def _fill_cover(self, slot: ft.Container, entry: LibraryEntry) -> None:
        data = await self.app.cover_for(entry.chat_id)
        if data:
            slot.content = cover(data, entry.title, width=40, height=40, radius=20)
            safe_update(slot)

    # ---- download state -----------------------------------------------------------------------
    def _items(self) -> dict[tuple[int, int], DownloadItem]:
        out: dict[tuple[int, int], DownloadItem] = {}
        for item in self.app.state.manager.items:
            if item.chat_id is not None and item.msg_id is not None:
                key = (item.chat_id, item.msg_id)
                if key not in out or item.state in RUNNING:
                    out[key] = item
        return out

    def _refresh_buttons(self) -> None:
        items = self._items()
        history = self.app.state.history
        lookup = {(e.chat_id, m.msg_id): (e, ent, m) for e, ent, m in self.found}
        for key, holder in self._buttons.items():
            item = items.get(key)
            if item is not None and item.state is ItemState.DOWNLOADING:
                content: ft.Control = ft.ProgressRing(value=(item.percent / 100) if item.total else None, width=24, height=24, stroke_width=3)
            elif item is not None and item.state in RUNNING:
                content = ft.Icon(ft.Icons.PAUSE_CIRCLE if item.state is ItemState.PAUSED else ft.Icons.SCHEDULE,
                                  color=ft.Colors.ON_SURFACE_VARIANT)
            elif history.downloaded(*key):
                content = ft.Icon(ft.Icons.CHECK_CIRCLE, color=ft.Colors.GREEN)
            else:
                entry, entity, media = lookup[key]
                failed = item is not None and item.state is ItemState.FAILED
                content = ft.IconButton(ft.Icons.ERROR if failed else ft.Icons.ARROW_CIRCLE_DOWN_OUTLINED,
                                        icon_color=ft.Colors.ERROR if failed else None,
                                        tooltip=(item.error if failed and item else None) or "Download",
                                        on_click=lambda e, t=(entry, entity, media): self.download([t]))
            holder.content = content

    def on_downloads_changed(self) -> None:
        self._refresh_buttons()
        for holder in self._buttons.values():
            safe_update(holder)

    # ---- actions ---------------------------------------------------------------------------------
    def download(self, found: list[tuple[LibraryEntry, Any, MediaEntry]]) -> int:
        items = self._items()
        history = self.app.state.history
        manager = self.app.state.manager
        count = 0
        for entry, entity, media in found:
            key = (entry.chat_id, media.msg_id)
            if history.downloaded(*key) or (key in items and items[key].state in RUNNING) or media.message is None:
                continue
            for item in manager.enqueue_telegram([ResolvedMsg(media.message, entity, entry.title)]):
                item.chat_id = entry.chat_id
                item.chat_title = item.chat_title or entry.title
            count += 1
        if count:
            self.app.toast(f"Added {count} to the queue", action="Queue", on_action=lambda e: self.app.open_queue())
        else:
            self.app.toast("Nothing new to download")
        self.on_downloads_changed()
        return count

    def download_all(self) -> int:
        return self.download(list(self.found))

    def forget_chat(self, chat_id: int) -> None:
        """The chat was opened, so its posts are no longer new."""
        before = len(self.found)
        self.found = [t for t in self.found if t[0].chat_id != chat_id]
        if len(self.found) != before and self.app.current == "updates":
            self.render()

    async def refresh(self) -> None:
        if self.checking:
            return
        st = self.app.state
        entries = st.library.entries()
        if not entries:
            self.render()
            return
        try:
            client = await st.session.ensure_connected()
        except LoginError as exc:
            self.app.toast(exc.message, error=True)
            return
        self.checking = True
        self.progress.visible = True
        self.render()
        found: list[tuple[LibraryEntry, Any, MediaEntry]] = []
        failed: list[str] = []
        for n, entry in enumerate(entries, start=1):
            self.status.value = f"Checking {n} of {len(entries)}: {entry.title}"
            safe_update(self.status)
            try:
                entity = await self.app.entity_for(entry.chat_id, entry.username)
                if entry.last_seen_id <= 0:
                    entry.last_seen_id = await newest_message_id(client, entity)  # first check: start counting from now
                    entry.unread = 0
                    continue
                try:
                    f = MediaFilter(entry.media_filter)
                except ValueError:
                    f = MediaFilter.VIDEO
                page = await fetch_media(client, entity, f, min_id=entry.last_seen_id, limit=PER_CHAT)
                entry.unread = len(page.entries)
                found.extend((entry, entity, m) for m in page.entries)
            except Exception as exc:  # noqa: BLE001 - one broken chat must not stop the rest
                log.warning("update check for %s failed: %s", entry.chat_id, exc)
                failed.append(entry.title)
            finally:
                entry.last_checked = time.time()
        st.save_library()
        self.found = found
        self.checking = False
        self.last_check = time.time()
        self.progress.visible = False
        self.render()
        self.app.set_badge("updates", sum(e.unread for e in entries))
        if failed:
            self.app.toast(f"Could not check {len(failed)} chat(s): {', '.join(failed[:3])}", error=True)
