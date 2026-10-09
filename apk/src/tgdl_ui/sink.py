"""Collects engine events so the UI can redraw a few times per second instead of on every chunk."""
from __future__ import annotations

from tgdl.engine.models import DownloadItem


class UiSink:
    def __init__(self) -> None:
        self.dirty: set[str] = set()
        self.structure_changed = False
        self.finished_items: list[DownloadItem] = []

    # ProgressSink protocol ------------------------------------------------
    def added(self, item: DownloadItem) -> None:
        self.dirty.add(item.id)
        self.structure_changed = True

    def started(self, item: DownloadItem) -> None:
        self.dirty.add(item.id)
        self.structure_changed = True

    def progress(self, item: DownloadItem) -> None:
        self.dirty.add(item.id)

    def finished(self, item: DownloadItem) -> None:
        self.dirty.add(item.id)
        self.structure_changed = True
        self.finished_items.append(item)

    def log(self, text: str) -> None:
        pass

    # UI side ----------------------------------------------------------------
    def take(self) -> tuple[set[str], bool, list[DownloadItem]]:
        dirty, structure, done = self.dirty, self.structure_changed, self.finished_items
        self.dirty, self.structure_changed, self.finished_items = set(), False, []
        return dirty, structure, done
