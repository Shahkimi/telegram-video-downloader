"""Facts about the running environment, for `downloader.py --diag` and the app Diagnostics screen."""
from __future__ import annotations

import os
import platform
import sys
import tempfile
from importlib import metadata

from . import __version__, crypto_backend
from .config import AppConfig
from .engine.ytdlp_dl import detect_ffmpeg, detect_js_runtimes
from .paths import AppPaths


def _version(dist: str) -> str:
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return "not installed"


def writable_dir(path: str) -> str:
    """Try to create the folder and write a file in it. Returns 'ok' or the reason it failed."""
    try:
        os.makedirs(path, exist_ok=True)
        fd, probe = tempfile.mkstemp(dir=path, prefix=".tgdl-probe-")
        os.close(fd)
        os.remove(probe)
        return "ok"
    except OSError as exc:
        return f"not writable ({exc.__class__.__name__}: {exc.strerror or exc})"


def collect(paths: AppPaths, cfg: AppConfig, run_benchmark: bool = False) -> list[tuple[str, str]]:
    info = crypto_backend.current_info()
    if run_benchmark:
        crypto_backend.benchmark()
    runtimes = ", ".join(detect_js_runtimes()) or "none"
    ssl_file = os.environ.get("SSL_CERT_FILE", "")

    rows = [
        ("App", f"tgdl {__version__}"),
        ("Python", f"{sys.version.split()[0]} on {platform.system()} {platform.machine()}"),
        ("Android", "yes" if paths.is_android else "no"),
        ("Telethon", _version("telethon")),
        ("yt-dlp", _version("yt-dlp")),
        ("Flet", _version("flet")),
        ("AES-IGE backend", info.describe()),
        ("Speed check", "slow: downloads will crawl" if not info.fast else "fast"),
        ("ffmpeg", "found" if detect_ffmpeg() else "not found (no audio/video merging)"),
        ("JavaScript runtime", runtimes),
        ("CA bundle (SSL_CERT_FILE)", ssl_file or "system default"),
        ("Data folder", str(paths.data_dir)),
        ("Session file", "present" if os.path.exists(str(paths.session_base) + ".session") else "none"),
        ("Download folder", f"{cfg.downloads_path(paths)}  [{writable_dir(cfg.downloads_path(paths))}]"),
        ("Concurrent files / connections", f"{cfg.max_concurrent_files} / {cfg.parallel_connections}"),
    ]
    return rows
