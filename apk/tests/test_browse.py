from tests.conftest import FakeMessage
from tgdl.telegram import browse
from tgdl.telegram.browse import chat_cover, fetch_media, message_thumb, newest_message_id
from tgdl.telegram.resolve import MediaFilter


class FakeClient:
    """iter_messages newest first, honouring limit, offset_id, min_id and the video/document filters."""

    def __init__(self, messages):
        self.messages = sorted(messages, key=lambda m: -m.id)
        self.calls = []
        self.thumb_calls = 0
        self.photo_calls = 0

    async def iter_messages(self, entity, limit=None, offset_id=0, min_id=0, filter=None):
        self.calls.append({"limit": limit, "offset_id": offset_id, "min_id": min_id, "filter": type(filter).__name__ if filter else None})
        n = 0
        for m in self.messages:
            if offset_id and m.id >= offset_id:
                continue
            if m.id <= min_id:
                continue
            if filter is not None and type(filter).__name__ == "InputMessagesFilterVideo" and not m.video:
                continue
            if limit is not None and n >= limit:
                return
            n += 1
            yield m

    async def download_media(self, message, file=None, thumb=None):
        self.thumb_calls += 1
        return b"jpeg" if message.id % 2 else None

    async def download_profile_photo(self, entity, file=None, download_big=False):
        self.photo_calls += 1
        return b"photo"


def history(n):
    msgs = []
    for i in range(1, n + 1):
        has_file = i % 5 != 0
        video = i % 3 != 0 and has_file
        msgs.append(FakeMessage(id=i, text=f"clip {i}", video=video, mime="video/mp4" if video else "application/pdf",
                                ext=".mp4" if video else ".pdf", has_file=has_file))
    return msgs


async def test_pages_walk_backwards_until_the_start():
    client = FakeClient(history(30))
    first = await fetch_media(client, "chan", MediaFilter.VIDEO, limit=8)
    assert [e.msg_id for e in first.entries][:3] == [29, 28, 26]
    assert first.newest_id == 29 and first.next_offset == first.entries[-1].msg_id
    assert client.calls[0]["filter"] == "InputMessagesFilterVideo"

    seen = [e.msg_id for e in first.entries]
    offset = first.next_offset
    while offset:
        page = await fetch_media(client, "chan", MediaFilter.VIDEO, offset_id=offset, limit=8)
        seen += [e.msg_id for e in page.entries]
        offset = page.next_offset
    expected = [m.id for m in sorted(history(30), key=lambda m: -m.id) if m.video and m.file]
    assert seen == expected


async def test_everything_filter_skips_messages_without_files():
    client = FakeClient(history(12))
    page = await fetch_media(client, "chan", MediaFilter.ANY, limit=50)
    assert 10 not in [e.msg_id for e in page.entries] and 5 not in [e.msg_id for e in page.entries]
    assert page.next_offset == 0  # fewer than asked for: nothing older
    assert client.calls[0]["filter"] is None and client.calls[0]["limit"] == 200


async def test_min_id_returns_only_new_posts():
    client = FakeClient(history(20))
    page = await fetch_media(client, "chan", MediaFilter.VIDEO, min_id=17)
    assert [e.msg_id for e in page.entries] == [19]  # 18 is a pdf, 20 has no file
    assert await newest_message_id(client, "chan") == 20


async def test_entry_fields():
    msg = FakeMessage(id=3, text="Holiday\nsecond line", size=2048)
    msg.file.duration = 75.4
    entry = browse.entry_for(msg)
    assert entry.caption == "Holiday" and entry.name == "Holiday.mp4"
    assert entry.size == 2048 and entry.duration == 75 and entry.is_video


async def test_thumbs_and_covers_are_cached(tmp_path):
    client = FakeClient([])
    odd, even = FakeMessage(id=1), FakeMessage(id=2)
    assert await message_thumb(client, odd, tmp_path, chat_id=-100) == b"jpeg"
    assert await message_thumb(client, odd, tmp_path, chat_id=-100) == b"jpeg"
    assert await message_thumb(client, even, tmp_path, chat_id=-100) is None
    assert await message_thumb(client, even, tmp_path, chat_id=-100) is None
    assert client.thumb_calls == 2  # second asks come from disk, "no thumbnail" is remembered too

    class Chan:
        id = 55
        broadcast = True

    assert await chat_cover(client, Chan(), tmp_path) == b"photo"
    assert await chat_cover(client, Chan(), tmp_path) == b"photo"
    assert await chat_cover(client, Chan(), tmp_path, refresh=True) == b"photo"
    assert client.photo_calls == 2
