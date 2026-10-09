"""Data types the download engine shares with the UIs."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


class ItemState(str, Enum):
    QUEUED = "queued"
    PAUSED = "paused"                   # held back by the user; resume() puts it back in the queue
    RESOLVING = "resolving"
    DOWNLOADING = "downloading"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def finished(self) -> bool:
        return self in (ItemState.DONE, ItemState.SKIPPED, ItemState.FAILED, ItemState.CANCELLED)


@dataclass
class DownloadItem:
    id: str
    kind: str                           # "telegram" | "ytdlp"
    title: str
    filename: str = ""
    total: int = 0
    done: int = 0
    speed: float = 0.0
    state: ItemState = ItemState.QUEUED
    error: str | None = None
    path: str | None = None
    index: int = 0                      # position inside its batch, 1-based
    batch_total: int = 0
    source: str = ""                    # the link or channel it came from
    message: Any = None                 # telegram: the Telethon message
    peer: Any = None                    # telegram: the chat entity
    chat_id: int | None = None          # telegram: marked peer id (-100... for channels)
    chat_title: str = ""                # telegram: chat name, used for the per-channel folder
    subfolder: str = ""                 # user-chosen folder inside the chat's folder; "" = the chat's folder itself
    folder: str | None = None           # where the file goes; None = decided by the manager when it starts
    url: str | None = None              # ytdlp
    started_at: float | None = None
    finished_at: float | None = None

    @property
    def percent(self) -> float:
        return min(100.0, self.done * 100.0 / self.total) if self.total else 0.0

    @property
    def eta(self) -> float | None:
        if self.speed > 0 and self.total > self.done:
            return (self.total - self.done) / self.speed
        return None

    @property
    def display_name(self) -> str:
        return self.filename or self.title or self.url or self.id

    @property
    def msg_id(self) -> int | None:
        return getattr(self.message, "id", None)


@dataclass
class Summary:
    total: int = 0
    done: int = 0
    skipped: int = 0
    failed: int = 0
    cancelled: int = 0
    bytes_downloaded: int = 0

    @property
    def succeeded(self) -> int:
        return self.done + self.skipped


class ProgressSink(Protocol):
    """Receives engine events. Called on the event loop thread; keep it cheap."""

    def added(self, item: DownloadItem) -> None: ...
    def started(self, item: DownloadItem) -> None: ...
    def progress(self, item: DownloadItem) -> None: ...
    def finished(self, item: DownloadItem) -> None: ...
    def log(self, text: str) -> None: ...


class NullSink:
    def added(self, item: DownloadItem) -> None: ...
    def started(self, item: DownloadItem) -> None: ...
    def progress(self, item: DownloadItem) -> None: ...
    def finished(self, item: DownloadItem) -> None: ...
    def log(self, text: str) -> None: ...


class SpeedTracker:
    """Smoothed bytes/second from cumulative byte counts."""

    def __init__(self) -> None:
        self._last_t = time.monotonic()
        self._last_b = 0
        self.speed = 0.0

    def update(self, done: int) -> float:
        now = time.monotonic()
        dt = now - self._last_t
        if dt >= 0.5:
            inst = max(0, done - self._last_b) / dt
            self.speed = inst if self.speed == 0 else 0.7 * self.speed + 0.3 * inst
            self._last_t, self._last_b = now, done
        return self.speed


def summarize(items: list[DownloadItem]) -> Summary:
    s = Summary(total=len(items))
    for it in items:
        if it.state is ItemState.DONE:
            s.done += 1
            s.bytes_downloaded += it.done
        elif it.state is ItemState.SKIPPED:
            s.skipped += 1
        elif it.state is ItemState.FAILED:
            s.failed += 1
        elif it.state is ItemState.CANCELLED:
            s.cancelled += 1
    return s
