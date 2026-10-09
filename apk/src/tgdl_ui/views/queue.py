"""Download queue (More > Download queue): live progress, pause/resume, reorder, cancel and retry, and play or open
finished files."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import flet as ft

from tgdl.engine.models import DownloadItem, ItemState
from tgdl.util import format_size, format_speed

from ..widgets import DownloadTile, empty_state, muted, safe_update
from . import player

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger("tgdl.ui.queue")

MAX_QUEUED_SHOWN = 100
MAX_FINISHED_SHOWN = 100


class QueueView:
    def __init__(self, app: "App"):
        self.app = app
        self.tiles: dict[str, DownloadTile] = {}

        self.summary = muted("")
        self.folder = muted("", size=11)
        self.empty = empty_state(ft.Icons.DOWNLOAD_DONE, "No downloads. Pick a channel in Library or Browse.")
        self.list = ft.ListView(expand=True, spacing=0, padding=ft.Padding.only(bottom=88))
        self.clear_btn = ft.TextButton("Clear finished", icon=ft.Icons.DONE_ALL, on_click=self._on_clear)
        self.retry_btn = ft.TextButton("Retry failed", icon=ft.Icons.REPLAY, on_click=self._on_retry)
        self.stop_btn = ft.TextButton("Cancel all", icon=ft.Icons.STOP, on_click=self._on_stop)
        self.pause_btn = ft.IconButton(ft.Icons.PAUSE, tooltip="Pause all", on_click=self._on_toggle_pause)

        self.actions: list[ft.Control] = [
            self.pause_btn,
            ft.PopupMenuButton(icon=ft.Icons.MORE_VERT, items=[
                ft.PopupMenuItem(content="Clear finished", icon=ft.Icons.DONE_ALL, on_click=self._on_clear),
                ft.PopupMenuItem(content="Retry failed", icon=ft.Icons.REPLAY, on_click=self._on_retry),
                ft.PopupMenuItem(content="Cancel all", icon=ft.Icons.STOP, on_click=self._on_stop),
            ]),
        ]

        self.root = ft.Column(
            expand=True,
            spacing=6,
            controls=[
                self.summary,
                self.folder,
                ft.Row([self.clear_btn, self.retry_btn, self.stop_btn], wrap=True, spacing=0, run_spacing=0),
                self.empty,
                self.list,
            ],
        )

    # ---- showing ----------------------------------------------------------------------
    def on_show(self) -> None:
        self._rebuild()
        self._update()

    def on_close(self) -> None:
        pass

    def _update(self) -> None:
        safe_update(self.root)
        safe_update(self.pause_btn)

    # ---- called by the app ticker ---------------------------------------------------------
    def tick(self) -> list[DownloadItem]:
        """Apply pending engine events to the screen. Returns items that just finished."""
        dirty, structure, finished = self.app.state.sink.take()
        self.apply(dirty, structure)
        return finished

    def apply(self, dirty: set[str], structure: bool) -> None:
        if structure:
            self._rebuild()
            self._update()
        elif dirty:
            by_id = {i.id: i for i in self.app.state.manager.items}
            for item_id in dirty:
                tile, item = self.tiles.get(item_id), by_id.get(item_id)
                if tile and item:
                    tile.update_from(item)
                    safe_update(tile.control)
            self._update_summary()
            safe_update(self.summary)

    # ---- building ---------------------------------------------------------------------------
    def _ordered(self) -> tuple[list[DownloadItem], int, int]:
        manager = self.app.state.manager
        items = manager.items
        active = [i for i in items if i.state in (ItemState.DOWNLOADING, ItemState.RESOLVING)]
        queued = manager.waiting()
        listed = set(map(id, queued))
        queued += [i for i in items if i.state is ItemState.QUEUED and id(i) not in listed]
        paused = [i for i in items if i.state is ItemState.PAUSED]
        done = [i for i in reversed(items) if i.state.finished]
        hidden_q = max(0, len(queued) - MAX_QUEUED_SHOWN)
        hidden_d = max(0, len(done) - MAX_FINISHED_SHOWN)
        return active + queued[:MAX_QUEUED_SHOWN] + paused + done[:MAX_FINISHED_SHOWN], hidden_q, hidden_d

    def _rebuild(self) -> None:
        shown, hidden_q, hidden_d = self._ordered()
        controls: list[ft.Control] = []
        keep: dict[str, DownloadTile] = {}
        for item in shown:
            tile = self.tiles.get(item.id) or DownloadTile(item, self._on_tile_action)
            tile.update_from(item)
            keep[item.id] = tile
            controls.append(tile.control)
        if hidden_q:
            controls.append(muted(f"+ {hidden_q} more waiting"))
        if hidden_d:
            controls.append(muted(f"+ {hidden_d} older finished items not shown"))
        self.tiles = keep
        self.list.controls = controls
        self.empty.visible = not shown
        self._update_summary()
        manager = self.app.state.manager
        has_finished = any(i.state.finished for i in manager.items)
        has_failed = any(i.state is ItemState.FAILED for i in manager.items)
        self.clear_btn.visible = has_finished
        self.retry_btn.visible = has_failed
        self.stop_btn.visible = manager.busy or any(i.state is ItemState.PAUSED for i in manager.items)
        self.pause_btn.icon = ft.Icons.PLAY_ARROW if manager.paused else ft.Icons.PAUSE
        self.pause_btn.tooltip = "Resume all" if manager.paused else "Pause all"
        self.folder.value = f"Saving to {manager.downloads_dir}"

    def _update_summary(self) -> None:
        manager = self.app.state.manager
        items = manager.items
        active = [i for i in items if i.state is ItemState.DOWNLOADING]
        queued = sum(1 for i in items if i.state is ItemState.QUEUED)
        paused = sum(1 for i in items if i.state is ItemState.PAUSED)
        done = sum(1 for i in items if i.state in (ItemState.DONE, ItemState.SKIPPED))
        failed = sum(1 for i in items if i.state is ItemState.FAILED)
        speed = sum(i.speed for i in active)
        parts = []
        if manager.paused:
            parts.append("Queue paused")
        if active:
            parts.append(f"{len(active)} downloading ({format_speed(speed)})")
        if queued:
            parts.append(f"{queued} waiting")
        if paused:
            parts.append(f"{paused} paused")
        if done:
            parts.append(f"{done} done")
        if failed:
            parts.append(f"{failed} failed")
        total = sum(i.total for i in items if not i.state.finished)
        if total:
            parts.append(f"{format_size(total)} to go")
        self.summary.value = "  -  ".join(parts) if parts else ""

    # ---- actions -----------------------------------------------------------------------------
    def _on_tile_action(self, action: str, item: DownloadItem) -> None:
        manager = self.app.state.manager
        if action == "cancel":
            manager.cancel(item.id)
        elif action == "retry":
            manager.retry(item.id)
        elif action == "pause":
            manager.pause(item.id)
        elif action == "resume":
            manager.resume(item.id)
        elif action == "top":
            manager.move_to_top(item.id)
        elif action == "play" and item.path:
            if player.available():
                self.app.page.run_task(player.PlayerPage(self.app, item.display_name, path=item.path).open)
            else:
                self.app.page.run_task(self.app.open_with, item.path)
        elif action == "open_with" and item.path:
            self.app.page.run_task(self.app.open_with, item.path)
        elif action == "share" and item.path:
            self.app.page.run_task(self.app.share_file, item.path)
        elif action == "channel" and item.chat_id is not None:
            self.app.open_channel(item.chat_id, item.chat_title or str(item.chat_id), entity=item.peer)

    def _on_toggle_pause(self, e: ft.Event) -> None:
        manager = self.app.state.manager
        if manager.paused:
            manager.resume_all()
            self.app.toast("Downloads resumed")
        else:
            manager.pause_all()
            self.app.toast("Downloads paused. Running files start over when resumed.")
        self._rebuild()
        self._update()

    def _on_clear(self, e: ft.Event) -> None:
        self.app.state.manager.clear_finished()
        self._rebuild()
        self._update()

    def _on_retry(self, e: ft.Event) -> None:
        self.app.state.manager.retry_failed()

    def _on_stop(self, e: ft.Event) -> None:
        manager = self.app.state.manager
        manager.cancel_all()
        manager.paused = False
        self._rebuild()
        self._update()
