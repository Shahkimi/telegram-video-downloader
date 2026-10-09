"""Download folders: one folder per channel, custom folders inside it, the .nomedia marker that hides a folder from
gallery apps, and the free-space check done before queueing."""
from __future__ import annotations

import mimetypes
import os
import shutil
from dataclasses import dataclass

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


def subfolder_name(name: str) -> str:
    """A user-typed subfolder name made safe: one level, no path tricks. '' when nothing usable is left."""
    cleaned = clean_filename((name or "").replace("/", " ").replace("\\", " "))[:MAX_FOLDER_NAME].strip(" .")
    return cleaned


def subfolder_dir(base: str, name: str) -> str:
    """base/name for a custom subfolder, or base itself when name is empty."""
    safe = subfolder_name(name)
    return os.path.join(base, safe) if safe else base


def subfolders(base: str) -> list[tuple[str, int]]:
    """The visible folders directly inside base with the number of files in each, sorted by name."""
    out: list[tuple[str, int]] = []
    try:
        entries = list(os.scandir(base))
    except OSError:
        return out
    for entry in entries:
        try:
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            count = sum(1 for f in os.scandir(entry.path)
                        if f.is_file() and not f.name.startswith(".") and not f.name.endswith(".part"))
        except OSError:
            continue
        out.append((entry.name, count))
    return sorted(out, key=lambda t: t[0].lower())


def subfolder_of(path: str, base: str) -> str:
    """The subfolder of base that holds path ('' when it sits directly in base or somewhere else)."""
    parent = os.path.dirname(os.path.abspath(path))
    root = os.path.abspath(base)
    if os.path.normcase(os.path.dirname(parent)) == os.path.normcase(root):
        return os.path.basename(parent)
    return ""


RESERVE = 200 * 1024 * 1024   # leave this much free so Android itself does not run out


def free_space(path: str) -> int | None:
    """Free bytes on the storage holding path (the folder may not exist yet). None when it cannot be read."""
    probe = os.path.abspath(path or ".")
    while not os.path.isdir(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            return None
        probe = parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return None


@dataclass
class SpaceCheck:
    """Whether `needed` more bytes fit, after what the queue still has to write (`queued`)."""
    needed: int
    queued: int = 0
    free: int | None = None

    @property
    def fits(self) -> bool:
        return self.free is None or self.needed + self.queued + RESERVE <= self.free

    @property
    def short_by(self) -> int:
        return 0 if self.fits else self.needed + self.queued + RESERVE - (self.free or 0)

    @property
    def free_after(self) -> int | None:
        return None if self.free is None else self.free - self.queued - self.needed


def check_space(folder: str, needed: int, queued: int = 0) -> SpaceCheck:
    return SpaceCheck(max(0, needed), max(0, queued), free_space(folder))


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
