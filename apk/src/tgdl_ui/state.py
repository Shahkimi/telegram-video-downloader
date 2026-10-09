"""Everything that must outlive a single page: settings, Telegram session, download queue."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from tgdl import __version__, netfix
from tgdl.config import AppConfig, JsonConfigStore
from tgdl.engine.manager import DownloadManager
from tgdl.links.rules import RuleSet
from tgdl.logs import setup_logging
from tgdl.paths import AppPaths, resolve_paths
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

        self.dialogs: list[DialogInfo] | None = None
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
