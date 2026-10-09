"""Everything that must outlive a single page: settings, Telegram session, download queue."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from tgdl import __version__, netfix
from tgdl.config import AppConfig, JsonConfigStore
from tgdl.engine.manager import DownloadManager, Placement
from tgdl.engine.models import DownloadItem
from tgdl.library import History, Library, LibraryEntry
from tgdl.links.rules import RuleSet
from tgdl.logs import setup_logging
from tgdl.paths import AppPaths, resolve_paths
from tgdl.storage import channel_dir
from tgdl.telegram.dialogs import DialogInfo
from tgdl.telegram.session import TelegramSession

from .sink import UiSink

log = logging.getLogger("tgdl.ui")


class AppState:
    def __init__(self) -> None:
        self.paths: AppPaths = resolve_paths("app")
        self.paths.ensure()
        setup_logging(self.paths)
        netfix.apply_network_fixes(self.paths.data_dir)

        self.store = JsonConfigStore(self.paths.config_file)
        self.cfg: AppConfig = self.store.load()
        self.rules: RuleSet = RuleSet.load(self.paths.rules_file)
        self.session = TelegramSession(self.paths, self.store, device_model="TG Downloader", app_version=__version__)
        self.sink = UiSink()
        self.manager = DownloadManager(self.session, self.cfg, self.paths, self.sink)
        self.manager.placer = self.place
        self.library = Library.load(self.paths.data_dir / "library.json")
        self.history = History.load(self.paths.data_dir / "history.json")

        self.dialogs: list[DialogInfo] | None = None
        self.entities: dict[int, Any] = {}      # chat id -> Telethon entity, filled while browsing
        self.pending_shares: list[str] = []
        self.last_clipboard_seen = ""
        self.active_app: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        log.info("state ready, data dir %s", self.paths.data_dir)

    # ---- persistence ------------------------------------------------------
    def save_config(self) -> None:
        self.store.save(self.cfg)
        self.manager.cfg = self.cfg

    def save_rules(self) -> None:
        self.rules.save(self.paths.rules_file)

    def reload_rules(self) -> RuleSet:
        self.rules = RuleSet.load(self.paths.rules_file)
        return self.rules

    def save_library(self) -> None:
        try:
            self.library.save()
        except OSError:
            log.exception("cannot save the library")

    def save_history(self) -> None:
        try:
            self.history.save()
        except OSError:
            log.exception("cannot save the history")

    # ---- where files go -----------------------------------------------------
    def folder_for(self, chat_id: int | None, title: str) -> str:
        """The folder a chat's downloads go to: its own choice, else the per-channel default."""
        entry = self.library.get(chat_id)
        if entry is not None and entry.folder:
            return entry.folder
        root = self.manager.downloads_dir
        return channel_dir(root, title) if (self.cfg.per_channel_folders and title) else root

    def place(self, item: DownloadItem) -> Placement | None:
        entry = self.library.get(item.chat_id)
        if entry is None:
            return None
        return Placement(self.folder_for(entry.chat_id, item.chat_title or entry.title), nomedia=entry.nomedia)

    def add_to_library(self, chat_id: int, title: str, username: str | None = None, category: str = "",
                       last_seen_id: int = 0) -> LibraryEntry:
        entry = self.library.add(LibraryEntry(chat_id=chat_id, title=title, username=username, category=category,
                                              last_seen_id=last_seen_id))
        self.save_library()
        return entry

    # ---- event loop -------------------------------------------------------
    async def bind_loop(self) -> None:
        """Telethon connections belong to the loop that created them; start over if the loop changed."""
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            log.info("event loop changed, resetting Telegram connection")
            await self.session.close()
            self.dialogs = None
        self._loop = loop


_state: AppState | None = None


def get_state() -> AppState:
    global _state
    if _state is None:
        _state = AppState()
    return _state
