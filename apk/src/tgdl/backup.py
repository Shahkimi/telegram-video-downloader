"""
Settings backup: one JSON file with the API ID/hash, settings, link rules and library, optionally password protected.

The Telegram login (session file) is never included: whoever has it can use the account without a code.

Encrypted files use PBKDF2-SHA256 for the key, AES-256-CTR (pyaes, which ships with Telethon) for the content and
HMAC-SHA256 over everything, so a wrong password or a changed file is detected before anything is applied.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .config import AppConfig, config_from_dict, config_to_dict
from .library import Library, LibraryEntry

FORMAT = "tg-downloader-backup"
VERSION = 1
KDF_ITERATIONS = 200_000
CREDENTIAL_KEYS = ("api_id", "api_hash")
DEVICE_PATH_KEYS = ("downloads_dir",)       # only applied when the folder exists on this phone


class BackupError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind            # "format" | "password_needed" | "bad_password"
        self.message = message


@dataclass
class Backup:
    config: dict[str, Any] | None = None
    rules: dict[str, Any] | None = None
    library: list[dict[str, Any]] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    app_version: str = ""

    @property
    def has_credentials(self) -> bool:
        return bool(self.config and self.config.get("api_id") and self.config.get("api_hash"))

    @property
    def rule_count(self) -> int:
        return len((self.rules or {}).get("rules") or [])

    def describe(self) -> list[str]:
        out = []
        if self.has_credentials:
            api_id = str(self.config.get("api_id"))
            out.append(f"API ID {api_id[:3]}{'*' * max(0, len(api_id) - 3)} and API hash")
        if self.config:
            out.append("Settings")
        if self.rules is not None:
            out.append(f"{self.rule_count} link rule(s)")
        if self.library:
            out.append(f"{len(self.library)} library chat(s)")
        return out


# ---------------------------------------------------------------------
# Building and reading
# ---------------------------------------------------------------------

def make_backup(cfg: AppConfig, *, rules_text: str | None = None, library: Library | None = None,
                include_credentials: bool = True, app_version: str = "") -> Backup:
    config = config_to_dict(cfg)
    if not include_credentials:
        for key in CREDENTIAL_KEYS:
            config.pop(key, None)
    rules = json.loads(rules_text) if rules_text else None
    rows = [asdict(e) for e in library.entries()] if library is not None else []
    return Backup(config=config, rules=rules, library=rows, app_version=app_version)


def _keys(password: str, salt: bytes, iterations: int = KDF_ITERATIONS) -> tuple[bytes, bytes]:
    raw = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=64)
    return raw[:32], raw[32:]


def _ctr(key: bytes, nonce: bytes, data: bytes) -> bytes:
    import pyaes

    counter = pyaes.Counter(initial_value=int.from_bytes(nonce, "big"))
    return pyaes.AESModeOfOperationCTR(key, counter=counter).encrypt(data)


def dumps(backup: Backup, password: str | None = None) -> bytes:
    content = {"config": backup.config, "rules": backup.rules, "library": backup.library}
    head = {"format": FORMAT, "version": VERSION, "created": int(backup.created), "app_version": backup.app_version}
    if not password:
        return json.dumps({**head, "encrypted": False, **content}, indent=2, ensure_ascii=False).encode("utf-8")
    salt, nonce = os.urandom(16), os.urandom(16)
    enc_key, mac_key = _keys(password, salt)
    cipher = _ctr(enc_key, nonce, json.dumps(content, ensure_ascii=False).encode("utf-8"))
    mac = hmac.new(mac_key, salt + nonce + cipher, hashlib.sha256).digest()
    b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
    return json.dumps({
        **head, "encrypted": True, "kdf": "pbkdf2-sha256", "iterations": KDF_ITERATIONS, "cipher": "aes-256-ctr",
        "salt": b64(salt), "nonce": b64(nonce), "data": b64(cipher), "mac": b64(mac),
    }, indent=2).encode("utf-8")


def _outer(raw: bytes) -> dict[str, Any]:
    try:
        doc = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise BackupError("format", "This is not a TG Downloader backup file.") from exc
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise BackupError("format", "This is not a TG Downloader backup file.")
    if int(doc.get("version") or 0) > VERSION:
        raise BackupError("format", "This backup was made by a newer version of the app. Update the app first.")
    return doc


def is_encrypted(raw: bytes) -> bool:
    return bool(_outer(raw).get("encrypted"))


def loads(raw: bytes, password: str | None = None) -> Backup:
    doc = _outer(raw)
    if doc.get("encrypted"):
        if not password:
            raise BackupError("password_needed", "This backup is protected with a password.")
        try:
            salt, nonce, cipher, mac = (base64.b64decode(doc[k]) for k in ("salt", "nonce", "data", "mac"))
            # a crafted file must not hang the app with an absurd iteration count
            iterations = min(max(int(doc.get("iterations") or KDF_ITERATIONS), 10_000), 2_000_000)
        except (KeyError, ValueError, TypeError) as exc:
            raise BackupError("format", "The backup file is damaged.") from exc
        enc_key, mac_key = _keys(password, salt, iterations)
        if not hmac.compare_digest(hmac.new(mac_key, salt + nonce + cipher, hashlib.sha256).digest(), mac):
            raise BackupError("bad_password", "Wrong password, or the file was changed.")
        try:
            content = json.loads(_ctr(enc_key, nonce, cipher).decode("utf-8"))
        except ValueError as exc:
            raise BackupError("format", "The backup file is damaged.") from exc
    else:
        content = doc
    config = content.get("config") if isinstance(content.get("config"), dict) else None
    rules = content.get("rules") if isinstance(content.get("rules"), dict) else None
    library = [r for r in content.get("library") or [] if isinstance(r, dict)]
    return Backup(config=config, rules=rules, library=library, created=float(doc.get("created") or 0),
                  app_version=str(doc.get("app_version") or ""))


def file_name(when: float | None = None) -> str:
    return time.strftime("tg-downloader-backup-%Y%m%d-%H%M.json", time.localtime(when or time.time()))


# ---------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------

def merged_config(current: AppConfig, backup: Backup, *, take_credentials: bool = True) -> AppConfig:
    """Settings from the backup on top of the current ones. Folders that do not exist on this phone are left alone."""
    if not backup.config:
        return current
    data = config_to_dict(current)
    incoming = dict(backup.config)
    if not take_credentials or not backup.has_credentials:
        for key in CREDENTIAL_KEYS:
            incoming.pop(key, None)
    for key in DEVICE_PATH_KEYS:
        value = incoming.get(key)
        if value and not os.path.isdir(str(value)):
            incoming.pop(key, None)
    yt = incoming.get("ytdlp")
    if isinstance(yt, dict) and yt.get("cookies_file") and not os.path.isfile(str(yt["cookies_file"])):
        incoming["ytdlp"] = {k: v for k, v in yt.items() if k != "cookies_file"}
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key] = {**data[key], **value}
        else:
            data[key] = value
    return config_from_dict(data)


def apply_library(library: Library, rows: list[dict[str, Any]]) -> int:
    """Add the backup's chats. Existing chats keep their own folder unless they have none. Returns how many were added."""
    added = 0
    names = set(LibraryEntry.__dataclass_fields__)
    for row in rows:
        try:
            entry = LibraryEntry(**{k: v for k, v in row.items() if k in names})
            entry.chat_id = int(entry.chat_id)
        except (TypeError, ValueError):
            continue
        if entry.folder and not os.path.isdir(entry.folder):
            entry.folder = None
        existing = library.get(entry.chat_id)
        if existing is None:
            entry.unread = 0
            library.add(entry)
            added += 1
        else:
            existing.nomedia = existing.nomedia or entry.nomedia
            existing.folder = existing.folder or entry.folder
            existing.last_seen_id = max(existing.last_seen_id, entry.last_seen_id)
    return added
