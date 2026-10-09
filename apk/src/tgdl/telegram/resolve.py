"""Turn parsed link targets into real Telegram messages, and scan a whole channel for media."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from ..links.model import TelegramTarget
from ..util import friendly_error


class MediaFilter(str, Enum):
    VIDEO = "video"     # videos only (CLI mode 3, batch scan)
    FILE = "file"       # files that are not videos (CLI mode 4)
    ANY = "any"         # anything with a file attached (the app)


@dataclass
class Note:
    level: str          # "ok" | "warn" | "error"
    text: str


@dataclass
class ResolvedMsg:
    message: Any
    entity: Any
    source: str = ""


@dataclass
class ResolveResult:
    found: list[ResolvedMsg] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)


@dataclass
class ScanResult:
    messages: list[Any] = field(default_factory=list)
    total_size: int = 0
    scanned: int = 0
    limit_reached: bool = False
    cancelled: bool = False


def is_video(message: Any) -> bool:
    doc = getattr(message, "document", None)
    return bool(getattr(message, "video", None)) or bool(doc and (getattr(doc, "mime_type", "") or "").startswith("video/"))


def file_size(message: Any) -> int:
    f = getattr(message, "file", None)
    return getattr(f, "size", 0) or 0 if f else 0


def reject_reason(message: Any, f: MediaFilter) -> str | None:
    """None when the message is wanted, otherwise a short reason."""
    if f is MediaFilter.VIDEO:
        return None if is_video(message) else "does not contain a video file."
    if not getattr(message, "file", None):
        return "does not contain any file/document."
    if f is MediaFilter.FILE and is_video(message):
        return "is a video file. (Use the video option for videos)"
    return None


def accepts(message: Any, f: MediaFilter) -> bool:
    return reject_reason(message, f) is None


def kind_label(message: Any) -> str:
    if is_video(message):
        return "video"
    ext = (getattr(getattr(message, "file", None), "ext", "") or "").upper().strip(".")
    return f"file ({ext or 'DOCUMENT'})"


async def resolve_targets(
    client: Any,
    targets: list[TelegramTarget],
    f: MediaFilter = MediaFilter.ANY,
    on_note: Callable[[Note], None] | None = None,
) -> ResolveResult:
    """
    Fetch the messages behind link targets. Messages are requested 100 at a time per chat.
    When a chat is not in Telethon's cache the dialog list is loaded once and the lookup retried.
    """
    result = ResolveResult()
    entities: dict[Any, Any] = {}
    dialogs_loaded = False

    def note(level: str, text: str) -> None:
        n = Note(level, text)
        result.notes.append(n)
        if on_note:
            on_note(n)

    async def get_entity(peer: Any) -> Any:
        nonlocal dialogs_loaded
        try:
            return await client.get_entity(peer)
        except Exception:  # noqa: BLE001 - Telethon raises several types for "not cached"
            if dialogs_loaded:
                raise
            dialogs_loaded = True
            await client.get_dialogs(limit=None)
            return await client.get_entity(peer)

    for target in targets:
        if target.peer not in entities:
            try:
                entities[target.peer] = await get_entity(target.peer)
            except Exception as exc:  # noqa: BLE001
                entities[target.peer] = exc
        entity = entities[target.peer]
        if isinstance(entity, BaseException):
            note("error", f"Cannot open chat {target.peer}. Detail: {friendly_error(entity)}")
            continue

        ids = list(target.msg_ids)
        for i in range(0, len(ids), 100):
            chunk = ids[i:i + 100]
            try:
                messages = await client.get_messages(entity, ids=chunk)
            except Exception as exc:  # noqa: BLE001
                note("error", f"Failed to fetch message IDs {chunk[0]}-{chunk[-1]}. Detail: {friendly_error(exc)}")
                continue
            if not isinstance(messages, (list, tuple)) and not hasattr(messages, "__iter__"):
                messages = [messages]
            for msg_id, message in zip(chunk, messages):
                if not message:
                    note("warn", f"Message ID {msg_id} not found in target channel.")
                    continue
                reason = reject_reason(message, f)
                if reason:
                    note("warn", f"Message ID {msg_id} {reason}")
                    continue
                result.found.append(ResolvedMsg(message, entity, target.source))
                note("ok", f"Found {kind_label(message)} in Message ID {msg_id}")
            await asyncio.sleep(0)
    return result


async def scan_channel(
    client: Any,
    entity: Any,
    f: MediaFilter,
    max_total_size: int,
    on_progress: Callable[[int, int, int], None] | None = None,
    cancel: asyncio.Event | None = None,
) -> ScanResult:
    """Walk a chat oldest-first and collect wanted media until the size budget is used up."""
    out = ScanResult()
    async for message in client.iter_messages(entity, reverse=True):
        out.scanned += 1
        if cancel is not None and cancel.is_set():
            out.cancelled = True
            break
        if on_progress and out.scanned % 100 == 0:
            on_progress(out.scanned, len(out.messages), out.total_size)
        if not accepts(message, f):
            continue
        size = file_size(message)
        if out.total_size + size > max_total_size:
            out.limit_reached = True
            break
        out.messages.append(message)
        out.total_size += size
    if on_progress:
        on_progress(out.scanned, len(out.messages), out.total_size)
    return out
