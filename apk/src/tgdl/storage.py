"""Download folders: one folder per channel, and the .nomedia marker that hides a folder from gallery apps."""
from __future__ import annotations

import mimetypes
import os

from .util import clean_filename

NOMEDIA = ".nomedia"
MAX_FOLDER_NAME = 60
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".ts", ".3gp"}


def is_video(name: str) -> bool:
    return os.path.splitext(name or "")[1].lower() in VIDEO_EXT


def mime_for(name: str) -> str | None:
    """The type other apps are offered the file as. Videos always get a video type, so players show up."""
    guess = mimetypes.guess_type(name or "")[0]
    if is_video(name) and not (guess or "").startswith("video/"):
        return "video/*"            # e.g. .ts guesses as text, .mkv is unknown on some systems
    return guess


def folder_name(title: str) -> str:
    """A channel title turned into a safe folder name."""
    name = clean_filename(title or "")[:MAX_FOLDER_NAME].strip(" .")
    return name or "Unknown chat"


def channel_dir(root: str, title: str) -> str:
    return os.path.join(root, folder_name(title))


def has_nomedia(folder: str) -> bool:
    return os.path.isfile(os.path.join(folder, NOMEDIA))


def set_nomedia(folder: str, hidden: bool) -> bool:
    """Create or remove `.nomedia` in folder. Returns True when something changed."""
    marker = os.path.join(folder, NOMEDIA)
    if hidden:
        if os.path.isfile(marker):
            return False
        os.makedirs(folder, exist_ok=True)
        with open(marker, "wb"):
            pass
        return True
    if not os.path.isfile(marker):
        return False
    os.remove(marker)
    return True


def hidden_by(path: str, stop_at: str | None = None) -> str | None:
    """The folder whose .nomedia hides path from galleries (Android honours it for subfolders too), or None."""
    folder = os.path.dirname(os.path.abspath(path)) if os.path.isfile(path) else os.path.abspath(path)
    stop = os.path.abspath(stop_at) if stop_at else None
    while True:
        if has_nomedia(folder):
            return folder
        parent = os.path.dirname(folder)
        if parent == folder or (stop and os.path.normcase(folder) == os.path.normcase(stop)):
            return None
        folder = parent


def media_files(folder: str, limit: int = 2000) -> list[str]:
    """Files under folder (two levels deep), for asking the media scanner to look at them again."""
    out: list[str] = []
    try:
        top = list(os.scandir(folder))
    except OSError:
        return out
    for entry in top:
        if len(out) >= limit:
            break
        try:
            if entry.is_file() and not entry.name.startswith(".") and not entry.name.endswith(".part"):
                out.append(entry.path)
            elif entry.is_dir():
                for sub in os.scandir(entry.path):
                    if len(out) >= limit:
                        break
                    if sub.is_file() and not sub.name.startswith(".") and not sub.name.endswith(".part"):
                        out.append(sub.path)
        except OSError:
            continue
    return out
