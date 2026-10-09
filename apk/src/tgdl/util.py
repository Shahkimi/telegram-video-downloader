"""Small helpers shared by the CLI and the app."""
from __future__ import annotations

import re
import secrets


def clean_filename(name: str) -> str:
    """Remove characters that are not allowed in file names and squeeze whitespace."""
    cleaned = re.sub(r'[\\/*?:"<>|]', "", name)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"[\x00-\x1f]", "", cleaned).strip()
    return cleaned


def format_size(num_bytes: float) -> str:
    size = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"  # pragma: no cover


def format_speed(bytes_per_s: float) -> str:
    return f"{format_size(bytes_per_s)}/s" if bytes_per_s else "-"


def format_eta(seconds: float | None) -> str:
    if seconds is None or seconds <= 0 or seconds > 99 * 3600:
        return "-"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def short_id() -> str:
    return secrets.token_hex(4)


def friendly_error(exc: BaseException) -> str:
    """One readable line for an exception, without secrets or tracebacks."""
    text = str(exc).strip() or exc.__class__.__name__
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    return text.splitlines()[0][:300]
