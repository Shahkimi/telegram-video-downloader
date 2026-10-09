"""
Download links from other sites with yt-dlp.

yt-dlp is synchronous, so it runs in a worker thread. Progress comes back through
loop.call_soon_threadsafe. Cancelling sets a flag that the next progress hook turns into
yt_dlp.utils.DownloadCancelled.

Limits worth knowing (shown in the app):
  * Without ffmpeg (always the case on Android) only formats that already contain video and
    audio can be used, so high resolutions on sites that split them (YouTube) are not available.
  * Recent YouTube support needs a JavaScript runtime (deno, node, bun or quickjs). Desktop can
    have one; Android cannot, so YouTube there is best effort.
  * Some sites (Instagram, X, age-gated videos) need a cookies file.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from ..util import friendly_error
from .models import DownloadItem, ItemState, ProgressSink

log = logging.getLogger("tgdl.ytdlp")

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


class _QuietLogger:
    def debug(self, msg: str) -> None:
        if msg.startswith("[debug]"):
            return
        log.debug(_ANSI.sub("", msg))

    def info(self, msg: str) -> None:
        log.info(_ANSI.sub("", msg))

    def warning(self, msg: str) -> None:
        log.warning(_ANSI.sub("", msg))

    def error(self, msg: str) -> None:
        log.error(_ANSI.sub("", msg))


def detect_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def detect_js_runtimes() -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for name, binary in (("deno", "deno"), ("node", "node"), ("bun", "bun"), ("quickjs", "qjs")):
        path = shutil.which(binary)
        if path:
            found[name] = {"path": path}
    return found


def explain_error(message: str) -> str:
    """Turn the common yt-dlp failures into something a person can act on."""
    text = _ANSI.sub("", message).strip()
    text = re.sub(r"^ERROR:\s*", "", text)
    low = text.lower()
    if "requested format is not available" in low:
        return text + " (This site only offers separate video and audio streams and ffmpeg is not available here, or the video is limited.)"
    if "sign in" in low or "cookies" in low or "login required" in low or "private video" in low:
        return text + " (This video needs you to be logged in: set a cookies file in Settings.)"
    if "unsupported url" in low:
        return text + " (yt-dlp does not support this site.)"
    if "javascript runtime" in low or "challenge" in low:
        return text + " (YouTube needs a JavaScript runtime such as deno or node, which this device does not have.)"
    return text


class YtdlpDownloader:
    def __init__(
        self,
        downloads_dir: str,
        cache_dir: str | os.PathLike[str] | None = None,
        *,
        max_height: int | None = 1080,
        cookies_file: str | None = None,
        has_ffmpeg: bool | None = None,
    ):
        self.downloads_dir = downloads_dir
        self.cache_dir = str(cache_dir) if cache_dir else None
        self.max_height = max_height
        self.cookies_file = cookies_file
        self.has_ffmpeg = detect_ffmpeg() if has_ffmpeg is None else has_ffmpeg

    def format_spec(self) -> str:
        h = f"[height<={self.max_height}]" if self.max_height else ""
        if self.has_ffmpeg:
            return f"bv*{h}+ba/b{h}/b"
        # one file with video and audio inside; prefer plain http over HLS/DASH manifests
        return f"b{h}[protocol^=http]/b{h}/b"

    def build_options(self, hook: Any) -> dict[str, Any]:
        opts: dict[str, Any] = {
            "format": self.format_spec(),
            "outtmpl": os.path.join(self.downloads_dir, "%(title).80B [%(id)s].%(ext)s"),
            "noplaylist": True,
            "playlist_items": "1",
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "windowsfilenames": True,
            "retries": 5,
            "fragment_retries": 10,
            "continuedl": True,
            "overwrites": False,
            "concurrent_fragment_downloads": 4,
            "progress_hooks": [hook],
            "logger": _QuietLogger(),
            "color": {"stdout": "never", "stderr": "never"},
        }
        if self.cache_dir:
            opts["cachedir"] = os.path.join(self.cache_dir, "yt-dlp")
        else:
            opts["cachedir"] = False
        if self.has_ffmpeg:
            opts["merge_output_format"] = "mp4"
        if self.cookies_file and os.path.exists(self.cookies_file):
            opts["cookiefile"] = self.cookies_file
        runtimes = detect_js_runtimes()
        if runtimes:
            opts["js_runtimes"] = runtimes
        return opts

    async def download(self, item: DownloadItem, sink: ProgressSink, cancel: threading.Event) -> bool:
        loop = asyncio.get_running_loop()
        os.makedirs(self.downloads_dir, exist_ok=True)
        item.state, item.started_at, item.error = ItemState.DOWNLOADING, time.time(), None
        sink.started(item)

        def apply(title: str | None, total: int, done: int, speed: float, name: str | None) -> None:
            if title and not item.title:
                item.title = title
            if name:
                item.filename = os.path.basename(name).removesuffix(".part")
            if total:
                item.total = total
            item.done, item.speed = done, speed
            sink.progress(item)

        def run() -> str:
            import yt_dlp
            from yt_dlp.utils import DownloadCancelled

            def hook(d: dict[str, Any]) -> None:
                if cancel.is_set():
                    raise DownloadCancelled()
                info = d.get("info_dict") or {}
                total = int(d.get("total_bytes") or d.get("total_bytes_estimate") or 0)
                done = int(d.get("downloaded_bytes") or 0)
                speed = float(d.get("speed") or 0.0)
                loop.call_soon_threadsafe(apply, info.get("title"), total, done, speed, d.get("filename"))

            with yt_dlp.YoutubeDL(self.build_options(hook)) as ydl:
                info = ydl.extract_info(item.url, download=True)
                if info is None:
                    raise RuntimeError("yt-dlp returned no information for this link")
                if info.get("_type") == "playlist":
                    entries = [e for e in (info.get("entries") or []) if e]
                    if not entries:
                        raise RuntimeError("This link is a playlist with nothing downloadable")
                    info = entries[0]
                for dl in info.get("requested_downloads") or []:
                    if dl.get("filepath"):
                        return dl["filepath"]
                return ydl.prepare_filename(info)

        try:
            path = await asyncio.to_thread(run)
        except asyncio.CancelledError:
            cancel.set()
            item.state, item.finished_at = ItemState.CANCELLED, time.time()
            sink.finished(item)
            raise
        except BaseException as exc:  # noqa: BLE001 - DownloadError, DownloadCancelled, network errors
            if cancel.is_set():
                item.state = ItemState.CANCELLED
            else:
                item.state, item.error = ItemState.FAILED, explain_error(friendly_error(exc))
                log.warning("yt-dlp failed for %s: %s", item.url, item.error)
            item.finished_at = time.time()
            sink.finished(item)
            return item.state is ItemState.CANCELLED

        size = os.path.getsize(path) if os.path.exists(path) else 0
        item.path = path
        item.filename = Path(path).name
        item.done = item.total = size or item.total
        item.speed, item.finished_at = 0.0, time.time()
        item.state = ItemState.DONE
        sink.finished(item)
        return True
