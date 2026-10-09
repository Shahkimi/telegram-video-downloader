import json

import pytest

from tgdl.backup import BackupError, apply_library, dumps, is_encrypted, loads, make_backup, merged_config
from tgdl.config import AppConfig
from tgdl.library import Library, LibraryEntry
from tgdl.links.rules import LinkRule, RuleSet


def sample(tmp_path):
    cfg = AppConfig(api_id=25073761, api_hash="0123456789abcdef0123456789abcdef", downloads_dir=str(tmp_path),
                    max_concurrent_files=5, nomedia=True)
    rules = RuleSet()
    rules.add(LinkRule(id="mirror", pattern="mysite.com/{channel}/{msg}"))
    lib = Library(tmp_path / "library.json", [LibraryEntry(chat_id=-1001, title="Movies", nomedia=True, folder=str(tmp_path))])
    return cfg, rules, lib


def test_plain_round_trip(tmp_path):
    cfg, rules, lib = sample(tmp_path)
    raw = dumps(make_backup(cfg, rules_text=rules.export_text(), library=lib, app_version="3.1.0"))
    assert not is_encrypted(raw)
    back = loads(raw)
    assert back.has_credentials and back.config["api_hash"] == cfg.api_hash
    assert back.rule_count == 1 and back.library[0]["title"] == "Movies"
    assert back.app_version == "3.1.0"
    assert back.describe()[0] == "API ID 250***** and API hash"


def test_credentials_can_be_left_out(tmp_path):
    cfg, _, _ = sample(tmp_path)
    back = loads(dumps(make_backup(cfg, include_credentials=False)))
    assert not back.has_credentials and "api_hash" not in back.config
    assert back.rules is None and back.library == []


def test_password_protects_everything(tmp_path):
    cfg, rules, lib = sample(tmp_path)
    raw = dumps(make_backup(cfg, rules_text=rules.export_text(), library=lib), password="s3cret pass")
    assert is_encrypted(raw)
    text = raw.decode()
    assert cfg.api_hash not in text and "25073761" not in text and "Movies" not in text

    with pytest.raises(BackupError) as need:
        loads(raw)
    assert need.value.kind == "password_needed"
    with pytest.raises(BackupError) as wrong:
        loads(raw, "nope")
    assert wrong.value.kind == "bad_password"
    assert loads(raw, "s3cret pass").config["api_hash"] == cfg.api_hash

    doc = json.loads(raw)
    doc["data"] = doc["data"][:-4] + ("AAAA" if not doc["data"].endswith("AAAA") else "BBBB")
    with pytest.raises(BackupError) as tampered:
        loads(json.dumps(doc).encode(), "s3cret pass")
    assert tampered.value.kind == "bad_password"


def test_rejects_other_files():
    for raw in (b"not json", b"[]", json.dumps({"format": "other"}).encode(),
                json.dumps({"format": "tg-downloader-backup", "version": 99}).encode()):
        with pytest.raises(BackupError) as err:
            loads(raw)
        assert err.value.kind == "format"
    damaged = json.dumps({"format": "tg-downloader-backup", "version": 1, "encrypted": True, "salt": "!!"}).encode()
    with pytest.raises(BackupError) as err:
        loads(damaged, "pw")
    assert err.value.kind == "format"


def test_merge_onto_another_phone(tmp_path):
    cfg, rules, lib = sample(tmp_path)
    backup = loads(dumps(make_backup(cfg, rules_text=rules.export_text(), library=lib)))
    backup.config["downloads_dir"] = "/does/not/exist/here"
    backup.config["ytdlp"]["cookies_file"] = "/also/missing.txt"

    phone = AppConfig(api_id=1, api_hash="old", downloads_dir="/sdcard/Mine", parallel_connections=4)
    merged = merged_config(phone, backup)
    assert (merged.api_id, merged.api_hash) == (cfg.api_id, cfg.api_hash)
    assert merged.max_concurrent_files == 5 and merged.nomedia is True
    assert merged.downloads_dir == "/sdcard/Mine"            # the backup's folder is not on this phone
    assert merged.ytdlp.cookies_file is None

    kept = merged_config(phone, backup, take_credentials=False)
    assert (kept.api_id, kept.api_hash) == (1, "old")


def test_library_is_added_not_replaced(tmp_path):
    _, _, lib = sample(tmp_path)
    rows = [{"chat_id": -1001, "title": "Movies", "nomedia": True, "folder": "/missing", "last_seen_id": 50, "unread": 9},
            {"chat_id": "-1002", "title": "News", "folder": "/missing", "unread": 4},
            {"title": "broken"}]
    target = Library(tmp_path / "other.json", [LibraryEntry(chat_id=-1001, title="Movies", last_seen_id=10)])
    assert apply_library(target, rows) == 1
    movies, news = target.get(-1001), target.get(-1002)
    assert movies.nomedia and movies.last_seen_id == 50 and movies.folder is None
    assert news.title == "News" and news.folder is None and news.unread == 0


def test_library_subfolders_travel_with_the_backup(tmp_path):
    lib = Library(tmp_path / "library.json", [LibraryEntry(chat_id=-1001, title="Dev", subfolders=["auth", "session"])])
    back = loads(dumps(make_backup(AppConfig(), library=lib)))
    target = Library(tmp_path / "other.json", [LibraryEntry(chat_id=-1002, title="News", subfolders=["x"])])
    rows = back.library + [{"chat_id": -1002, "title": "News", "subfolders": ["x", "Y", 7]},
                           {"chat_id": -1003, "title": "Bad", "subfolders": "nope"}]
    apply_library(target, rows)
    assert target.get(-1001).subfolders == ["auth", "session"]
    assert target.get(-1002).subfolders == ["x", "Y"]
    assert target.get(-1003).subfolders == []
