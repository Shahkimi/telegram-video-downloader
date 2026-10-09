import asyncio

import pytest

from tests.conftest import FakeMessage
from tgdl.links.model import TelegramTarget
from tgdl.telegram.dialogs import ChatCategory, classify, list_dialogs
from tgdl.telegram.resolve import MediaFilter, accepts, reject_reason, resolve_targets, scan_channel


class FakeClient:
    def __init__(self, messages=None, known=None):
        self.messages = messages or {}
        self.known = known if known is not None else {"chan", -1001}
        self.entity_calls = []
        self.dialog_loads = 0
        self.batches = []

    async def get_entity(self, peer):
        self.entity_calls.append(peer)
        if peer not in self.known:
            raise ValueError(f"cannot find {peer}")
        return f"entity:{peer}"

    async def get_dialogs(self, limit=None):
        self.dialog_loads += 1
        self.known.add("hidden")

    async def get_messages(self, entity, ids=None):
        self.batches.append(list(ids))
        return [self.messages.get(i) for i in ids]

    async def iter_dialogs(self):
        for d in getattr(self, "dialogs", []):
            yield d

    async def iter_messages(self, entity, reverse=False):
        for m in getattr(self, "feed", []):
            yield m


def target(peer, ids):
    return TelegramTarget(peer, tuple(ids), "src", "builtin:test")


async def test_resolve_found_missing_and_wrong_type():
    video = FakeMessage(id=1)
    doc = FakeMessage(id=2, video=False, mime="application/pdf", ext=".pdf")
    client = FakeClient({1: video, 2: doc})
    res = await resolve_targets(client, [target("chan", [1, 2, 3])], MediaFilter.VIDEO)
    assert [r.message.id for r in res.found] == [1]
    levels = [n.level for n in res.notes]
    assert levels.count("ok") == 1 and levels.count("warn") == 2
    assert any("not found" in n.text for n in res.notes)
    assert any("does not contain a video" in n.text for n in res.notes)


async def test_resolve_file_filter_rejects_videos_and_empty():
    video = FakeMessage(id=1)
    doc = FakeMessage(id=2, video=False, mime="application/pdf", ext=".pdf")
    empty = FakeMessage(id=3, video=False, has_file=False, media=False)
    client = FakeClient({1: video, 2: doc, 3: empty})
    res = await resolve_targets(client, [target("chan", [1, 2, 3])], MediaFilter.FILE)
    assert [r.message.id for r in res.found] == [2]
    texts = " ".join(n.text for n in res.notes)
    assert "is a video file" in texts and "does not contain any file" in texts


async def test_resolve_batches_of_100_and_one_entity_lookup():
    client = FakeClient({i: FakeMessage(id=i) for i in range(1, 251)})
    res = await resolve_targets(client, [target("chan", range(1, 251)), target("chan", [300])], MediaFilter.ANY)
    assert len(res.found) == 250
    assert [len(b) for b in client.batches] == [100, 100, 50, 1]
    assert client.entity_calls.count("chan") == 1


async def test_resolve_loads_dialogs_once_when_chat_not_cached():
    client = FakeClient({1: FakeMessage(id=1)})
    res = await resolve_targets(client, [target("hidden", [1]), target("hidden", [1])], MediaFilter.ANY)
    assert len(res.found) == 2 and client.dialog_loads == 1


async def test_resolve_unknown_chat_reports_error_and_continues():
    client = FakeClient({1: FakeMessage(id=1)})
    res = await resolve_targets(client, [target("nowhere", [1]), target("chan", [1])], MediaFilter.ANY)
    assert len(res.found) == 1
    assert any(n.level == "error" and "nowhere" in n.text for n in res.notes)
    assert client.dialog_loads == 1


async def test_scan_channel_respects_size_budget_and_filter():
    client = FakeClient()
    client.feed = [
        FakeMessage(id=1, size=100),
        FakeMessage(id=2, video=False, mime="application/pdf", size=50),
        FakeMessage(id=3, size=100),
        FakeMessage(id=4, size=100),
    ]
    out = await scan_channel(client, "chan", MediaFilter.VIDEO, 250)
    assert [m.id for m in out.messages] == [1, 3] and out.limit_reached and out.total_size == 200
    out = await scan_channel(client, "chan", MediaFilter.VIDEO, 10 ** 9)
    assert [m.id for m in out.messages] == [1, 3, 4] and not out.limit_reached and out.scanned == 4


async def test_scan_channel_cancel_and_progress():
    client = FakeClient()
    client.feed = [FakeMessage(id=i, size=1) for i in range(1, 450)]
    cancel = asyncio.Event()
    seen = []

    def progress(scanned, found, size):
        seen.append(scanned)
        if scanned >= 200:
            cancel.set()

    out = await scan_channel(client, "chan", MediaFilter.VIDEO, 10 ** 9, on_progress=progress, cancel=cancel)
    assert out.cancelled and out.scanned < 449 and seen[0] == 100


def test_accepts_and_reason():
    pdf = FakeMessage(video=False, mime="application/pdf", ext=".pdf")
    vid = FakeMessage()
    odd_video_doc = FakeMessage(video=False, mime="video/x-matroska", ext=".mkv")
    assert accepts(vid, MediaFilter.VIDEO) and accepts(odd_video_doc, MediaFilter.VIDEO)
    assert not accepts(pdf, MediaFilter.VIDEO) and accepts(pdf, MediaFilter.FILE)
    assert not accepts(vid, MediaFilter.FILE) and accepts(vid, MediaFilter.ANY)
    assert reject_reason(pdf, MediaFilter.FILE) is None


def test_classify_with_real_telethon_types():
    from telethon.tl.types import Channel, Chat, User

    def channel(broadcast, username):
        return Channel(id=1, title="t", photo=None, date=None, broadcast=broadcast, megagroup=not broadcast, username=username)

    assert classify(channel(True, None)) is ChatCategory.PRIVATE_CHANNEL
    assert classify(channel(True, "x")) is ChatCategory.PUBLIC_CHANNEL
    assert classify(channel(False, None)) is ChatCategory.PRIVATE_GROUP
    assert classify(channel(False, "x")) is ChatCategory.PUBLIC_GROUP
    assert classify(Chat(id=1, title="t", photo=None, participants_count=1, date=None, version=1)) is ChatCategory.PRIVATE_GROUP
    assert classify(User(id=1)) is None


async def test_list_dialogs_sorted_and_filtered():
    from types import SimpleNamespace

    from telethon.tl.types import Channel

    def dlg(name, did, broadcast, username):
        ent = Channel(id=did, title=name, photo=None, date=None, broadcast=broadcast, megagroup=not broadcast, username=username)
        return SimpleNamespace(entity=ent, name=name, id=did)

    client = FakeClient()
    client.dialogs = [dlg("zeta", 1, True, None), dlg("Alpha", 2, True, None), dlg("beta", 3, False, None), SimpleNamespace(entity=object(), name="user", id=9)]
    everything = await list_dialogs(client)
    assert [d.name for d in everything] == ["Alpha", "beta", "zeta"]
    only = await list_dialogs(client, ChatCategory.PRIVATE_CHANNEL)
    assert [d.name for d in only] == ["Alpha", "zeta"]
