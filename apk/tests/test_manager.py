import asyncio
import os
import time

import pytest

from tests.conftest import FakeMessage
from tgdl.config import AppConfig
from tgdl.engine import manager as manager_mod
from tgdl.engine import telegram_dl
from tgdl.engine.manager import DownloadManager
from tgdl.engine.models import DownloadItem, ItemState, NullSink
from tgdl.links.model import ExternalTarget
from tgdl.telegram.resolve import ResolvedMsg
from tgdl.telegram.session import LoginError


class RecordingSink(NullSink):
    def __init__(self):
        self.events = []

    def added(self, item):
        self.events.append(("added", item.id))

    def started(self, item):
        self.events.append(("started", item.id))

    def finished(self, item):
        self.events.append(("finished", item.id, item.state.value))


class FakeSession:
    def __init__(self, client="client", error=None):
        self.client, self.error = client, error

    async def ensure_connected(self):
        if self.error:
            raise self.error
        return self.client


def make(paths, sink=None, session=None, **cfg_kw):
    cfg = AppConfig(**cfg_kw)
    return DownloadManager(session or FakeSession(), cfg, paths, sink or RecordingSink())


def resolved(n, size=100):
    return [ResolvedMsg(FakeMessage(id=i, text=f"video {i}", size=size), "entity", "src") for i in range(1, n + 1)]


async def test_concurrency_limit_and_wait_idle(paths, monkeypatch):
    running, peak = 0, 0

    async def fake_download(client, item, directory, sink, **kw):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        item.state = ItemState.DOWNLOADING
        sink.started(item)
        await asyncio.sleep(0.02)
        running -= 1
        item.state = ItemState.DONE
        sink.finished(item)
        return True

    monkeypatch.setattr(manager_mod, "download_message", fake_download)
    mgr = make(paths, max_concurrent_files=2)
    busy = []
    mgr.on_busy_change = busy.append
    items = mgr.enqueue_telegram(resolved(5))
    assert [i.index for i in items] == [1, 2, 3, 4, 5] and items[0].batch_total == 5
    await mgr.wait_idle()
    assert peak == 2
    assert all(i.state is ItemState.DONE for i in items)
    assert busy == [True, False]
    s = mgr.summary()
    assert (s.total, s.done, s.failed) == (5, 5, 0)


async def test_cancel_running_and_queued(paths, monkeypatch):
    async def slow(client, item, directory, sink, **kw):
        item.state = ItemState.DOWNLOADING
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            item.state = ItemState.CANCELLED
            sink.finished(item)
            raise

    monkeypatch.setattr(manager_mod, "download_message", slow)
    sink = RecordingSink()
    mgr = make(paths, sink, max_concurrent_files=1)
    a, b = mgr.enqueue_telegram(resolved(2))
    await asyncio.sleep(0.01)
    assert a.state is ItemState.DOWNLOADING and b.state is ItemState.QUEUED
    mgr.cancel(b.id)
    assert b.state is ItemState.CANCELLED
    mgr.cancel(a.id)
    await asyncio.wait_for(mgr.wait_idle(), 2)
    assert a.state is ItemState.CANCELLED
    assert not mgr.busy


async def test_failure_does_not_stop_queue_and_retry_works(paths, monkeypatch):
    attempts = {}

    async def flaky(client, item, directory, sink, **kw):
        attempts[item.id] = attempts.get(item.id, 0) + 1
        if item.index == 1 and attempts[item.id] == 1:
            item.state, item.error = ItemState.FAILED, "boom"
            sink.finished(item)
            return False
        item.state = ItemState.DONE
        sink.finished(item)
        return True

    monkeypatch.setattr(manager_mod, "download_message", flaky)
    mgr = make(paths)
    first, second = mgr.enqueue_telegram(resolved(2))
    await mgr.wait_idle()
    assert (first.state, second.state) == (ItemState.FAILED, ItemState.DONE)
    assert mgr.retry_failed() == 1
    await mgr.wait_idle()
    assert first.state is ItemState.DONE and first.error is None
    mgr.clear_finished()
    assert mgr.items == []


async def test_unexpected_exception_is_contained(paths, monkeypatch):
    async def broken(client, item, directory, sink, **kw):
        raise RuntimeError("kaput")

    monkeypatch.setattr(manager_mod, "download_message", broken)
    mgr = make(paths)
    (item,) = mgr.enqueue_telegram(resolved(1))
    await mgr.wait_idle()
    assert item.state is ItemState.FAILED and "kaput" in item.error


async def test_not_logged_in_fails_items_with_message(paths):
    mgr = make(paths, session=FakeSession(error=LoginError("not_logged_in", "Log in first.")))
    (item,) = mgr.enqueue_telegram(resolved(1))
    await mgr.wait_idle()
    assert item.state is ItemState.FAILED and item.error == "Log in first."


async def test_external_items_use_ytdlp_downloader(paths, monkeypatch):
    seen = {}

    class FakeYt:
        def __init__(self, directory, cache, **kw):
            seen["dir"], seen["kw"] = directory, kw

        async def download(self, item, sink, flag):
            seen["url"] = item.url
            item.state = ItemState.DONE
            sink.finished(item)
            return True

    monkeypatch.setattr(manager_mod, "YtdlpDownloader", FakeYt)
    mgr = make(paths)
    mgr.downloads_dir_override = str(paths.default_downloads_dir)
    (item,) = mgr.enqueue_external([ExternalTarget("https://example.com/v", "https://example.com/v", "builtin:ytdlp")])
    await mgr.wait_idle()
    assert item.kind == "ytdlp" and seen["url"] == "https://example.com/v"
    assert seen["kw"]["max_height"] == 1080


_real_sleep = asyncio.sleep


def _instant_sleep(_seconds):
    return _real_sleep(0)


# ---------------------------------------------------------------------
# download_message with a fake fast_download_media
# ---------------------------------------------------------------------

def fake_item(msg, total=None):
    return DownloadItem(id="x", kind="telegram", title="t", total=msg.file.size if total is None else total, message=msg, peer="peer")


class FakeTgClient:
    async def get_messages(self, peer, ids=None):
        return None


async def test_download_message_success_uses_part_file(tmp_path, monkeypatch):
    msg = FakeMessage(id=9, text="Hello", size=5)
    seen = {}

    async def fake_fast(client, message, peer, output_path, progress_cb=None, **kw):
        seen["part"], seen["kw"] = output_path, kw
        with open(output_path, "wb") as fh:
            fh.write(b"12345")
        progress_cb(5, 5)
        return output_path

    monkeypatch.setattr(telegram_dl, "fast_download_media", fake_fast)
    sink = RecordingSink()
    item = fake_item(msg)
    ok = await telegram_dl.download_message(FakeTgClient(), item, str(tmp_path), sink, workers=6, chunk_kb=256)
    assert ok and item.state is ItemState.DONE
    assert seen["part"].endswith("Hello.mp4.part") and seen["kw"] == {"parallel_workers": 6, "chunk_size": 256 * 1024}
    assert (tmp_path / "Hello.mp4").read_bytes() == b"12345" and not (tmp_path / "Hello.mp4.part").exists()
    assert [e[0] for e in sink.events] == ["started", "finished"]


async def test_download_message_skips_existing(tmp_path, monkeypatch):
    (tmp_path / "Hello.mp4").write_bytes(b"12345")
    called = []

    async def fake_fast(*a, **kw):
        called.append(1)

    monkeypatch.setattr(telegram_dl, "fast_download_media", fake_fast)
    item = fake_item(FakeMessage(id=9, text="Hello", size=5))
    assert await telegram_dl.download_message(FakeTgClient(), item, str(tmp_path), NullSink())
    assert item.state is ItemState.SKIPPED and not called


async def test_download_message_retries_then_fails_and_cleans_part(tmp_path, monkeypatch):
    calls = []

    async def fake_fast(client, message, peer, output_path, **kw):
        calls.append(output_path)
        with open(output_path, "wb") as fh:
            fh.write(b"partial")
        raise IOError("network down")

    monkeypatch.setattr(telegram_dl, "fast_download_media", fake_fast)
    monkeypatch.setattr(telegram_dl.asyncio, "sleep", _instant_sleep)
    item = fake_item(FakeMessage(id=9, text="Hello", size=5))
    ok = await telegram_dl.download_message(FakeTgClient(), item, str(tmp_path), NullSink(), max_retries=3)
    assert not ok and item.state is ItemState.FAILED and "network down" in item.error
    assert len(calls) == 3
    assert os.listdir(tmp_path) == []


async def test_download_message_recovers_on_second_attempt(tmp_path, monkeypatch):
    attempts = []

    async def fake_fast(client, message, peer, output_path, progress_cb=None, **kw):
        attempts.append(1)
        if len(attempts) == 1:
            raise IOError("hiccup")
        with open(output_path, "wb") as fh:
            fh.write(b"12345")
        return output_path

    monkeypatch.setattr(telegram_dl, "fast_download_media", fake_fast)
    monkeypatch.setattr(telegram_dl.asyncio, "sleep", _instant_sleep)
    item = fake_item(FakeMessage(id=9, text="Hello", size=5))
    assert await telegram_dl.download_message(FakeTgClient(), item, str(tmp_path), NullSink())
    assert item.state is ItemState.DONE and len(attempts) == 2


async def test_download_message_cancel_removes_part(tmp_path, monkeypatch):
    async def fake_fast(client, message, peer, output_path, **kw):
        with open(output_path, "wb") as fh:
            fh.write(b"half")
        await asyncio.sleep(10)

    monkeypatch.setattr(telegram_dl, "fast_download_media", fake_fast)
    item = fake_item(FakeMessage(id=9, text="Hello", size=5))
    task = asyncio.ensure_future(telegram_dl.download_message(FakeTgClient(), item, str(tmp_path), NullSink()))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert item.state is ItemState.CANCELLED and os.listdir(tmp_path) == []


async def test_same_caption_in_one_batch_gets_distinct_files(tmp_path, monkeypatch):
    async def fake_fast(client, message, peer, output_path, **kw):
        await asyncio.sleep(0.01)
        with open(output_path, "wb") as fh:
            fh.write(b"x" * message.file.size)
        return output_path

    monkeypatch.setattr(telegram_dl, "fast_download_media", fake_fast)
    reserved = set()
    items = [fake_item(FakeMessage(id=i, text="Same", size=3)) for i in (1, 2, 3)]
    results = await asyncio.gather(*[
        telegram_dl.download_message(FakeTgClient(), it, str(tmp_path), NullSink(), reserved=reserved) for it in items
    ])
    assert all(results)
    names = sorted(os.listdir(tmp_path))
    assert len(names) == 3, names
    assert reserved == set()


# ---- pause, resume, reorder and where files go ------------------------------------------------

def _slow_download(log=None):
    async def slow(client, item, directory, sink, **kw):
        if log is not None:
            log.append((item.index, directory))
        item.state = ItemState.DOWNLOADING
        sink.started(item)
        try:
            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            item.state = ItemState.CANCELLED
            sink.finished(item)
            raise
        item.state, item.path = ItemState.DONE, os.path.join(directory, item.filename)
        sink.finished(item)
        return True
    return slow


async def test_pause_and_resume_one_item(paths, monkeypatch):
    monkeypatch.setattr(manager_mod, "download_message", _slow_download())
    mgr = make(paths, max_concurrent_files=1)
    a, b = mgr.enqueue_telegram(resolved(2))
    await asyncio.sleep(0.01)
    mgr.pause(b.id)                       # waiting item: just held back
    assert b.state is ItemState.PAUSED
    mgr.pause(a.id)                       # running item: stopped, comes back as paused
    await asyncio.wait_for(mgr.wait_idle(), 2)
    assert a.state is ItemState.PAUSED and a.done == 0
    assert not mgr.busy
    mgr.resume(a.id)
    mgr.resume(b.id)
    await asyncio.wait_for(mgr.wait_idle(), 2)
    assert a.state is ItemState.DONE and b.state is ItemState.DONE


async def test_pause_all_requeues_running_items_first(paths, monkeypatch):
    order = []
    monkeypatch.setattr(manager_mod, "download_message", _slow_download(order))
    mgr = make(paths, max_concurrent_files=1)
    busy = []
    mgr.on_busy_change = busy.append
    items = mgr.enqueue_telegram(resolved(3))
    await asyncio.sleep(0.01)
    mgr.pause_all()
    await asyncio.wait_for(mgr.wait_idle(), 2)   # a paused queue counts as idle
    assert mgr.paused and not mgr.busy and busy[-1] is False
    assert [i.state for i in items] == [ItemState.QUEUED] * 3
    assert [i.index for i in mgr.waiting()] == [1, 2, 3]
    mgr.resume_all()
    await asyncio.wait_for(mgr.wait_idle(), 2)
    assert all(i.state is ItemState.DONE for i in items)
    assert [n for n, _ in order] == [1, 1, 2, 3]  # the interrupted file started over first


async def test_move_to_top(paths, monkeypatch):
    order = []
    monkeypatch.setattr(manager_mod, "download_message", _slow_download(order))
    mgr = make(paths, max_concurrent_files=1)
    items = mgr.enqueue_telegram(resolved(4))
    mgr.move_to_top(items[3].id)
    await asyncio.wait_for(mgr.wait_idle(), 3)
    assert [n for n, _ in order][:2] == [1, 4]


async def test_files_go_to_a_folder_per_channel(paths, monkeypatch):
    from types import SimpleNamespace

    dirs = []
    monkeypatch.setattr(manager_mod, "download_message", _slow_download(dirs))
    mgr = make(paths, per_channel_folders=True)
    chan = SimpleNamespace(title="My: Channel")
    mgr.enqueue_telegram([ResolvedMsg(FakeMessage(id=1, text="v"), chan, "src")])
    mgr.enqueue_telegram(resolved(1))  # entity without a title: main folder
    await mgr.wait_idle()
    root = str(paths.default_downloads_dir)
    assert dirs == [(1, os.path.join(root, "My Channel")), (1, root)]
    assert mgr.items[0].chat_title == "My: Channel" and mgr.items[0].folder == os.path.join(root, "My Channel")

    dirs.clear()
    mgr.cfg = AppConfig(per_channel_folders=False)
    mgr.enqueue_telegram([ResolvedMsg(FakeMessage(id=2, text="v"), chan, "src")])
    await mgr.wait_idle()
    assert dirs == [(1, root)]


async def test_placer_and_nomedia(paths, monkeypatch, tmp_path):
    from tgdl.engine.manager import Placement

    dirs = []
    monkeypatch.setattr(manager_mod, "download_message", _slow_download(dirs))
    mgr = make(paths, nomedia=True)
    own = tmp_path / "own"
    mgr.placer = lambda item: Placement(str(own), nomedia=True) if item.index == 1 else None
    mgr.enqueue_telegram(resolved(2))
    await mgr.wait_idle()
    assert dirs[0] == (1, str(own))
    assert (own / ".nomedia").exists()
    assert (paths.default_downloads_dir / ".nomedia").exists()  # the global switch marks the main folder

    def broken(item):
        raise RuntimeError("bad setting")

    dirs.clear()
    mgr.placer = broken
    mgr.enqueue_telegram(resolved(1))
    await mgr.wait_idle()
    assert dirs == [(1, str(paths.default_downloads_dir))]
