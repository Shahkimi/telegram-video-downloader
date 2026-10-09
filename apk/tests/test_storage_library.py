import json
import os

from tgdl.config import EnvConfigStore, JsonConfigStore, config_from_dict
from tgdl.engine.models import DownloadItem, ItemState
from tgdl.library import History, HistoryRecord, Library, LibraryEntry
from tgdl.storage import NOMEDIA, channel_dir, folder_name, has_nomedia, hidden_by, media_files, set_nomedia


def test_folder_name_is_safe():
    assert folder_name('Movies: "Best" / 2026?') == "Movies Best 2026"
    assert folder_name("   ") == "Unknown chat"
    assert folder_name("x" * 200) == "x" * 60
    assert channel_dir("/root", "A/B") == os.path.join("/root", "AB")


def test_nomedia_marker_on_and_off(tmp_path):
    folder = tmp_path / "dl" / "Chan"
    assert set_nomedia(str(folder), True) is True
    assert (folder / NOMEDIA).is_file() and has_nomedia(str(folder))
    assert set_nomedia(str(folder), True) is False  # already there
    assert set_nomedia(str(folder), False) is True
    assert not (folder / NOMEDIA).exists()
    assert set_nomedia(str(folder), False) is False


def test_hidden_by_looks_at_parent_folders(tmp_path):
    root = tmp_path / "dl"
    sub = root / "Chan"
    sub.mkdir(parents=True)
    video = sub / "a.mp4"
    video.write_bytes(b"x")
    assert hidden_by(str(video), str(tmp_path)) is None
    set_nomedia(str(root), True)
    assert hidden_by(str(video), str(tmp_path)) == str(root)
    assert hidden_by(str(sub), str(tmp_path)) == str(root)


def test_media_files_skips_markers_and_parts(tmp_path):
    (tmp_path / "Chan").mkdir()
    for name in ("a.mp4", ".nomedia", "b.mp4.part"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "Chan" / "c.mkv").write_bytes(b"x")
    found = sorted(os.path.basename(p) for p in media_files(str(tmp_path)))
    assert found == ["a.mp4", "c.mkv"]


def test_config_storage_options(tmp_path, monkeypatch):
    for key in ("PER_CHANNEL_FOLDERS", "NOMEDIA"):
        monkeypatch.setenv(key, "")  # the .env store mirrors values into os.environ; undo that after the test
    cfg = config_from_dict({"per_channel_folders": "no", "nomedia": True})
    assert cfg.per_channel_folders is False and cfg.nomedia is True
    store = JsonConfigStore(tmp_path / "config.json")
    store.save(cfg)
    assert store.load().nomedia is True

    env = EnvConfigStore(tmp_path / ".env")
    assert env.load().per_channel_folders is False  # the CLI keeps its single folder unless asked
    env.update_values({"PER_CHANNEL_FOLDERS": "1", "NOMEDIA": "1"})
    loaded = env.load()
    assert loaded.per_channel_folders is True and loaded.nomedia is True


def test_library_round_trip(tmp_path):
    path = tmp_path / "library.json"
    lib = Library.load(path)
    assert len(lib) == 0
    lib.add(LibraryEntry(chat_id=-1001, title="Beta", username="beta"))
    lib.add(LibraryEntry(chat_id=-1002, title="alpha", nomedia=True, folder="/x"))
    again = lib.add(LibraryEntry(chat_id=-1001, title="Beta renamed"))
    assert again.title == "Beta renamed" and again.username == "beta"
    lib.save()

    loaded = Library.load(path)
    assert [e.title for e in loaded.entries()] == ["alpha", "Beta renamed"]
    assert loaded.get(-1002).nomedia and loaded.get(-1002).folder == "/x"
    assert loaded.get(-1001).peer == "beta" and loaded.get(-1002).peer == -1002
    assert -1001 in loaded and loaded.remove(-1001) is not None and -1001 not in loaded


def test_library_survives_a_broken_file(tmp_path):
    path = tmp_path / "library.json"
    path.write_text("{not json", encoding="utf-8")
    assert len(Library.load(path)) == 0
    assert (tmp_path / "library.json.bad").exists()
    path.write_text(json.dumps({"chats": [{"chat_id": "12", "title": "ok", "unknown": 1}, {"title": "no id"}, 5]}), encoding="utf-8")
    lib = Library.load(path)
    assert [e.chat_id for e in lib.entries()] == [12]


def test_history_records_and_lookup(tmp_path):
    video = tmp_path / "a.mp4"
    video.write_bytes(b"1234")

    class Msg:
        id = 7

    item = DownloadItem(id="x", kind="telegram", title="a", filename="a.mp4", total=4, state=ItemState.DONE,
                        path=str(video), chat_id=-100, chat_title="Chan", message=Msg(), finished_at=1000.0)
    history = History.load(tmp_path / "history.json")
    record = history.add_item(item)
    assert record.msg_id == 7 and record.chat_title == "Chan"
    assert history.downloaded(-100, 7) and not history.downloaded(-100, 8)
    assert history.add_item(DownloadItem(id="y", kind="ytdlp", title="")) is None  # nothing saved, nothing recorded

    history.add(HistoryRecord(path=str(tmp_path / "gone.mp4"), filename="gone.mp4", chat_id=-100, msg_id=9))
    assert history.records[0].filename == "gone.mp4"
    assert not history.downloaded(-100, 9)  # recorded, but the file is not there
    history.save()

    loaded = History.load(tmp_path / "history.json")
    assert [r.filename for r in loaded.records] == ["gone.mp4", "a.mp4"]
    assert loaded.count_for(-100) == 1
    assert loaded.remove_missing() == 1
    loaded.add(HistoryRecord(path=str(video), filename="a.mp4", chat_id=-100, msg_id=7))
    assert len(loaded.records) == 1  # same path replaces the old record
    loaded.clear()
    assert not loaded.records and not loaded.downloaded(-100, 7)
