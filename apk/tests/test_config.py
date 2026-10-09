import json

from tgdl.config import (
    AppConfig,
    EnvConfigStore,
    JsonConfigStore,
    config_from_dict,
    parse_channel,
    parse_env_text,
)


def test_parse_env_text():
    text = "# comment\nAPI_ID=123\nAPI_HASH=\"abc def\"\nexport X='y'\nEMPTY=\nNOTE=value # trailing\nbad line\n"
    assert parse_env_text(text) == {
        "API_ID": "123", "API_HASH": "abc def", "X": "y", "EMPTY": "", "NOTE": "value",
    }


def test_parse_channel_matches_old_behaviour():
    assert parse_channel("-1001234") == -1001234
    assert parse_channel("12345") == 12345
    assert parse_channel("somechannel") == "somechannel"
    assert parse_channel("") is None and parse_channel(None) is None


def test_env_store_load_and_credentials_roundtrip(tmp_path, monkeypatch):
    for key in ("API_ID", "API_HASH", "CHANNEL_ID", "MAX_TOTAL_SIZE", "DOWNLOADS_DIR"):
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / ".env"
    env.write_text("# keep me\nAPI_ID=111\nAPI_HASH=hashhash\nCHANNEL_ID=-100555\nMAX_TOTAL_SIZE=1048576\nOTHER=1\n", encoding="utf-8")
    store = EnvConfigStore(env)
    cfg = store.load()
    assert (cfg.api_id, cfg.api_hash, cfg.default_channel, cfg.max_total_size_bytes) == (111, "hashhash", -100555, 1048576)

    store.save_credentials(222, " newhash ")
    text = env.read_text(encoding="utf-8")
    assert "# keep me" in text and "OTHER=1" in text and "API_ID=222" in text and "API_HASH=newhash" in text
    assert text.count("API_ID=") == 1
    assert store.load().api_id == 222


def test_env_placeholders_and_bad_values_fall_back(tmp_path, monkeypatch):
    for key in ("API_ID", "API_HASH", "MAX_TOTAL_SIZE", "DOWNLOADS_DIR", "CHANNEL_ID"):
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / ".env"
    env.write_text("API_ID=your_api_id_here\nAPI_HASH=your_api_hash_here\nMAX_TOTAL_SIZE=nonsense\nDOWNLOADS_DIR=downloads\nCHANNEL_ID=\n", encoding="utf-8")
    cfg = EnvConfigStore(env).load()
    assert cfg.api_id == 0 and not cfg.has_credentials
    assert cfg.max_total_size_bytes == 20 * 1024 ** 3
    assert cfg.downloads_dir is None
    assert cfg.default_channel is None


def test_env_file_wins_over_process_env(tmp_path, monkeypatch):
    monkeypatch.setenv("API_ID", "999")
    env = tmp_path / ".env"
    env.write_text("API_ID=111\n", encoding="utf-8")
    assert EnvConfigStore(env).load().api_id == 111
    env.write_text("OTHER=1\n", encoding="utf-8")
    assert EnvConfigStore(env).load().api_id == 999


def test_env_save_full_config(tmp_path):
    env = tmp_path / ".env"
    store = EnvConfigStore(env)
    cfg = AppConfig(api_id=5, api_hash="h", default_channel="chan", max_concurrent_files=2)
    cfg.ytdlp.enabled = False
    cfg.ytdlp.max_height = None
    store.save(cfg)
    back = store.load()
    assert (back.api_id, back.api_hash, back.default_channel, back.max_concurrent_files) == (5, "h", "chan", 2)
    assert back.ytdlp.enabled is False and back.ytdlp.max_height is None


def test_json_store_roundtrip_and_atomic(tmp_path):
    store = JsonConfigStore(tmp_path / "sub" / "config.json")
    assert store.load() == AppConfig.defaults()
    cfg = store.load()
    cfg.api_id, cfg.api_hash = 7, "secret"
    cfg.android.keep_screen_on = True
    store.save(cfg)
    assert store.load() == cfg
    assert not list((tmp_path / "sub").glob("*.tmp"))
    store.save_credentials(8, "other")
    assert store.load().api_id == 8


def test_json_store_corrupt_file_is_kept_and_defaults_returned(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{oops", encoding="utf-8")
    assert JsonConfigStore(path).load() == AppConfig.defaults()
    assert (tmp_path / "config.json.bad").exists()


def test_values_are_clamped_and_unknown_keys_ignored():
    cfg = config_from_dict({
        "api_id": "12", "max_concurrent_files": 99, "parallel_connections": 0, "range_cap": -5,
        "ytdlp": {"max_height": 99999}, "mystery": True, "android": "not a dict",
    })
    assert cfg.api_id == 12 and cfg.max_concurrent_files == 8 and cfg.parallel_connections == 1 and cfg.range_cap == 1
    assert cfg.ytdlp.max_height == 4320
    assert cfg.android.keep_alive is True


def test_downloads_path(paths):
    cfg = AppConfig()
    assert cfg.downloads_path(paths) == str(paths.default_downloads_dir)
    cfg.downloads_dir = "/somewhere"
    assert cfg.downloads_path(paths) == "/somewhere"


def test_json_is_valid_json_on_disk(tmp_path):
    store = JsonConfigStore(tmp_path / "c.json")
    store.save(AppConfig(api_id=1, api_hash="x"))
    data = json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))
    assert data["version"] == 1 and data["ytdlp"]["enabled"] is True
