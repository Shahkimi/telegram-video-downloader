"""
The download queue.

enqueue_*() adds items and starts them as slots free up (max_concurrent_files at a time).
wait_idle() returns when nothing is queued or running. cancel()/retry()/pause()/resume() work per item,
pause_all()/resume_all() hold the whole queue.
Everything runs on one asyncio event loop; sinks are called on that loop.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from ..config import AppConfig
from ..links.model import ExternalTarget
from ..paths import AppPaths
from ..storage import channel_dir, set_nomedia
from ..telegram.dialogs import peer_id
from ..telegram.resolve import ResolvedMsg, file_size
from ..telegram.session import LoginError, TelegramSession
from ..util import friendly_error, short_id
from .models import DownloadItem, ItemState, NullSink, ProgressSink, Summary, summarize
from .telegram_dl import build_filename, download_message
from .ytdlp_dl import YtdlpDownloader

log = logging.getLogger("tgdl.manager")

ACTIVE = (ItemState.DOWNLOADING, ItemState.RESOLVING)


def chat_title(entity: Any) -> str:
    title = getattr(entity, "title", None)
    return title.strip() if isinstance(title, str) else ""


@dataclass
class Placement:
    folder: str
    nomedia: bool = False   # put a .nomedia marker into folder


class DownloadManager:
    def __init__(self, session: TelegramSession, cfg: AppConfig, paths: AppPaths, sink: ProgressSink | None = None):
        self.session = session
        self.cfg = cfg              # the app replaces this object when settings change
        self.paths = paths
        self.sink: ProgressSink = sink or NullSink()
        self.downloads_dir_override: str | None = None
        self.items: list[DownloadItem] = []
        self.on_busy_change: Callable[[bool], None] | None = None
        # Lets the app send a chat to its own folder (library settings). None = use the default placement.
        self.placer: Callable[[DownloadItem], Placement | None] | None = None
        self.paused = False
        self._pending: deque[DownloadItem] = deque()
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._cancel_flags: dict[str, threading.Event] = {}
        self._reserved: set[str] = set()
        self._interrupt: dict[str, ItemState] = {}   # running items stopped by pause: the state they go back to
        self._busy = False

    # ---- configuration ---------------------------------------------
    @property
    def downloads_dir(self) -> str:
        return self.downloads_dir_override or self.cfg.downloads_path(self.paths)

    @property
    def busy(self) -> bool:
        return bool(self._tasks) or (bool(self._pending) and not self.paused)

    def default_placement(self, item: DownloadItem) -> Placement:
        root = self.downloads_dir
        folder = channel_dir(root, item.chat_title) if (self.cfg.per_channel_folders and item.chat_title) else root
        return Placement(folder)

    def placement(self, item: DownloadItem) -> Placement:
        if self.placer is not None:
            try:
                custom = self.placer(item)
            except Exception:  # noqa: BLE001 - a broken setting must not stop the download
                log.exception("placer failed for %s", item.id)
                custom = None
            if custom is not None:
                return custom
        return self.default_placement(item)

    def _prepare_folder(self, item: DownloadItem) -> str:
        """Decide (once) where item goes and put the .nomedia markers in place."""
        place = self.placement(item)
        folder = item.folder or place.folder
        item.folder = folder
        try:
            os.makedirs(folder, exist_ok=True)
            if self.cfg.nomedia:
                set_nomedia(self.downloads_dir, True)
            if place.nomedia:
                set_nomedia(folder, True)
        except OSError as exc:
            log.warning("cannot prepare %s: %s", folder, exc)
        return folder

    # ---- adding work ------------------------------------------------
    def enqueue_telegram(self, resolved: list[ResolvedMsg]) -> list[DownloadItem]:
        out: list[DownloadItem] = []
        total = len(resolved)
        for i, r in enumerate(resolved, start=1):
            msg = r.message
            name = build_filename(msg)
            item = DownloadItem(
                id=short_id(), kind="telegram", title=name, filename=name,
                total=file_size(msg), index=i, batch_total=total, source=r.source,
                message=msg, peer=r.entity,
                chat_id=peer_id(r.entity), chat_title=chat_title(r.entity),
            )
            out.append(item)
        return self._add(out)

    def enqueue_messages(self, messages: list[Any], entity: Any, source: str = "") -> list[DownloadItem]:
        """Convenience for batch mode: raw Telethon messages from one chat."""
        return self.enqueue_telegram([ResolvedMsg(m, entity, source) for m in messages])

    def enqueue_external(self, targets: list[ExternalTarget]) -> list[DownloadItem]:
        out: list[DownloadItem] = []
        total = len(targets)
        for i, t in enumerate(targets, start=1):
            out.append(DownloadItem(
                id=short_id(), kind="ytdlp", title="", filename="", index=i, batch_total=total,
                source=t.source, url=t.url,
            ))
        return self._add(out)

    def _add(self, items: list[DownloadItem]) -> list[DownloadItem]:
        for item in items:
            self.items.append(item)
            self._pending.append(item)
            self.sink.added(item)
        self._pump()
        return items

    # ---- scheduling -------------------------------------------------
    def _pump(self) -> None:
        loop = asyncio.get_running_loop()
        limit = max(1, self.cfg.max_concurrent_files)
        while not self.paused and self._pending and len(self._tasks) < limit:
            item = self._pending.popleft()
            if item.state is not ItemState.QUEUED:
                continue
            task = loop.create_task(self._run_item(item), name=f"download-{item.id}")
            self._tasks[item.id] = task
            task.add_done_callback(lambda t, item_id=item.id: self._on_done(item_id))
        self._notify_busy()

    def _on_done(self, item_id: str) -> None:
        self._tasks.pop(item_id, None)
        self._cancel_flags.pop(item_id, None)
        self._interrupt.pop(item_id, None)
        try:
            self._pump()
        except RuntimeError:  # loop is closing
            pass

    def _notify_busy(self) -> None:
        busy = self.busy
        if busy != self._busy:
            self._busy = busy
            if self.on_busy_change:
                try:
                    self.on_busy_change(busy)
                except Exception:  # noqa: BLE001
                    log.exception("on_busy_change failed")

    async def _run_item(self, item: DownloadItem) -> None:
        try:
            folder = self._prepare_folder(item)
            if item.kind == "telegram":
                try:
                    client = await self.session.ensure_connected()
                except LoginError as exc:
                    item.state, item.error, item.finished_at = ItemState.FAILED, exc.message, time.time()
                    self.sink.finished(item)
                    return
                await download_message(
                    client, item, folder, self.sink,
                    workers=self.cfg.parallel_connections,
                    chunk_kb=self.cfg.chunk_size_kb,
                    skip_existing=self.cfg.skip_existing,
                    reserved=self._reserved,
                )
            else:
                flag = threading.Event()
                self._cancel_flags[item.id] = flag
                cache = self.paths.cache_dir
                dl = YtdlpDownloader(
                    folder, cache,
                    max_height=self.cfg.ytdlp.max_height,
                    cookies_file=self.cfg.ytdlp.cookies_file,
                )
                await dl.download(item, self.sink, flag)
        except asyncio.CancelledError:
            back_to = self._interrupt.pop(item.id, None)
            if back_to is not None:
                self._park(item, back_to)
            elif not item.state.finished:
                item.state, item.finished_at = ItemState.CANCELLED, time.time()
                self.sink.finished(item)
        except Exception as exc:  # noqa: BLE001 - a bug in one item must not stop the queue
            log.exception("unexpected error while downloading %s", item.id)
            item.state, item.error, item.finished_at = ItemState.FAILED, friendly_error(exc), time.time()
            self.sink.finished(item)

    def _park(self, item: DownloadItem, state: ItemState) -> None:
        """A running item was interrupted by pause: forget the partial file and wait (Telegram has no resume)."""
        item.state, item.error, item.finished_at = state, None, None
        item.done, item.speed = 0, 0.0
        if state is ItemState.QUEUED:
            self._pending.appendleft(item)
        self.sink.added(item)

    def _find(self, item_id: str) -> DownloadItem | None:
        return next((i for i in self.items if i.id == item_id), None)

    def _interrupt_running(self, item: DownloadItem, back_to: ItemState) -> bool:
        task = self._tasks.get(item.id)
        if task is None:
            return False
        self._interrupt[item.id] = back_to
        flag = self._cancel_flags.get(item.id)
        if flag:
            flag.set()
        task.cancel()
        return True

    # ---- control ----------------------------------------------------
    def cancel(self, item_id: str) -> None:
        item = self._find(item_id)
        if item is None or item.state.finished:
            return
        self._interrupt.pop(item_id, None)
        flag = self._cancel_flags.get(item_id)
        if flag:
            flag.set()
        task = self._tasks.get(item_id)
        if task:
            task.cancel()
            return
        if item in self._pending:
            self._pending.remove(item)
        item.state, item.finished_at = ItemState.CANCELLED, time.time()
        self.sink.finished(item)
        self._notify_busy()

    def pause(self, item_id: str) -> None:
        """Hold one item back. A running download stops and starts over on resume()."""
        item = self._find(item_id)
        if item is None or item.state.finished or item.state is ItemState.PAUSED:
            return
        if self._interrupt_running(item, ItemState.PAUSED):
            return
        if item in self._pending:
            self._pending.remove(item)
        item.state = ItemState.PAUSED
        self.sink.added(item)
        self._notify_busy()

    def resume(self, item_id: str) -> None:
        item = self._find(item_id)
        if item is None or item.state is not ItemState.PAUSED:
            return
        item.state = ItemState.QUEUED
        self._pending.append(item)
        self.sink.added(item)
        self._pump()

    def pause_all(self) -> None:
        """Stop starting new downloads and put the running ones back at the front of the queue."""
        self.paused = True
        for item in list(self.items):
            if item.state in ACTIVE:
                self._interrupt_running(item, ItemState.QUEUED)
        self._notify_busy()

    def resume_all(self) -> None:
        self.paused = False
        for item in self.items:
            if item.state is ItemState.PAUSED:
                item.state = ItemState.QUEUED
                self._pending.append(item)
                self.sink.added(item)
        self._pump()

    def move_to_top(self, item_id: str) -> None:
        item = self._find(item_id)
        if item is None or item not in self._pending:
            return
        self._pending.remove(item)
        self._pending.appendleft(item)
        self.items.remove(item)
        first_waiting = next((n for n, i in enumerate(self.items) if i.state is ItemState.QUEUED), len(self.items))
        self.items.insert(first_waiting, item)
        self.sink.added(item)

    def waiting(self) -> list[DownloadItem]:
        """Queued items in the order they will start."""
        return [i for i in self._pending if i.state is ItemState.QUEUED]

    def cancel_all(self) -> None:
        for item in list(self.items):
            self.cancel(item.id)

    def retry(self, item_id: str) -> None:
        item = next((i for i in self.items if i.id == item_id), None)
        if item is None or item.state not in (ItemState.FAILED, ItemState.CANCELLED):
            return
        item.state, item.error, item.done, item.speed = ItemState.QUEUED, None, 0, 0.0
        item.finished_at = None
        self._pending.append(item)
        self.sink.added(item)
        self._pump()

    def retry_failed(self) -> int:
        failed = [i for i in self.items if i.state is ItemState.FAILED]
        for item in failed:
            self.retry(item.id)
        return len(failed)

    def clear_finished(self) -> None:
        self.items = [i for i in self.items if not i.state.finished]

    async def wait_idle(self) -> None:
        """Returns when nothing runs and nothing is waiting (a paused queue counts as idle)."""
        while self._tasks or (self._pending and not self.paused):
            if self._tasks:
                await asyncio.wait(list(self._tasks.values()))
            else:
                await asyncio.sleep(0)

    def summary(self, items: list[DownloadItem] | None = None) -> Summary:
        return summarize(self.items if items is None else items)
