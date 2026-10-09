"""Filesystem locations for CLI and Android-app modes."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

DOWNLOAD_SUBDIR = "Telegram Downloads"
ANDROID_PUBLIC_DOWNLOADS = "/storage/emulated/0/Download"


def is_android() -> bool:
    return sys.platform == "android" or os.getenv("FLET_PLATFORM", "").lower() == "android"


def default_downloads_dir() -> Path:
    if is_android():
        return Path(ANDROID_PUBLIC_DOWNLOADS) / DOWNLOAD_SUBDIR
    return Path.home() / "Downloads" / DOWNLOAD_SUBDIR


@dataclass(frozen=True)
class AppPaths:
    mode: str                       # "cli" or "app"
    data_dir: Path
    session_base: Path              # Telethon appends ".session"
    config_file: Path
    rules_file: Path
    log_file: Path
    cache_dir: Path
    default_downloads_dir: Path
    is_android: bool

    def ensure(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)


def resolve_paths(mode: str = "app", project_root: Path | None = None) -> AppPaths:
    """
    cli: everything lives next to downloader.py (.env, session.session, link_rules.json),
         which keeps existing installs working.
    app: everything lives in the app-private data dir (FLET_APP_STORAGE_DATA on Android).
    """
    android = is_android()
    if mode == "cli":
        root = Path(project_root or Path.cwd()).resolve()
        return AppPaths(
            mode="cli",
            data_dir=root,
            session_base=root / "session",
            config_file=root / ".env",
            rules_file=root / "link_rules.json",
            log_file=root / "tgdl.log",
            cache_dir=root / ".cache",
            default_downloads_dir=default_downloads_dir(),
            is_android=android,
        )

    raw = os.getenv("TGDL_DATA_DIR") or os.getenv("FLET_APP_STORAGE_DATA")
    data = Path(raw) if raw else Path.home() / ".tgdl"
    cache_raw = os.getenv("FLET_APP_STORAGE_CACHE")
    return AppPaths(
        mode="app",
        data_dir=data,
        session_base=data / "tgdl",
        config_file=data / "config.json",
        rules_file=data / "link_rules.json",
        log_file=data / "tgdl.log",
        cache_dir=Path(cache_raw) if cache_raw else data / "cache",
        default_downloads_dir=default_downloads_dir(),
        is_android=android,
    )
