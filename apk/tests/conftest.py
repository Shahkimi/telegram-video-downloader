from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from tgdl.config import AppConfig
from tgdl.paths import AppPaths


class FakeMessage:
    def __init__(self, id=1, text="", name=None, ext=".mp4", size=1000, video=True, mime="video/mp4", date=None, has_file=True, media=True):
        self.id = id
        self.text = text
        self.video = SimpleNamespace() if video else None
        self.document = SimpleNamespace(mime_type=mime) if has_file else None
        self.file = SimpleNamespace(name=name, ext=ext, size=size) if has_file else None
        self.media = SimpleNamespace() if media else None
        self.date = date or dt.datetime(2026, 1, 2, 3, 4, 5)
        self.peer_id = "peer"


@pytest.fixture
def fake_message():
    return FakeMessage


@pytest.fixture
def paths(tmp_path) -> AppPaths:
    return AppPaths(
        mode="app",
        data_dir=tmp_path / "data",
        session_base=tmp_path / "data" / "tgdl",
        config_file=tmp_path / "data" / "config.json",
        rules_file=tmp_path / "data" / "link_rules.json",
        log_file=tmp_path / "data" / "tgdl.log",
        cache_dir=tmp_path / "cache",
        default_downloads_dir=tmp_path / "dl",
        is_android=False,
    )


@pytest.fixture
def cfg() -> AppConfig:
    return AppConfig()
