"""
One chat, laid out like a Tachiyomi manga page: cover and details on top, then its media newest first, like chapters.
Tap the arrow on a row to download it, long-press to select several, or download the whole channel from the menu.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import flet as ft

from tgdl.engine.models import DownloadItem, ItemState
from tgdl.storage import hidden_by
from tgdl.stream import StreamSource
from tgdl.telegram.browse import MediaEntry, fetch_media
from tgdl.telegram.dialogs import ChatCategory
from tgdl.telegram.resolve import MediaFilter, ResolvedMsg, scan_channel
from tgdl.telegram.session import LoginError
from tgdl.util import format_eta, format_size, format_speed

from ..widgets import cover, empty_state, muted, safe_update
from . import player

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger("tgdl.ui.channel")

FILTERS = [(MediaFilter.VIDEO, "Videos"), (MediaFilter.FILE, "Files"), (MediaFilter.ANY, "Everything")]
RUNNING = (ItemState.QUEUED, ItemState.RESOLVING, ItemState.DOWNLOADING, ItemState.PAUSED)


def describe_entry(entry: MediaEntry) -> str:
    parts = []
    if entry.date:
        parts.append(entry.date.astimezone().strftime("%d %b %Y"))
    if entry.size:
        parts.append(format_size(entry.size))
    if entry.duration:
        parts.append(format_eta(entry.duration))
    parts.append(f"#{entry.msg_id}")
    return "  -  ".join(parts)


def relative_day(when: datetime | None) -> str:
    if when is None:
        return "Earlier"
    local = when.astimezone() if when.tzinfo else when.replace(tzinfo=timezone.utc).astimezone()
    days = (datetime.now().astimezone().date() - local.date()).days
    if days <= 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    if days < 7:
        return f"{days} days ago"
    return local.strftime("%d %b %Y")


class MediaRow:
    """One "chapter" row. set_status() swaps the button on the right without rebuilding the row."""

    def __init__(self, view: "ChannelView", entry: MediaEntry):
        self.view = view
        self.entry = entry
        self.status = ""
        self.check = ft.Checkbox(value=False, visible=False, on_change=lambda e: view.toggle(entry.msg_id))
        self.thumb_img = ft.Container(
            width=64, height=48, alignment=ft.Alignment.CENTER,
            content=ft.Icon(ft.Icons.MOVIE if entry.is_video else ft.Icons.INSERT_DRIVE_FILE, color=ft.Colors.ON_SURFACE_VARIANT),
        )
        layers: list[ft.Control] = [self.thumb_img]
        if entry.is_video:  # tap the picture to watch before downloading
            layers.append(ft.Container(width=64, height=48, alignment=ft.Alignment.CENTER, content=ft.Icon(
                ft.Icons.PLAY_CIRCLE_FILL, color="#E6FFFFFF", size=24)))
        self.thumb = ft.Container(
            width=64, height=48, border_radius=6, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS, tooltip="Watch" if entry.is_video else None,
            on_click=(lambda e: view.watch(entry)) if entry.is_video else None,
            content=ft.Stack(layers, width=64, height=48),
        )
        self.name = ft.Text(entry.caption or entry.name, size=14, weight=ft.FontWeight.W_500, max_lines=2,
                            overflow=ft.TextOverflow.ELLIPSIS)
        self.meta = ft.Text(describe_entry(entry), size=12, color=ft.Colors.ON_SURFACE_VARIANT, max_lines=1)
        self.slot = ft.Container(width=48, height=48, alignment=ft.Alignment.CENTER)
        self.control = ft.Container(
            padding=ft.Padding.symmetric(vertical=6),
            ink=True,
            on_click=lambda e: view.on_row_click(entry),
            on_long_press=lambda e: view.on_row_long_press(entry),
            content=ft.Row(
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[self.check, self.thumb, ft.Column([self.name, self.meta], spacing=2, expand=True), self.slot],
            ),
        )

    def set_thumb(self, data: bytes) -> None:
        self.thumb_img.content = ft.Image(src=data, fit=ft.BoxFit.COVER, width=64, height=48)
        safe_update(self.thumb)

    def set_selected(self, selecting: bool, selected: bool) -> None:
        self.check.visible = selecting
        self.check.value = selected
        self.control.bgcolor = ft.Colors.SECONDARY_CONTAINER if selected else None

    def set_status(self, status: str, item: DownloadItem | None) -> bool:
        """Returns True when something visible changed."""
        key = status
        if status == "downloading" and item is not None:
            key = f"downloading:{int(item.percent)}"
        if key == self.status:
            return False
        self.status = key
        v, entry = self.view, self.entry
        muted_name = status == "done"
        self.name.color = ft.Colors.ON_SURFACE_VARIANT if muted_name else None
        if status == "downloading" and item is not None:
            ring = ft.ProgressRing(value=(item.percent / 100) if item.total else None, width=24, height=24, stroke_width=3)
            self.slot.content = ft.Container(content=ring, tooltip="Pause", on_click=lambda e: v.pause(item))
            self.meta.value = f"{item.percent:.0f}%  -  {format_speed(item.speed)}  -  ETA {format_eta(item.eta)}"
        else:
            self.meta.value = describe_entry(entry)
            spec = {
                "none": (ft.Icons.ARROW_CIRCLE_DOWN_OUTLINED, None, "Download", lambda e: v.download([entry])),
                "queued": (ft.Icons.SCHEDULE, ft.Colors.ON_SURFACE_VARIANT, "Waiting. Tap to cancel", lambda e: v.cancel(item)),
                "paused": (ft.Icons.PAUSE_CIRCLE, ft.Colors.TERTIARY, "Paused. Tap to resume", lambda e: v.resume(item)),
                "done": (ft.Icons.CHECK_CIRCLE, ft.Colors.GREEN, "Downloaded", lambda e: v.show_details(entry)),
                "failed": (ft.Icons.ERROR, ft.Colors.ERROR, (item.error if item else None) or "Failed. Tap to try again",
                           lambda e: v.retry(item)),
            }[status]
            icon, color, tip, handler = spec
            self.slot.content = ft.IconButton(icon, icon_color=color, tooltip=tip[:120], on_click=handler)
        return True


class ChannelView:
    def __init__(self, app: "App", chat_id: int, title: str, *, entity: Any = None, username: str | None = None,
                 category: str = ""):
        self.app = app
        self.state = app.state
        self.chat_id = chat_id
        self.title = title
        self.entity = entity or app.state.entities.get(chat_id)
        self.username = username or getattr(self.entity, "username", None)
        self.category = category
        entry = app.state.library.get(chat_id)
        try:
            self.filter = MediaFilter(entry.media_filter) if entry else MediaFilter.VIDEO
        except ValueError:
            self.filter = MediaFilter.VIDEO
        self.entries: list[MediaEntry] = []
        self.rows: dict[int, MediaRow] = {}
        self.selected: set[int] = set()
        self.selecting = False
        self.next_offset = 0
        self.loading = False
        self.closed = False
        self._scanning = False
        self.view: ft.View | None = None

        # header
        self.cover_slot = ft.Container(content=cover(None, title, width=96, height=136, radius=8, text_size=36), width=96, height=136)
        self.title_text = ft.Text(title, size=20, weight=ft.FontWeight.BOLD, max_lines=3, overflow=ft.TextOverflow.ELLIPSIS)
        self.kind_text = muted("")
        self.stats_text = muted("")
        self.folder_text = muted("", size=11, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)
        self.lib_btn = self._action_button(ft.Icons.FAVORITE_BORDER, "Add to library", self._toggle_library)
        self.folder_btn = self._action_button(ft.Icons.FOLDER_OPEN, "Folder",
                                              lambda e: app.page.run_task(app.channel_folder_dialog, chat_id, self.title))
        self.hide_btn = self._action_button(ft.Icons.VISIBILITY_OFF, "Hide", self._toggle_hidden)
        self.all_btn = self._action_button(ft.Icons.DOWNLOAD_FOR_OFFLINE, "Get all", lambda e: app.page.run_task(self.download_whole_channel))
        self.filter_chips = ft.Row(spacing=6, scroll=ft.ScrollMode.AUTO)
        self.count_text = ft.Text("", size=13, weight=ft.FontWeight.W_600)
        self.banner = ft.Container(visible=False)
        self.more_btn = ft.TextButton("Load older", icon=ft.Icons.EXPAND_MORE, visible=False, on_click=lambda e: app.page.run_task(self.load_more))
        self.spinner = ft.ProgressBar(visible=False)
        self.empty = ft.Container(visible=False)

        self.header = ft.Column(spacing=10, controls=[
            ft.Row(spacing=14, vertical_alignment=ft.CrossAxisAlignment.START, controls=[
                self.cover_slot,
                ft.Column([self.title_text, self.kind_text, self.stats_text, self.folder_text], spacing=4, expand=True),
            ]),
            ft.Row([self.lib_btn, self.folder_btn, self.hide_btn, self.all_btn], alignment=ft.MainAxisAlignment.SPACE_AROUND),
            self.banner,
            self.filter_chips,
            ft.Row([self.count_text, ft.TextButton("Select", icon=ft.Icons.CHECKLIST, on_click=lambda e: self.start_selecting())],
                   alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            self.spinner,
        ])
        self.footer = ft.Column([self.more_btn, ft.Container(height=88)], horizontal_alignment=ft.CrossAxisAlignment.CENTER)
        self.list = ft.ListView(expand=True, spacing=0, controls=[self.header, self.empty, self.footer])
        self.root = self.list

        # app bar actions
        self.menu = ft.PopupMenuButton(icon=ft.Icons.DOWNLOAD, tooltip="Download", items=[
            ft.PopupMenuItem(content="Download everything shown", icon=ft.Icons.DOWNLOAD, on_click=lambda e: self.download_loaded()),
            ft.PopupMenuItem(content="Download whole channel...", icon=ft.Icons.DOWNLOAD_FOR_OFFLINE,
                             on_click=lambda e: app.page.run_task(self.download_whole_channel)),
            ft.PopupMenuItem(content="Select items", icon=ft.Icons.CHECKLIST, on_click=lambda e: self.start_selecting()),
        ])
        self.normal_actions: list[ft.Control] = [
            self.menu,
            ft.IconButton(ft.Icons.REFRESH, tooltip="Reload", on_click=lambda e: app.page.run_task(self.reload)),
        ]
        self.select_actions: list[ft.Control] = [
            ft.IconButton(ft.Icons.SELECT_ALL, tooltip="Select all", on_click=lambda e: self.select_all()),
            ft.IconButton(ft.Icons.FLIP_TO_BACK, tooltip="Invert selection", on_click=lambda e: self.invert()),
            ft.IconButton(ft.Icons.CLOSE, tooltip="Done", on_click=lambda e: self.stop_selecting()),
        ]
        self.fab = ft.FloatingActionButton(icon=ft.Icons.DOWNLOAD, content="Download", visible=False,
                                           on_click=lambda e: self.download_selected())
        self._build_filter_chips()
        self.refresh_header()

    @staticmethod
    def _action_button(icon: str, label: str, handler: Any) -> ft.Container:
        return ft.Container(
            ink=True, border_radius=8, padding=ft.Padding.symmetric(horizontal=8, vertical=6), on_click=handler,
            content=ft.Column([ft.Icon(icon, color=ft.Colors.PRIMARY), ft.Text(label, size=11, color=ft.Colors.PRIMARY)],
                              spacing=2, horizontal_alignment=ft.CrossAxisAlignment.CENTER, tight=True),
        )

    @staticmethod
    def _set_action(button: ft.Container, icon: str, label: str) -> None:
        col = button.content
        col.controls[0].icon = icon
        col.controls[1].value = label

    # ---- opening and closing --------------------------------------------------------------
    def open(self) -> None:
        self.view = self.app.push(self.title, self.root, owner=self, actions=list(self.normal_actions), fab=self.fab)
        self.app.page.run_task(self._start)

    def on_close(self) -> None:
        self.closed = True

    async def _start(self) -> None:
        if not await self.app.ensure_login():
            self._show_banner("Log in to Telegram to see this chat.", ft.Icons.LOGIN)
            return
        try:
            if self.entity is None:
                self.entity = await self.app.entity_for(self.chat_id, self.username)
            if not self.username:
                self.username = getattr(self.entity, "username", None)
        except Exception as exc:  # noqa: BLE001
            self._show_banner(f"Cannot open this chat: {exc}", ft.Icons.ERROR_OUTLINE)
            return
        self.refresh_header()
        self.app.page.run_task(self._load_cover)
        await self.reload()

    async def _load_cover(self) -> None:
        data = await self.app.cover_for(self.chat_id, self.entity)
        if data and not self.closed:
            self.cover_slot.content = cover(data, self.title, width=96, height=136, radius=8)
            safe_update(self.cover_slot)

    def _show_banner(self, text: str, icon: str) -> None:
        self.banner.content = ft.Row([ft.Icon(icon, color=ft.Colors.ERROR), ft.Text(text, expand=True, size=13)], spacing=8)
        self.banner.visible = True
        safe_update(self.list)

    # ---- header -------------------------------------------------------------------------------
    def refresh_header(self) -> None:
        st = self.state
        entry = st.library.get(self.chat_id)
        try:
            kind = ChatCategory(self.category).label if self.category else "Chat"
        except ValueError:
            kind = "Chat"
        self.kind_text.value = kind + (f"  -  @{self.username}" if self.username else "")
        downloaded = sum(1 for r in st.history.records if r.chat_id == self.chat_id)
        self.stats_text.value = f"{downloaded} downloaded" + (f"  -  {entry.unread} new" if entry and entry.unread else "")
        folder = st.folder_for(self.chat_id, self.title)
        hidden = hidden_by(folder) is not None or (entry is not None and entry.nomedia)
        self.folder_text.value = folder + ("  (hidden from gallery)" if hidden else "")

        in_lib = entry is not None
        self._set_action(self.lib_btn, ft.Icons.FAVORITE if in_lib else ft.Icons.FAVORITE_BORDER, "In library" if in_lib else "Add to library")
        chat_hidden = entry is not None and entry.nomedia
        self._set_action(self.hide_btn, ft.Icons.VISIBILITY if chat_hidden else ft.Icons.VISIBILITY_OFF,
                         "Show" if chat_hidden else "Hide")
        safe_update(self.header)

    def _build_filter_chips(self) -> None:
        self.filter_chips.controls = [
            ft.Chip(label=ft.Text(label), selected=self.filter is f, on_select=lambda e, f=f: self._set_filter(f))
            for f, label in FILTERS
        ]

    def _set_filter(self, f: MediaFilter) -> None:
        if f is self.filter:
            return
        self.filter = f
        entry = self.state.library.get(self.chat_id)
        if entry is not None:
            entry.media_filter = f.value
            self.state.save_library()
        self._build_filter_chips()
        self.app.page.run_task(self.reload)

    def _toggle_library(self, e: ft.Event) -> None:
        st = self.state
        if self.chat_id in st.library:
            st.library.remove(self.chat_id)
            st.save_library()
            self.app.toast("Removed from the library")
        else:
            self.app.library.add_chat(self.chat_id, self.title, self.entity, self.category)
            self.app.toast("Added to the library")
        self.refresh_header()

    def _toggle_hidden(self, e: ft.Event) -> None:
        entry = self.state.library.get(self.chat_id)
        self.app.page.run_task(self.app.set_chat_hidden, self.chat_id, self.title, not (entry is not None and entry.nomedia))

    # ---- loading media --------------------------------------------------------------------------
    async def reload(self) -> None:
        self.entries, self.rows, self.next_offset = [], {}, 0
        self.selected.clear()
        self.list.controls = [self.header, self.empty, self.footer]
        await self.load_more()
        self._mark_seen()

    async def load_more(self) -> None:
        if self.loading or self.closed or self.entity is None:
            return
        self.loading = True
        self.spinner.visible = True
        self.more_btn.visible = False
        safe_update(self.list)
        try:
            client = await self.state.session.ensure_connected()
            page = await fetch_media(client, self.entity, self.filter, offset_id=self.next_offset)
        except LoginError as exc:
            self.app.toast(exc.message, error=True)
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("loading media failed")
            self.app.toast(f"Could not load this chat: {exc}", error=True)
            return
        finally:
            self.loading = False
            self.spinner.visible = False
        if self.closed:
            return
        self.next_offset = page.next_offset
        new_rows = []
        for entry in page.entries:
            if entry.msg_id in self.rows:
                continue
            row = MediaRow(self, entry)
            self.rows[entry.msg_id] = row
            self.entries.append(entry)
            new_rows.append(row)
        at = len(self.list.controls) - 1  # before the footer
        self.list.controls[at:at] = [r.control for r in new_rows]
        self.more_btn.visible = bool(self.next_offset)
        self.empty.visible = not self.entries
        if not self.entries:
            what = {MediaFilter.VIDEO: "videos", MediaFilter.FILE: "files"}.get(self.filter, "media")
            self.empty.content = empty_state(ft.Icons.VIDEO_LIBRARY_OUTLINED, f"No {what} in this chat.")
        self._refresh_rows(new_rows)
        self._update_counts()
        safe_update(self.list)
        self.app.page.run_task(self._load_thumbs, new_rows)

    async def _load_thumbs(self, rows: list[MediaRow]) -> None:
        for row in rows:
            if self.closed:
                return
            if row.entry.message is None:
                continue
            data = await self.app.thumb_for(row.entry.message, self.chat_id)
            if data and not self.closed:
                row.set_thumb(data)
            await asyncio.sleep(0)

    def _mark_seen(self) -> None:
        entry = self.state.library.get(self.chat_id)
        if entry is None or not self.entries:
            return
        newest = max(e.msg_id for e in self.entries)
        changed = entry.unread or newest > entry.last_seen_id
        entry.last_seen_id = max(entry.last_seen_id, newest)
        entry.unread = 0
        if changed:
            self.state.save_library()
            self.app.updates.forget_chat(self.chat_id)
            self.refresh_header()

    # ---- download states ----------------------------------------------------------------------
    def _items_by_msg(self) -> dict[int, DownloadItem]:
        out: dict[int, DownloadItem] = {}
        for item in self.state.manager.items:
            if item.chat_id == self.chat_id and item.msg_id is not None:
                current = out.get(item.msg_id)
                if current is None or item.state in RUNNING or current.state not in RUNNING:
                    out[item.msg_id] = item
        return out

    def status_of(self, entry: MediaEntry, items: dict[int, DownloadItem]) -> tuple[str, DownloadItem | None]:
        item = items.get(entry.msg_id)
        if item is not None:
            if item.state is ItemState.DOWNLOADING or item.state is ItemState.RESOLVING:
                return "downloading", item
            if item.state is ItemState.QUEUED:
                return "queued", item
            if item.state is ItemState.PAUSED:
                return "paused", item
        if self.state.history.downloaded(self.chat_id, entry.msg_id):
            return "done", item
        if item is not None and item.state is ItemState.FAILED:
            return "failed", item
        return "none", item

    def _refresh_rows(self, rows: list[MediaRow] | None = None) -> list[MediaRow]:
        items = self._items_by_msg()
        changed = []
        for row in rows if rows is not None else list(self.rows.values()):
            status, item = self.status_of(row.entry, items)
            if row.set_status(status, item):
                changed.append(row)
        return changed

    def on_downloads_changed(self) -> None:
        """Called by the app ticker while this page is on top."""
        if self.closed:
            return
        for row in self._refresh_rows():
            safe_update(row.control)
        self._update_counts()
        safe_update(self.count_text)

    def _update_counts(self) -> None:
        done = sum(1 for r in self.rows.values() if r.status == "done")
        text = f"{len(self.entries)} shown  -  {done} downloaded"
        if self.selecting:
            text = f"{len(self.selected)} selected of {len(self.entries)}"
        self.count_text.value = text

    # ---- row actions --------------------------------------------------------------------------
    def on_row_click(self, entry: MediaEntry) -> None:
        if self.selecting:
            self.toggle(entry.msg_id)
        else:
            self.show_details(entry)

    def on_row_long_press(self, entry: MediaEntry) -> None:
        if not self.selecting:
            self.start_selecting()
        self.toggle(entry.msg_id)

    def download(self, entries: list[MediaEntry]) -> list[DownloadItem]:
        if self.entity is None:
            self.app.toast("Still opening the chat, try again in a moment")
            return []
        items = self._items_by_msg()
        wanted = [e for e in entries if self.status_of(e, items)[0] in ("none", "failed") and e.message is not None]
        if not wanted:
            self.app.toast("Already downloaded or in the queue")
            return []
        added = self.state.manager.enqueue_telegram([ResolvedMsg(e.message, self.entity, self.title) for e in wanted])
        self._own(added)
        self.app.toast(f"Added {len(added)} to the queue", action="Queue", on_action=lambda ev: self.app.open_queue())
        self.on_downloads_changed()
        return added

    def watch(self, entry: MediaEntry) -> None:
        """Play a video: the saved file when it is downloaded, otherwise streamed from Telegram."""
        if not player.available():
            self.app.toast("The video player is not part of this build", error=True)
            return
        title = entry.caption or entry.name
        record = self.state.history.lookup(self.chat_id, entry.msg_id)
        if record is not None and record.exists:
            page = player.PlayerPage(self.app, title, path=record.path)
        else:
            if entry.message is None or self.entity is None:
                self.app.toast("Still opening the chat, try again in a moment")
                return
            f = getattr(entry.message, "file", None)
            source = StreamSource(entry.message, self.entity, entry.size, mime=getattr(f, "mime_type", None) or "video/mp4",
                                  name=entry.name)
            page = player.PlayerPage(self.app, title, source=source, on_download=lambda: self.download([entry]))
        self.app.page.run_task(page.open)

    def _own(self, items: list[DownloadItem]) -> None:
        """Items queued from this page belong to this chat, whatever Telethon reports for the entity."""
        for item in items:
            item.chat_id = self.chat_id
            item.chat_title = item.chat_title or self.title

    def pause(self, item: DownloadItem | None) -> None:
        if item is not None:
            self.state.manager.pause(item.id)

    def resume(self, item: DownloadItem | None) -> None:
        if item is not None:
            self.state.manager.resume(item.id)

    def cancel(self, item: DownloadItem | None) -> None:
        if item is not None:
            self.state.manager.cancel(item.id)

    def retry(self, item: DownloadItem | None) -> None:
        if item is not None:
            self.state.manager.retry(item.id)

    def show_details(self, entry: MediaEntry) -> None:
        page = self.app.page
        record = self.state.history.lookup(self.chat_id, entry.msg_id)
        status, item = self.status_of(entry, self._items_by_msg())
        buttons: list[ft.Control] = []

        def close_then(fn: Any) -> Any:
            def handler(e: ft.Event) -> None:
                page.pop_dialog()
                fn()
            return handler

        if entry.is_video:
            buttons.append(ft.Button("Watch", icon=ft.Icons.PLAY_ARROW, on_click=close_then(lambda: self.watch(entry))))
        if status in ("none", "failed"):
            buttons.append(ft.Button("Download", icon=ft.Icons.DOWNLOAD, on_click=close_then(lambda: self.download([entry]))))
        if record is not None and record.exists:
            buttons.append(ft.OutlinedButton("Open with...", icon=ft.Icons.OPEN_IN_NEW,
                                             on_click=close_then(lambda: page.run_task(self.app.open_with, record.path))))
            buttons.append(ft.OutlinedButton("Share", icon=ft.Icons.SHARE,
                                             on_click=close_then(lambda: page.run_task(self.app.share_file, record.path))))
            buttons.append(ft.TextButton("Delete file", icon=ft.Icons.DELETE_OUTLINE,
                                         on_click=close_then(lambda: self.app.history.confirm_delete(record))))
        lines: list[ft.Control] = [
            ft.Text(entry.caption or entry.name, size=16, weight=ft.FontWeight.BOLD, selectable=True),
            muted(describe_entry(entry)),
            muted(f"File name: {entry.name}"),
        ]
        if record is not None:
            lines.append(muted(("Saved at " if record.exists else "Was saved at (file is gone) ") + record.path, size=11))
        if item is not None and item.error and status == "failed":
            lines.append(ft.Text(item.error, color=ft.Colors.ERROR, size=12))
        page.show_dialog(ft.BottomSheet(
            content=ft.Container(
                padding=ft.Padding.only(left=16, right=16, top=16, bottom=28),
                content=ft.Column([*lines, ft.Row(buttons, wrap=True, spacing=8)], tight=True, spacing=8),
            ),
        ))

    # ---- selection ----------------------------------------------------------------------------
    def _apply_bar(self) -> None:
        if self.view is None:
            return
        bar = self.view.appbar
        if self.selecting:
            bar.title = ft.Text(f"{len(self.selected)} selected")
            bar.actions = list(self.select_actions)
        else:
            bar.title = ft.Text(self.title)
            bar.actions = list(self.normal_actions)
        self.fab.visible = self.selecting and bool(self.selected)
        self.fab.content = f"Download {len(self.selected)}" if self.selected else "Download"
        safe_update(self.view)

    def start_selecting(self) -> None:
        self.selecting = True
        self._redraw_selection()

    def stop_selecting(self) -> None:
        self.selecting = False
        self.selected.clear()
        self._redraw_selection()

    def toggle(self, msg_id: int) -> None:
        if msg_id in self.selected:
            self.selected.discard(msg_id)
        else:
            self.selected.add(msg_id)
        row = self.rows.get(msg_id)
        if row is not None:
            row.set_selected(True, msg_id in self.selected)
            safe_update(row.control)
        self._update_counts()
        safe_update(self.count_text)
        self._apply_bar()

    def select_all(self) -> None:
        self.selected = {e.msg_id for e in self.entries}
        self._redraw_selection()

    def invert(self) -> None:
        self.selected = {e.msg_id for e in self.entries} - self.selected
        self._redraw_selection()

    def _redraw_selection(self) -> None:
        for msg_id, row in self.rows.items():
            row.set_selected(self.selecting, msg_id in self.selected)
        self._update_counts()
        safe_update(self.list)
        self._apply_bar()

    def download_selected(self) -> None:
        chosen = [e for e in self.entries if e.msg_id in self.selected]
        if not chosen:
            self.app.toast("Select something first")
            return
        self.download(chosen)
        self.stop_selecting()

    def download_loaded(self) -> None:
        if not self.entries:
            self.app.toast("Nothing loaded yet")
            return
        self.download(self.entries)

    # ---- whole channel ---------------------------------------------------------------------------
    async def download_whole_channel(self) -> None:
        if self._scanning or self.entity is None:
            return
        self._scanning = True
        st = self.state
        page = self.app.page
        cancel = asyncio.Event()
        counter = ft.Text("Starting...", size=13)
        wait = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"Scanning {self.title}"[:60]),
            content=ft.Row([ft.ProgressRing(width=22, height=22, stroke_width=3), counter], spacing=14),
            actions=[ft.TextButton("Stop", on_click=lambda e: cancel.set())],
        )

        def progress(scanned: int, found: int, size: int) -> None:
            counter.value = f"Looked at {scanned} messages, found {found} ({format_size(size)})"
            safe_update(wait)

        page.show_dialog(wait)
        try:
            client = await st.session.ensure_connected()
            result = await scan_channel(client, self.entity, self.filter, st.cfg.max_total_size_bytes, on_progress=progress, cancel=cancel)
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

        items = self._items_by_msg()
        fresh = [m for m in result.messages
                 if not st.history.downloaded(self.chat_id, m.id)
                 and not (m.id in items and items[m.id].state in RUNNING)]
        if not fresh:
            self.app.toast("Everything here is already downloaded or queued" if result.messages else "Nothing to download in this chat")
            return
        size = sum((getattr(getattr(m, "file", None), "size", 0) or 0) for m in fresh)
        what = {MediaFilter.VIDEO: "video", MediaFilter.FILE: "file"}.get(self.filter, "item")
        lines: list[ft.Control] = [ft.Text(f"{len(fresh)} {what}{'s' if len(fresh) != 1 else ''} to download, {format_size(size)}")]
        skipped = len(result.messages) - len(fresh)
        if skipped:
            lines.append(muted(f"{skipped} already downloaded or queued.", size=12))
        if result.limit_reached:
            lines.append(muted("Newer files were left out to stay under your size limit (Settings).", size=12))
        if result.cancelled:
            lines.append(muted("The scan was stopped early.", size=12))
        lines.append(muted(f"Saving to {st.folder_for(self.chat_id, self.title)}", size=11))

        def start(e: ft.Event) -> None:
            page.pop_dialog()
            added = st.manager.enqueue_messages(fresh, self.entity, self.title)
            self._own(added)
            self.app.toast(f"Added {len(added)} downloads", action="Queue", on_action=lambda ev: self.app.open_queue())
            self.on_downloads_changed()

        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text(self.title[:60]),
            content=ft.Column(lines, tight=True, spacing=6),
            actions=[ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
                     ft.Button("Download all", icon=ft.Icons.DOWNLOAD, on_click=start)],
        ))
