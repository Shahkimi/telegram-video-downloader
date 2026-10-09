"""Page through a chat's media newest first (the channel screen), plus cached thumbnails and chat photos."""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..engine.telegram_dl import build_filename
from .dialogs import peer_id
from .resolve import MediaFilter, accepts, file_size, is_video

log = logging.getLogger("tgdl.browse")

PAGE_SIZE = 40
RAW_FACTOR = 4          # without a server-side filter, look at up to this many messages per wanted one
_thumb_gate: asyncio.Semaphore | None = None


@dataclass
class MediaEntry:
    msg_id: int
    name: str
    size: int
    is_video: bool
    date: datetime | None = None
    duration: int | None = None
    caption: str = ""
    message: Any = None


@dataclass
class MediaPage:
    entries: list[MediaEntry]
    next_offset: int        # pass as offset_id for the next page; 0 when there is nothing older
    newest_id: int          # highest message id seen on this page (0 when empty)


def entry_for(message: Any) -> MediaEntry:
    f = getattr(message, "file", None)
    duration = getattr(f, "duration", None) if f is not None else None
    text = (getattr(message, "text", None) or "").strip()
    return MediaEntry(
        msg_id=message.id,
        name=build_filename(message),
        size=file_size(message),
        is_video=is_video(message),
        date=getattr(message, "date", None),
        duration=int(duration) if isinstance(duration, (int, float)) else None,
        caption=text.split("\n")[0][:200] if text else "",
        message=message,
    )


def _server_filter(f: MediaFilter) -> Any:
    from telethon.tl import types

    if f is MediaFilter.VIDEO:
        return types.InputMessagesFilterVideo()
    if f is MediaFilter.FILE:
        return types.InputMessagesFilterDocument()
    return None


async def fetch_media(
    client: Any,
    entity: Any,
    f: MediaFilter = MediaFilter.VIDEO,
    *,
    offset_id: int = 0,
    min_id: int = 0,
    limit: int = PAGE_SIZE,
) -> MediaPage:
    """One page of media older than offset_id (0 = newest) and newer than min_id."""
    server = _server_filter(f)
    raw_limit = limit if server is not None else limit * RAW_FACTOR
    entries: list[MediaEntry] = []
    last_id = 0
    newest = 0
    seen = 0
    async for message in client.iter_messages(entity, limit=raw_limit, offset_id=offset_id, min_id=min_id, filter=server):
        seen += 1
        last_id = message.id
        newest = max(newest, message.id)
        if accepts(message, f):
            entries.append(entry_for(message))
            if len(entries) >= limit:
                break
    exhausted = seen < raw_limit and len(entries) < limit
    return MediaPage(entries, 0 if (exhausted or not last_id) else last_id, newest)


async def newest_message_id(client: Any, entity: Any) -> int:
    async for message in client.iter_messages(entity, limit=1):
        return message.id
    return 0


# ---------------------------------------------------------------------
# Pictures, cached on disk so lists scroll without asking Telegram again
# ---------------------------------------------------------------------

def _gate() -> asyncio.Semaphore:
    global _thumb_gate
    if _thumb_gate is None:
        _thumb_gate = asyncio.Semaphore(4)
    return _thumb_gate


def _read(path: Path) -> bytes | None:
    try:
        data = path.read_bytes()
        return data or None
    except OSError:
        return None


def _write(path: Path, data: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except OSError:
        log.debug("cannot cache %s", path, exc_info=True)


def thumb_path(cache_dir: Path, chat_id: int | None, msg_id: int) -> Path:
    return Path(cache_dir) / "thumbs" / f"{chat_id or 0}_{msg_id}.jpg"


def cover_path(cache_dir: Path, chat_id: int | None) -> Path:
    return Path(cache_dir) / "covers" / f"{chat_id or 0}.jpg"


async def message_thumb(client: Any, message: Any, cache_dir: Path, chat_id: int | None = None) -> bytes | None:
    """The small preview Telegram stores with a video or document, or None."""
    path = thumb_path(cache_dir, chat_id if chat_id is not None else peer_id(getattr(message, "peer_id", None)), message.id)
    cached = _read(path)
    if cached:
        return cached
    if path.with_suffix(".none").exists():
        return None
    async with _gate():
        try:
            data = await client.download_media(message, file=bytes, thumb=-1)
        except Exception:  # noqa: BLE001 - no thumbnail, expired reference, network
            data = None
    if isinstance(data, (bytes, bytearray)) and data:
        _write(path, bytes(data))
        return bytes(data)
    _write(path.with_suffix(".none"), b"-")
    return None


async def chat_cover(client: Any, entity: Any, cache_dir: Path, refresh: bool = False) -> bytes | None:
    """The chat's profile photo (small size), or None when it has none."""
    path = cover_path(cache_dir, peer_id(entity))
    if not refresh:
        cached = _read(path)
        if cached:
            return cached
        if path.with_suffix(".none").exists():
            return None
    async with _gate():
        try:
            data = await client.download_profile_photo(entity, file=bytes, download_big=False)
        except Exception:  # noqa: BLE001
            data = None
    if isinstance(data, (bytes, bytearray)) and data:
        _write(path, bytes(data))
        return bytes(data)
    _write(path.with_suffix(".none"), b"-")
    return None
