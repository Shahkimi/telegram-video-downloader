"""Queue tab: live progress for every download."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import flet as ft

from tgdl.engine.models import DownloadItem, ItemState
from tgdl.util import format_size, format_speed

from ..widgets import DownloadTile, muted, title

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger("tgdl.ui.queue")

MAX_QUEUED_SHOWN = 100
MAX_FINISHED_SHOWN = 100


class QueueView:
    def __init__(self, app: "App"):
        self.app = app
        self.tiles: dict[str, DownloadTile] = {}
        self._signature: tuple[str, ...] = ()

        self.summary = muted("")
        self.empty = muted("Nothing yet. Add links on the Download tab.", size=14)
        self.list = ft.ListView(expand=True, spacing=4, padding=ft.Padding.only(bottom=12))
        self.clear_btn = ft.TextButton("Clear finished", icon=ft.Icons.DONE_ALL, on_click=self._on_clear)
        self.retry_btn = ft.TextButton("Retry failed", icon=ft.Icons.REPLAY, on_click=self._on_retry)
        self.stop_btn = ft.TextButton("Cancel all", icon=ft.Icons.STOP, on_click=self._on_stop)

        self.root = ft.Column(
            expand=True,
            spacing=8,
            controls=[
                title("Queue"),
                self.summary,
                ft.Row([self.clear_btn, self.retry_btn, self.stop_btn], wrap=True, spacing=0, run_spacing=0),
                self.empty,
                self.list,
            ],
        )

    # ---- showing ----------------------------------------------------------------------
    def on_show(self) -> None:
        self._rebuild()
        self._update()

    def _update(self) -> None:
        try:
            self.root.update()
        except Exception:  # noqa: BLE001
            pass

    # ---- called by the app ticker ---------------------------------------------------------
    def tick(self) -> list[DownloadItem]:
        """Apply pending engine events to the screen. Returns items that just finished."""
        dirty, structure, finished = self.app.state.sink.take()
        if structure:
            self._rebuild()
            self._update()
        elif dirty:
            by_id = {i.id: i for i in self.app.state.manager.items}
            for item_id in dirty:
                tile, item = self.tiles.get(item_id), by_id.get(item_id)
                if tile and item:
                    tile.update_from(item)
                    try:
                        tile.control.update()
                    except Exception:  # noqa: BLE001
                        pass
            self._update_summary()
            try:
                self.summary.update()
            except Exception:  # noqa: BLE001
                pass
        return finished

    # ---- building ---------------------------------------------------------------------------
    def _ordered(self) -> tuple[list[DownloadItem], int, int]:
        items = self.app.state.manager.items
        active = [i for i in items if i.state in (ItemState.DOWNLOADING, ItemState.RESOLVING)]
        queued = [i for i in items if i.state is ItemState.QUEUED]
        done = [i for i in reversed(items) if i.state.finished]
        hidden_q = max(0, len(queued) - MAX_QUEUED_SHOWN)
        hidden_d = max(0, len(done) - MAX_FINISHED_SHOWN)
        return active + queued[:MAX_QUEUED_SHOWN] + done[:MAX_FINISHED_SHOWN], hidden_q, hidden_d

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
        has_finished = any(i.state.finished for i in self.app.state.manager.items)
        has_failed = any(i.state is ItemState.FAILED for i in self.app.state.manager.items)
        self.clear_btn.visible = has_finished
        self.retry_btn.visible = has_failed
        self.stop_btn.visible = self.app.state.manager.busy

    def _update_summary(self) -> None:
        items = self.app.state.manager.items
        active = [i for i in items if i.state is ItemState.DOWNLOADING]
        queued = sum(1 for i in items if i.state is ItemState.QUEUED)
        done = sum(1 for i in items if i.state in (ItemState.DONE, ItemState.SKIPPED))
        failed = sum(1 for i in items if i.state is ItemState.FAILED)
        speed = sum(i.speed for i in active)
        parts = []
        if active:
            parts.append(f"{len(active)} downloading ({format_speed(speed)})")
        if queued:
            parts.append(f"{queued} waiting")
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
        elif action == "share" and item.path:
            self.app.page.run_task(self.app.share_file, item.path)

    def _on_clear(self, e: ft.Event) -> None:
        self.app.state.manager.clear_finished()
        self._rebuild()
        self._update()

    def _on_retry(self, e: ft.Event) -> None:
        self.app.state.manager.retry_failed()

    def _on_stop(self, e: ft.Event) -> None:
        self.app.state.manager.cancel_all()
