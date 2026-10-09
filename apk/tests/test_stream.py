import asyncio

import pytest

from tgdl.stream import CHUNK, StreamServer, StreamSource, parse_range

DATA = bytes(range(256)) * ((CHUNK * 3 + 1000) // 256)   # a bit over three Telegram chunks


def fake_fetch(calls):
    async def fetch(source, offset):
        calls.append(offset)
        assert offset % CHUNK == 0
        for pos in range(offset, len(DATA), CHUNK):
            yield DATA[pos:pos + CHUNK]
    return fetch


async def request(port, path, headers=None, method="GET"):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    lines = [f"{method} {path} HTTP/1.1", "Host: x"] + [f"{k}: {v}" for k, v in (headers or {}).items()]
    writer.write(("\r\n".join(lines) + "\r\n\r\n").encode())
    await writer.drain()
    head = (await reader.readuntil(b"\r\n\r\n")).decode()
    status = head.split("\r\n")[0]
    hdrs = {k.lower(): v.strip() for k, v in (l.split(":", 1) for l in head.split("\r\n")[1:] if ":" in l)}
    body = b""
    if method != "HEAD":
        body = await reader.readexactly(int(hdrs.get("content-length", 0)))
    writer.close()
    return status, hdrs, body


def test_parse_range():
    assert parse_range(None, 100) is None
    assert parse_range("bytes=0-", 100) == (0, 99)
    assert parse_range("bytes=10-19", 100) == (10, 19)
    assert parse_range("bytes=90-500", 100) == (90, 99)
    assert parse_range("bytes=-10", 100) == (90, 99)
    assert parse_range("bytes=0-1,5-6", 100) is None  # several ranges: send it all
    with pytest.raises(ValueError):
        parse_range("bytes=100-", 100)


@pytest.fixture
async def server():
    calls = []
    srv = StreamServer(get_client=None, fetch=fake_fetch(calls))
    srv.calls = calls
    yield srv
    await srv.close()


async def test_whole_file_and_ranges(server):
    url = await server.url_for(StreamSource(message=None, peer=None, size=len(DATA), name="clip.mp4"))
    path = url.split(str(server.port), 1)[1]
    assert url.startswith("http://127.0.0.1:") and path.endswith(".mp4")

    status, hdrs, body = await request(server.port, path)
    assert status.endswith("200 OK") and body == DATA and hdrs["accept-ranges"] == "bytes"

    start, end = CHUNK + 123, CHUNK * 2 + 77   # crosses a chunk border, not aligned
    status, hdrs, body = await request(server.port, path, {"Range": f"bytes={start}-{end}"})
    assert "206" in status and body == DATA[start:end + 1]
    assert hdrs["content-range"] == f"bytes {start}-{end}/{len(DATA)}"
    assert server.calls[-1] == CHUNK  # fetched from the aligned offset

    status, _, body = await request(server.port, path, {"Range": "bytes=-500"})
    assert "206" in status and body == DATA[-500:]

    status, hdrs, body = await request(server.port, path, method="HEAD")
    assert "200" in status and hdrs["content-length"] == str(len(DATA)) and body == b""

    status, hdrs, _ = await request(server.port, path, {"Range": f"bytes={len(DATA)}-"})
    assert "416" in status and hdrs["content-range"] == f"bytes */{len(DATA)}"


async def test_unknown_or_forgotten_paths_are_refused(server):
    url = await server.url_for(StreamSource(message=None, peer=None, size=len(DATA)))
    path = url.split(str(server.port), 1)[1]
    status, _, _ = await request(server.port, "/v/guess.mp4")
    assert "404" in status
    status, _, _ = await request(server.port, "/etc/passwd")
    assert "404" in status
    status, _, _ = await request(server.port, path, method="POST")
    assert "405" in status
    server.forget(url)
    status, _, _ = await request(server.port, path)
    assert "404" in status


async def test_player_hanging_up_stops_the_download(server):
    stopped = asyncio.Event()

    async def endless(source, offset):
        try:
            while True:
                yield b"x" * CHUNK
                await asyncio.sleep(0)
        finally:
            stopped.set()

    server.fetch = endless
    url = await server.url_for(StreamSource(message=None, peer=None, size=CHUNK * 1000))
    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
    writer.write(f"GET {url.split(str(server.port), 1)[1]} HTTP/1.1\r\n\r\n".encode())
    await reader.readuntil(b"\r\n\r\n")
    await reader.read(CHUNK)
    writer.transport.abort()
    await asyncio.wait_for(stopped.wait(), 5)


async def test_old_links_are_pruned(server):
    urls = [await server.url_for(StreamSource(message=None, peer=None, size=10)) for _ in range(30)]
    assert len(server.sources) <= 20
    status, _, _ = await request(server.port, urls[0].split(str(server.port), 1)[1])
    assert "404" in status
