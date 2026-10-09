"""
The app's own records, kept as JSON next to config.json:

library.json   chats the user follows (Library screen), with a folder and .nomedia choice per chat
history.json   every finished download, newest first (History screen, "downloaded" marks)
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from .config import atomic_write_text

log = logging.getLogger("tgdl.library")

HISTORY_LIMIT = 5000


def _load_list(path: Path, key: str) -> list[dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        log.warning("%s is unreadable, starting empty", path)
        try:
            os.replace(path, path.with_suffix(path.suffix + ".bad"))
        except OSError:
            pass
        return []
    rows = data.get(key) if isinstance(data, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _from_dict(cls: type, row: dict[str, Any]) -> Any:
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in row.items() if k in names})


# ---------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------

@dataclass
class LibraryEntry:
    chat_id: int
    title: str
    username: str | None = None
    category: str = ""                  # ChatCategory value
    added_at: float = field(default_factory=time.time)
    folder: str | None = None           # own download folder; None = the default (per-channel) folder
    nomedia: bool = False               # hide this chat's folder from galleries
    media_filter: str = "video"         # MediaFilter value used on its screen and for updates
    last_seen_id: int = 0               # newest message the user has looked at
    unread: int = 0                     # new media found by the last update check
    last_checked: float = 0.0
    subfolders: list[str] = field(default_factory=list)   # custom folders made inside this chat's folder

    def remember_subfolder(self, name: str) -> bool:
        """Keep a subfolder name for the picker. False when it was already known (case-insensitive)."""
        if name and name.lower() not in {s.lower() for s in self.subfolders}:
            self.subfolders.append(name)
            return True
        return False

    @property
    def peer(self) -> int | str:
        return self.username or self.chat_id


class Library:
    def __init__(self, path: Path, entries: list[LibraryEntry] | None = None):
        self.path = Path(path)
        self._by_id: dict[int, LibraryEntry] = {e.chat_id: e for e in (entries or [])}

    @classmethod
    def load(cls, path: Path) -> "Library":
        entries = []
        for row in _load_list(Path(path), "chats"):
            try:
                entry = _from_dict(LibraryEntry, row)
                entry.chat_id = int(entry.chat_id)
                entries.append(entry)
            except (TypeError, ValueError):
                continue
        return cls(path, entries)

    def save(self) -> None:
        rows = [asdict(e) for e in self.entries()]
        atomic_write_text(self.path, json.dumps({"version": 1, "chats": rows}, indent=1, ensure_ascii=False))

    def entries(self) -> list[LibraryEntry]:
        return sorted(self._by_id.values(), key=lambda e: e.title.strip().lower())

    def get(self, chat_id: int | None) -> LibraryEntry | None:
        return self._by_id.get(chat_id) if chat_id is not None else None

    def __contains__(self, chat_id: object) -> bool:
        return chat_id in self._by_id

    def __len__(self) -> int:
        return len(self._by_id)

    def add(self, entry: LibraryEntry) -> LibraryEntry:
        existing = self._by_id.get(entry.chat_id)
        if existing is not None:
            existing.title, existing.username = entry.title or existing.title, entry.username or existing.username
            return existing
        self._by_id[entry.chat_id] = entry
        return entry

    def remove(self, chat_id: int) -> LibraryEntry | None:
        return self._by_id.pop(chat_id, None)


# ---------------------------------------------------------------------
# History
# ---------------------------------------------------------------------

@dataclass
class HistoryRecord:
    path: str
    filename: str
    size: int = 0
    finished_at: float = field(default_factory=time.time)
    chat_id: int | None = None
    chat_title: str = ""
    msg_id: int | None = None
    url: str | None = None
    kind: str = "telegram"

    @property
    def exists(self) -> bool:
        return bool(self.path) and os.path.exists(self.path)


class History:
    def __init__(self, path: Path, records: list[HistoryRecord] | None = None):
        self.path = Path(path)
        self.records: list[HistoryRecord] = list(records or [])   # newest first
        self._index: dict[tuple[int, int], HistoryRecord] = {}
        self._reindex()

    @classmethod
    def load(cls, path: Path) -> "History":
        records = []
        for row in _load_list(Path(path), "downloads"):
            try:
                records.append(_from_dict(HistoryRecord, row))
            except TypeError:
                continue
        return cls(path, records)

    def save(self) -> None:
        rows = [asdict(r) for r in self.records[:HISTORY_LIMIT]]
        atomic_write_text(self.path, json.dumps({"version": 1, "downloads": rows}, indent=0, ensure_ascii=False))

    def _reindex(self) -> None:
        self._index = {}
        for r in reversed(self.records):  # newest wins
            if r.chat_id is not None and r.msg_id is not None:
                self._index[(r.chat_id, r.msg_id)] = r

    def add(self, record: HistoryRecord) -> None:
        self.records = [r for r in self.records if r.path != record.path]
        self.records.insert(0, record)
        del self.records[HISTORY_LIMIT:]
        self._reindex()

    def add_item(self, item: Any) -> HistoryRecord | None:
        """Record a finished DownloadItem (only saved and already-present files count)."""
        if not getattr(item, "path", None):
            return None
        record = HistoryRecord(
            path=item.path,
            filename=os.path.basename(item.path),
            size=item.total or item.done or 0,
            finished_at=item.finished_at or time.time(),
            chat_id=item.chat_id,
            chat_title=item.chat_title,
            msg_id=item.msg_id,
            url=item.url,
            kind=item.kind,
        )
        self.add(record)
        return record

    def lookup(self, chat_id: int | None, msg_id: int | None) -> HistoryRecord | None:
        if chat_id is None or msg_id is None:
            return None
        return self._index.get((chat_id, msg_id))

    def downloaded(self, chat_id: int | None, msg_id: int | None) -> bool:
        record = self.lookup(chat_id, msg_id)
        return record is not None and record.exists

    def count_for(self, chat_id: int) -> int:
        return sum(1 for (cid, _), r in self._index.items() if cid == chat_id and r.exists)

    def remove(self, path: str) -> None:
        self.records = [r for r in self.records if r.path != path]
        self._reindex()

    def remove_missing(self) -> int:
        """Drop records whose file is gone. Returns how many were dropped."""
        before = len(self.records)
        self.records = [r for r in self.records if r.exists]
        self._reindex()
        return before - len(self.records)

    def clear(self) -> None:
        self.records = []
        self._index = {}
