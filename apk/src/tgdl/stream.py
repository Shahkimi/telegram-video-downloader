"""
Watch a Telegram video before downloading it.

A tiny HTTP server on 127.0.0.1 hands the player only the bytes it asks for (HTTP Range requests), fetching them from
Telegram as they are needed, so seeking works and nothing is saved to disk. Every video gets a random, unguessable
path: other apps on the phone can reach 127.0.0.1 too, and must not be able to read anything else.
"""
from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

log = logging.getLogger("tgdl.stream")

CHUNK = 512 * 1024              # Telegram wants offsets aligned to the request size; 512 KB is its maximum
MAX_TOKENS = 20
TOKEN_LIFETIME = 6 * 3600
_RANGE = re.compile(r"bytes=(\d*)-(\d*)")


@dataclass
class StreamSource:
    message: Any
    peer: Any
    size: int
    mime: str = "video/mp4"
    name: str = "video.mp4"
    created: float = field(default_factory=time.time)


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """(start, end inclusive) for a single 'bytes=a-b' range, None for the whole file. Raises ValueError when unsatisfiable."""
    if not header:
        return None
    m = _RANGE.fullmatch(header.strip())
    if not m:
        return None  # multiple ranges or junk: answer with the whole file, which is always allowed
    first, last = m.groups()
    if first == "" and last == "":
        return None
    if first == "":                      # suffix: the last N bytes
        n = int(last)
        if n <= 0:
            raise ValueError("empty suffix range")
        return max(0, size - n), size - 1
    start = int(first)
    end = int(last) if last else size - 1
    if start >= size or end < start:
        raise ValueError("range outside the file")
    return start, min(end, size - 1)


class StreamServer:
    """
    get_client: coroutine returning a connected Telethon client (called per request, so a reconnect is picked up).
    fetch: for tests, replaces the Telegram download. fetch(source, aligned_offset) yields CHUNK-sized pieces.
    """

    def __init__(self, get_client: Callable[[], Awaitable[Any]],
                 fetch: Callable[[StreamSource, int], Any] | None = None):
        self.get_client = get_client
        self.fetch = fetch or self._telegram_chunks
        self.sources: dict[str, StreamSource] = {}
        self.server: asyncio.base_events.Server | None = None
        self.port = 0
        self._loop: asyncio.AbstractEventLoop | None = None

    # ---- lifecycle ----------------------------------------------------------
    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        if self.server is not None and self._loop is loop:
            return
        await self.close()
        self.server = await asyncio.start_server(self._handle, host="127.0.0.1", port=0)
        self.port = self.server.sockets[0].getsockname()[1]
        self._loop = loop
        log.info("stream server on 127.0.0.1:%s", self.port)

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            try:
                await self.server.wait_closed()
            except Exception:  # noqa: BLE001
                pass
        self.server = None

    async def url_for(self, source: StreamSource) -> str:
        await self.start()
        self._prune()
        token = secrets.token_urlsafe(18)
        self.sources[token] = source
        ext = source.name.rsplit(".", 1)[-1] if "." in source.name else "mp4"
        return f"http://127.0.0.1:{self.port}/v/{token}.{ext}"

    def forget(self, url: str) -> None:
        token = url.rsplit("/", 1)[-1].split(".", 1)[0]
        self.sources.pop(token, None)

    def _prune(self) -> None:
        now = time.time()
        for token, src in list(self.sources.items()):
            if now - src.created > TOKEN_LIFETIME:
                del self.sources[token]
        while len(self.sources) >= MAX_TOKENS:
            oldest = min(self.sources, key=lambda t: self.sources[t].created)
            del self.sources[oldest]

    # ---- HTTP ---------------------------------------------------------------
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:  # keep-alive: players often send several range requests on one connection
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=30)
                keep = await self._answer(head.decode("latin-1"), writer)
                if not keep:
                    break
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError, asyncio.LimitOverrunError):
            pass
        except Exception:  # noqa: BLE001 - one bad request must not take the server down
            log.exception("stream request failed")
        finally:
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    async def _reply(writer: asyncio.StreamWriter, status: str, headers: dict[str, str], body: bytes = b"") -> None:
        lines = [f"HTTP/1.1 {status}"] + [f"{k}: {v}" for k, v in headers.items()]
        writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body)
        await writer.drain()

    async def _answer(self, head: str, writer: asyncio.StreamWriter) -> bool:
        lines = head.split("\r\n")
        try:
            method, target, _ = lines[0].split(" ", 2)
        except ValueError:
            await self._reply(writer, "400 Bad Request", {"Content-Length": "0", "Connection": "close"})
            return False
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()

        token = target.split("?", 1)[0].rsplit("/", 1)[-1].split(".", 1)[0]
        source = self.sources.get(token) if target.startswith("/v/") else None
        if source is None:
            await self._reply(writer, "404 Not Found", {"Content-Length": "0", "Connection": "close"})
            return False
        if method not in ("GET", "HEAD"):
            await self._reply(writer, "405 Method Not Allowed", {"Content-Length": "0", "Allow": "GET, HEAD", "Connection": "close"})
            return False

        size = source.size
        try:
            wanted = parse_range(headers.get("range"), size)
        except ValueError:
            await self._reply(writer, "416 Range Not Satisfiable", {"Content-Range": f"bytes */{size}", "Content-Length": "0"})
            return True
        start, end = wanted if wanted else (0, size - 1)
        length = end - start + 1
        out = {
            "Content-Type": source.mime or "application/octet-stream",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
            "Cache-Control": "no-store",
        }
        if wanted:
            out["Content-Range"] = f"bytes {start}-{end}/{size}"
        await self._reply(writer, "206 Partial Content" if wanted else "200 OK", out)
        if method == "HEAD" or length <= 0:
            return True
        await self._send(source, start, end, writer)
        return True

    async def _send(self, source: StreamSource, start: int, end: int, writer: asyncio.StreamWriter) -> None:
        aligned = start - start % CHUNK
        skip = start - aligned
        remaining = end - start + 1
        chunks = self.fetch(source, aligned)
        try:
            async for chunk in chunks:
                if skip:
                    chunk = chunk[skip:]
                    skip = 0
                if not chunk:
                    break
                piece = chunk[:remaining]
                writer.write(piece)
                await writer.drain()  # raises when the player hung up
                remaining -= len(piece)
                if remaining <= 0:
                    return
        finally:
            closer = getattr(chunks, "aclose", None)
            if closer is not None:
                await closer()  # stop asking Telegram for bytes nobody reads
        if remaining > 0:
            raise ConnectionError(f"Telegram ended the stream {remaining} bytes early")

    # ---- Telegram -----------------------------------------------------------
    async def _telegram_chunks(self, source: StreamSource, offset: int):
        from telethon import errors

        client = await self.get_client()
        message = source.message
        for attempt in range(3):
            doc = getattr(getattr(message, "media", None), "document", None) or message.media
            stream = client.iter_download(doc, offset=offset, request_size=CHUNK, chunk_size=CHUNK, file_size=source.size)
            try:
                async for chunk in stream:
                    yield bytes(chunk)
                    offset += len(chunk)
                return
            except (errors.FileReferenceExpiredError, errors.FilerefUpgradeNeededError):
                if attempt == 2 or source.peer is None:
                    raise
                fresh = await client.get_messages(source.peer, ids=message.id)
                if not fresh or not fresh.media:
                    raise
                message = source.message = fresh
            finally:
                try:
                    await stream.close()
                except Exception:  # noqa: BLE001
                    pass
