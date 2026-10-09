"""
The download queue.

enqueue_*() adds items and starts them as slots free up (max_concurrent_files at a time).
wait_idle() returns when nothing is queued or running. cancel()/retry() work per item.
Everything runs on one asyncio event loop; sinks are called on that loop.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from typing import Any, Callable

from ..config import AppConfig
from ..links.model import ExternalTarget
from ..paths import AppPaths
from ..telegram.resolve import ResolvedMsg, file_size
from ..telegram.session import LoginError, TelegramSession
from ..util import friendly_error, short_id
from .models import DownloadItem, ItemState, NullSink, ProgressSink, Summary, summarize
from .telegram_dl import build_filename, download_message
from .ytdlp_dl import YtdlpDownloader

log = logging.getLogger("tgdl.manager")


class DownloadManager:
    def __init__(self, session: TelegramSession, cfg: AppConfig, paths: AppPaths, sink: ProgressSink | None = None):
        self.session = session
        self.cfg = cfg              # the app replaces this object when settings change
        self.paths = paths
        self.sink: ProgressSink = sink or NullSink()
        self.downloads_dir_override: str | None = None
        self.items: list[DownloadItem] = []
        self.on_busy_change: Callable[[bool], None] | None = None
        self._pending: deque[DownloadItem] = deque()
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._cancel_flags: dict[str, threading.Event] = {}
        self._reserved: set[str] = set()
        self._busy = False

    # ---- configuration ---------------------------------------------
    @property
    def downloads_dir(self) -> str:
        return self.downloads_dir_override or self.cfg.downloads_path(self.paths)

    @property
    def busy(self) -> bool:
        return bool(self._tasks or self._pending)

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
        while self._pending and len(self._tasks) < limit:
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
            if item.kind == "telegram":
                try:
                    client = await self.session.ensure_connected()
                except LoginError as exc:
                    item.state, item.error, item.finished_at = ItemState.FAILED, exc.message, time.time()
                    self.sink.finished(item)
                    return
                await download_message(
                    client, item, self.downloads_dir, self.sink,
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
                    self.downloads_dir, cache,
                    max_height=self.cfg.ytdlp.max_height,
                    cookies_file=self.cfg.ytdlp.cookies_file,
                )
                await dl.download(item, self.sink, flag)
        except asyncio.CancelledError:
            if not item.state.finished:
                item.state, item.finished_at = ItemState.CANCELLED, time.time()
                self.sink.finished(item)
        except Exception as exc:  # noqa: BLE001 - a bug in one item must not stop the queue
            log.exception("unexpected error while downloading %s", item.id)
            item.state, item.error, item.finished_at = ItemState.FAILED, friendly_error(exc), time.time()
            self.sink.finished(item)

    # ---- control ----------------------------------------------------
    def cancel(self, item_id: str) -> None:
        item = next((i for i in self.items if i.id == item_id), None)
        if item is None or item.state.finished:
            return
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
        while self._tasks or self._pending:
            if self._tasks:
                await asyncio.wait(list(self._tasks.values()))
            else:
                await asyncio.sleep(0)

    def summary(self, items: list[DownloadItem] | None = None) -> Summary:
        return summarize(self.items if items is None else items)
