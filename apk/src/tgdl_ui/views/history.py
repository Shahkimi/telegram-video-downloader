"""History tab: every finished download, newest first, grouped by day. Share, delete or jump to the chat."""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import TYPE_CHECKING

import flet as ft

from tgdl.library import HistoryRecord
from tgdl.telegram.browse import thumb_path
from tgdl.util import format_size

from ..widgets import empty_state, muted, safe_update
from .channel import relative_day

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger("tgdl.ui.history")

MAX_ROWS = 400
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".ts", ".3gp"}


class HistoryView:
    def __init__(self, app: "App"):
        self.app = app
        self.search = ft.TextField(hint_text="Search history", prefix_icon=ft.Icons.SEARCH, dense=True, visible=False,
                                   on_change=lambda e: self.render())
        self.appbar = ft.AppBar(
            title=ft.Text("History"),
            actions=[
                ft.IconButton(ft.Icons.SEARCH, tooltip="Search", on_click=self._toggle_search),
                ft.PopupMenuButton(icon=ft.Icons.MORE_VERT, items=[
                    ft.PopupMenuItem(content="Forget missing files", icon=ft.Icons.CLEANING_SERVICES, on_click=lambda e: self._forget_missing()),
                    ft.PopupMenuItem(content="Clear history", icon=ft.Icons.DELETE_SWEEP, on_click=lambda e: self._confirm_clear()),
                ]),
            ],
        )
        self.list = ft.ListView(expand=True, spacing=0, padding=ft.Padding.only(bottom=24))
        self.root = ft.Column(expand=True, spacing=6, controls=[self.search, self.list])

    def on_show(self) -> None:
        self.render()

    def _toggle_search(self, e: ft.Event) -> None:
        self.search.visible = not self.search.visible
        if not self.search.visible:
            self.search.value = ""
        self.render()

    def _records(self) -> list[HistoryRecord]:
        records = self.app.state.history.records
        needle = (self.search.value or "").strip().lower() if self.search.visible else ""
        if needle:
            records = [r for r in records if needle in r.filename.lower() or needle in r.chat_title.lower()]
        return records

    def render(self) -> None:
        records = self._records()
        if not records:
            text = "Nothing matches." if self.search.visible and self.search.value else "Finished downloads show up here."
            self.list.controls = [empty_state(ft.Icons.HISTORY, text)]
            safe_update(self.root)
            return
        rows: list[ft.Control] = []
        day = None
        for record in records[:MAX_ROWS]:
            label = relative_day(datetime.fromtimestamp(record.finished_at).astimezone())
            if label != day:
                day = label
                rows.append(ft.Container(ft.Text(label, size=13, weight=ft.FontWeight.BOLD, color=ft.Colors.PRIMARY),
                                         padding=ft.Padding.only(top=12, bottom=4)))
            rows.append(self._row(record))
        if len(records) > MAX_ROWS:
            rows.append(muted(f"+ {len(records) - MAX_ROWS} older downloads. Search to find them."))
        self.list.controls = rows
        safe_update(self.root)

    def _thumb(self, record: HistoryRecord) -> ft.Control:
        is_video = os.path.splitext(record.filename)[1].lower() in VIDEO_EXT
        content: ft.Control = ft.Icon(ft.Icons.MOVIE if is_video else ft.Icons.INSERT_DRIVE_FILE, color=ft.Colors.ON_SURFACE_VARIANT)
        if record.msg_id is not None:
            path = thumb_path(self.app.state.paths.cache_dir, record.chat_id, record.msg_id)
            try:
                data = path.read_bytes()
                if data:
                    content = ft.Image(src=data, fit=ft.BoxFit.COVER, width=64, height=48)
            except OSError:
                pass
        return ft.Container(content=content, width=64, height=48, border_radius=6, alignment=ft.Alignment.CENTER,
                            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST, clip_behavior=ft.ClipBehavior.ANTI_ALIAS)

    def _row(self, record: HistoryRecord) -> ft.Control:
        exists = record.exists
        when = datetime.fromtimestamp(record.finished_at).strftime("%H:%M")
        meta = "  -  ".join(p for p in (record.chat_title or (record.url or ""), format_size(record.size) if record.size else "", when) if p)
        items = []
        if exists:
            items.append(ft.PopupMenuItem(content="Share", icon=ft.Icons.SHARE,
                                          on_click=lambda e: self.app.page.run_task(self.app.share_file, record.path)))
        if record.chat_id is not None:
            items.append(ft.PopupMenuItem(content="Open chat", icon=ft.Icons.FORUM,
                                          on_click=lambda e: self.app.open_channel(record.chat_id, record.chat_title or str(record.chat_id))))
        if exists:
            items.append(ft.PopupMenuItem(content="Delete file", icon=ft.Icons.DELETE_OUTLINE, on_click=lambda e: self.confirm_delete(record)))
        items.append(ft.PopupMenuItem(content="Remove from history", icon=ft.Icons.REMOVE_CIRCLE_OUTLINE, on_click=lambda e: self._forget(record)))
        return ft.Container(
            padding=ft.Padding.symmetric(vertical=6),
            content=ft.Row(spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                self._thumb(record),
                ft.Column(spacing=2, expand=True, controls=[
                    ft.Text(record.filename, size=14, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS,
                            color=None if exists else ft.Colors.ON_SURFACE_VARIANT),
                    muted(meta if exists else "File no longer on the phone  -  " + meta, size=11, max_lines=1),
                ]),
                ft.PopupMenuButton(icon=ft.Icons.MORE_VERT, items=items),
            ]),
        )

    # ---- actions ------------------------------------------------------------------------------
    def confirm_delete(self, record: HistoryRecord) -> None:
        page = self.app.page

        def delete(e: ft.Event) -> None:
            page.pop_dialog()
            try:
                os.remove(record.path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                self.app.toast(f"Cannot delete: {exc}", error=True)
                return
            self.app.state.history.remove(record.path)
            self.app.state.save_history()
            self.app.page.run_task(self.app.native.scan_files, [record.path])  # drop it from galleries too
            self.app.toast("File deleted")
            self.render()
            owner = self.app.top_owner()
            if hasattr(owner, "on_downloads_changed"):
                owner.on_downloads_changed()

        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text("Delete file?"),
            content=ft.Text(f"{record.filename}\n\nThis removes the file from the phone.", size=13),
            actions=[ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
                     ft.Button("Delete", icon=ft.Icons.DELETE, on_click=delete)],
        ))

    def _forget(self, record: HistoryRecord) -> None:
        self.app.state.history.remove(record.path)
        self.app.state.save_history()
        self.render()

    def _forget_missing(self) -> None:
        dropped = self.app.state.history.remove_missing()
        self.app.state.save_history()
        self.app.toast(f"Removed {dropped} missing file(s) from the history")
        self.render()

    def _confirm_clear(self) -> None:
        page = self.app.page

        def clear(e: ft.Event) -> None:
            page.pop_dialog()
            self.app.state.history.clear()
            self.app.state.save_history()
            self.render()
            self.app.toast("History cleared. Your files are still on the phone.")

        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text("Clear history?"),
            content=ft.Text("Only the list is cleared. Downloaded files stay on the phone, but chats will no longer mark them as downloaded.",
                            size=13),
            actions=[ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()), ft.Button("Clear", on_click=clear)],
        ))
