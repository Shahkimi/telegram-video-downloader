"""The Tachiyomi-style screens, built with a mocked page: Library, channel page, Updates, History, More, queue."""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import flet as ft
import pytest

from tests.conftest import FakeMessage
from tgdl.engine.models import DownloadItem, ItemState
from tgdl.library import HistoryRecord, LibraryEntry
from tgdl.telegram.browse import MediaPage, entry_for
from tgdl_ui import state as ui_state
from tgdl_ui.app import App
from tgdl_ui.views import channel as channel_mod
from tgdl_ui.views.channel import ChannelView


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("TGDL_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setattr(ui_state, "_state", None)
    state = ui_state.get_state()
    state.cfg.downloads_dir = str(tmp_path / "dl")
    state.manager.paused = True  # nothing really downloads in these tests
    page = MagicMock()
    page.platform = ft.PagePlatform.WINDOWS
    page.views = [object()]
    instance = App(page, state)
    yield instance
    state.active_app = None
    monkeypatch.setattr(ui_state, "_state", None)


def chan(chat_id=-1001, title="Movies"):
    return SimpleNamespace(id=chat_id, title=title, username=None, broadcast=True)


def toast(app):
    return app.toast_text.value


def test_library_grid_list_and_actions(app):
    st = app.state
    st.library.add(LibraryEntry(chat_id=-1, title="Beta", unread=3))
    st.library.add(LibraryEntry(chat_id=-2, title="Alpha", nomedia=True))
    st.history.add(HistoryRecord(path="x.mp4", filename="x.mp4", chat_id=-2, msg_id=1))
    app.show("library")
    view = app.library
    assert len(view.grid.controls) == 2 and not view.empty.visible
    view._set_sort("unread")
    assert view._entries()[0].title == "Beta"
    view._set_sort("downloaded")
    assert view._entries()[0].title == "Alpha"
    view._toggle_layout(SimpleNamespace(control=SimpleNamespace(icon=None)))
    assert view.list.visible and len(view.list.controls) == 2
    view.search.visible, view.search.value = True, "alp"
    assert [e.title for e in view._entries()] == ["Alpha"]
    view.show_actions(st.library.get(-1))
    assert isinstance(app.page.show_dialog.call_args[0][0], ft.BottomSheet)


def test_empty_library(app):
    app.show("library")
    assert app.library.empty.visible and not app.library.grid.visible


async def test_channel_page_loads_and_downloads(app, monkeypatch):
    entity = chan()
    msgs = [FakeMessage(id=i, text=f"clip {i}", size=1000 * i) for i in (5, 4, 3)]

    async def fake_fetch(client, ent, f, offset_id=0, min_id=0, limit=40):
        return MediaPage([entry_for(m) for m in msgs], 0, 5)

    async def connected():
        return "client"

    monkeypatch.setattr(channel_mod, "fetch_media", fake_fetch)
    monkeypatch.setattr(app.state.session, "ensure_connected", connected)

    st = app.state
    st.library.add(LibraryEntry(chat_id=-1001, title="Movies", unread=2, last_seen_id=1))
    view = app.open_channel(-1001, "Movies", entity=entity)
    assert app.top_owner() is view and app.current == "panel"
    await view.reload()
    assert [r.entry.msg_id for r in view.rows.values()] == [5, 4, 3]
    assert all(r.status == "none" for r in view.rows.values())
    assert st.library.get(-1001).unread == 0 and st.library.get(-1001).last_seen_id == 5

    added = view.download([view.rows[5].entry])
    assert len(added) == 1 and added[0].chat_id == -1001 and added[0].chat_title == "Movies"
    view.on_downloads_changed()
    assert view.rows[5].status == "queued"
    assert view.download([view.rows[5].entry]) == []  # already queued
    assert "Already" in toast(app)

    # a finished download (history) shows as done
    path = os.path.join(st.cfg.downloads_dir, "clip 4.mp4")
    os.makedirs(st.cfg.downloads_dir, exist_ok=True)
    open(path, "wb").close()
    st.history.add(HistoryRecord(path=path, filename="clip 4.mp4", chat_id=-1001, msg_id=4))
    view.on_downloads_changed()
    assert view.rows[4].status == "done"

    # selection: long-press starts it, the floating button downloads what is picked
    view.on_row_long_press(view.rows[3].entry)
    assert view.selecting and view.selected == {3} and view.fab.visible
    view.select_all()
    assert view.selected == {5, 4, 3}
    view.invert()
    assert view.selected == set()
    view.toggle(3)
    view.download_selected()
    assert not view.selecting
    assert view.rows[3].status == "queued"

    view.show_details(view.rows[4].entry)
    assert isinstance(app.page.show_dialog.call_args[0][0], ft.BottomSheet)
    app.back()
    assert view.closed and app.current == app.tab


async def test_channel_page_states_follow_the_queue(app):
    view = ChannelView(app, -1001, "Movies", entity=chan())
    entry = entry_for(FakeMessage(id=9, text="x", size=100))
    row = channel_mod.MediaRow(view, entry)
    view.rows[9], view.entries = row, [entry]
    item = DownloadItem(id="i", kind="telegram", title="x", total=100, done=40, speed=10, state=ItemState.DOWNLOADING,
                        chat_id=-1001, message=entry.message)
    app.state.manager.items.append(item)
    view.on_downloads_changed()
    assert row.status.startswith("downloading") and "40%" in row.meta.value
    item.state = ItemState.PAUSED
    view.on_downloads_changed()
    assert row.status == "paused"
    item.state, item.error = ItemState.FAILED, "network down"
    view.on_downloads_changed()
    assert row.status == "failed"


async def test_hide_from_gallery_per_chat_and_globally(app):
    st = app.state
    st.cfg.per_channel_folders = True
    await app.set_chat_hidden(-1001, "Movies", True)
    entry = st.library.get(-1001)
    folder = st.folder_for(-1001, "Movies")
    assert entry is not None and entry.nomedia
    assert os.path.exists(os.path.join(folder, ".nomedia"))
    await app.set_chat_hidden(-1001, "Movies", False)
    assert not os.path.exists(os.path.join(folder, ".nomedia")) and not entry.nomedia

    await app.set_download_root_hidden(True)
    assert st.cfg.nomedia and os.path.exists(os.path.join(st.manager.downloads_dir, ".nomedia"))
    await app.set_chat_hidden(-1001, "Movies", False)
    assert "still hidden" in toast(app)
    await app.set_download_root_hidden(False)
    assert not os.path.exists(os.path.join(st.manager.downloads_dir, ".nomedia"))


def test_per_chat_folder_is_used_for_downloads(app, tmp_path):
    st = app.state
    st.library.add(LibraryEntry(chat_id=-1001, title="Movies", folder=str(tmp_path / "mine"), nomedia=True))
    item = DownloadItem(id="a", kind="telegram", title="v", chat_id=-1001, chat_title="Movies")
    place = st.place(item)
    assert place.folder == str(tmp_path / "mine") and place.nomedia
    assert st.place(DownloadItem(id="b", kind="telegram", title="v", chat_id=-5, chat_title="Other")) is None


async def test_updates_history_and_more_render(app, tmp_path):
    st = app.state
    entry = st.library.add(LibraryEntry(chat_id=-1001, title="Movies", unread=1))
    media = entry_for(FakeMessage(id=12, text="new one"))
    app.show("updates")
    app.updates.found = [(entry, chan(), media)]
    app.updates.render()
    assert any(isinstance(c, ft.Container) for c in app.updates.list.controls)
    assert app.updates.download_all() == 1
    assert app.state.manager.items[-1].msg_id == 12
    app.updates.forget_chat(-1001)
    assert app.updates.found == []

    real = tmp_path / "a.mp4"
    real.write_bytes(b"1")
    st.history.add(HistoryRecord(path=str(tmp_path / "gone.mp4"), filename="gone.mp4"))
    st.history.add(HistoryRecord(path=str(real), filename="a.mp4", chat_id=-1001, msg_id=1, chat_title="Movies"))
    app.show("history")
    assert len(app.history.list.controls) >= 3  # a day header and two rows
    app.history._forget_missing()
    assert [r.filename for r in st.history.records] == ["a.mp4"]
    app.history.confirm_delete(st.history.records[0])
    dialog = app.page.show_dialog.call_args[0][0]
    dialog.actions[1].on_click(None)
    assert not real.exists() and not st.history.records

    app.show("more")
    app.more.refresh_queue_line()
    assert "waiting" in app.more.queue_line.value or "paused" in app.more.queue_line.value


def test_queue_page_pause_resume_all(app):
    manager = app.state.manager
    manager.paused = False
    app.open_queue()
    assert app.top_owner() is app.queue
    item = DownloadItem(id="q1", kind="telegram", title="clip", state=ItemState.PAUSED)
    manager.items.append(item)
    app.queue.on_show()
    assert "q1" in app.queue.tiles
    tile = app.queue.tiles["q1"]
    assert [i.content for i in tile.menu.items] == ["Resume", "Cancel"]
    manager.paused = True
    app.queue._rebuild()
    assert app.queue.pause_btn.icon == ft.Icons.PLAY_ARROW


def test_finished_downloads_go_to_history(app, tmp_path):
    path = tmp_path / "done.mp4"
    path.write_bytes(b"1")
    msg = FakeMessage(id=3)
    done = DownloadItem(id="d", kind="telegram", title="done", state=ItemState.DONE, path=str(path), chat_id=-7,
                        chat_title="C", message=msg, total=1)
    failed = DownloadItem(id="f", kind="telegram", title="bad", state=ItemState.FAILED)
    app._record_finished([done, failed])
    assert app.state.history.downloaded(-7, 3)
    assert len(app.state.history.records) == 1 and app._history_dirty


async def test_watch_streams_or_plays_the_saved_file(app, tmp_path):
    import flet_video as fv

    scheduled = []
    app.page.run_task = lambda fn, *a: scheduled.append((fn, a))
    view = ChannelView(app, -1001, "Movies", entity=chan())
    msg = FakeMessage(id=8, text="trailer", size=5000)
    msg.file.mime_type = "video/x-matroska"
    entry = entry_for(msg)
    try:
        # not downloaded yet: streamed from Telegram through a private local link
        view.watch(entry)
        page = scheduled[-1][0].__self__
        assert page.source.size == 5000 and page.source.mime == "video/x-matroska" and page.on_download
        await page.open()
        assert page.url.startswith("http://127.0.0.1:") and page.url.endswith(".mp4")
        root = app.page.views[-1].controls[0].content.content
        video = root.controls[0].content
        assert isinstance(video, fv.Video) and video.playlist[0].resource == page.url and video.autoplay
        assert len(app.state.streams.sources) == 1
        page._download(None)
        assert app.state.manager.items[-1].msg_id == 8  # Download from the player queues it
        app.back()
        assert not app.state.streams.sources  # the link dies with the page

        # downloaded: plays the file, nothing is streamed
        saved = tmp_path / "trailer.mp4"
        saved.write_bytes(b"1")
        app.state.history.add(HistoryRecord(path=str(saved), filename="trailer.mp4", chat_id=-1001, msg_id=8))
        view.watch(entry)
        page = scheduled[-1][0].__self__
        await page.open()
        assert page.url == str(saved) and not app.state.streams.sources
        app.back()
    finally:
        await app.state.streams.close()


def test_history_offers_play_for_videos(app, tmp_path):
    saved = tmp_path / "clip.mp4"
    saved.write_bytes(b"1")
    app.state.history.add(HistoryRecord(path=str(saved), filename="clip.mp4"))
    app.show("history")
    row = app.history.list.controls[-1]
    menu = row.content.controls[-1]
    assert [i.content for i in menu.items][:3] == ["Play", "Open with...", "Share"]


async def test_queue_plays_and_opens_finished_files(app, tmp_path, monkeypatch):
    video, doc = tmp_path / "clip.mkv", tmp_path / "notes.pdf"
    video.write_bytes(b"1")
    doc.write_bytes(b"1")
    manager = app.state.manager
    manager.items += [
        DownloadItem(id="v", kind="telegram", title="clip", state=ItemState.DONE, path=str(video)),
        DownloadItem(id="p", kind="telegram", title="notes", state=ItemState.DONE, path=str(doc)),
        DownloadItem(id="g", kind="telegram", title="gone", state=ItemState.DONE, path=str(tmp_path / "gone.mp4")),
    ]
    app.open_queue()
    tiles = app.queue.tiles
    assert [i.content for i in tiles["v"].menu.items] == ["Play", "Open with...", "Share"]
    assert tiles["v"].action.visible and tiles["v"].action.tooltip == "Play"
    assert [i.content for i in tiles["p"].menu.items] == ["Open with...", "Share"]
    assert tiles["p"].action.tooltip == "Open with..."
    assert not tiles["g"].menu.visible and not tiles["g"].action.visible

    scheduled = []
    app.page.run_task = lambda fn, *a: scheduled.append((fn, a))
    tiles["v"]._quick()
    assert scheduled[-1][0].__self__.path == str(video)          # the in-app player
    tiles["p"]._quick()
    assert scheduled[-1] == (app.open_with, (str(doc),))

    # Android: the system chooser gets the file and a type players accept
    calls = []

    async def chooser(path, mime, title):
        calls.append((path, mime))
        return "no_app" if path.endswith(".pdf") else "opened"

    monkeypatch.setattr(app.native, "available", True)
    monkeypatch.setattr(app.native, "open_with", chooser)
    await app.open_with(str(video))
    assert calls == [(str(video), "video/*")] or calls == [(str(video), "video/x-matroska")]
    await app.open_with(str(doc))
    assert "No app" in toast(app)
    await app.open_with(str(tmp_path / "gone.mp4"))
    assert "no longer" in toast(app) and len(calls) == 2


def test_mime_for_offers_videos_to_players():
    from tgdl.storage import mime_for

    assert mime_for("a.mp4") == "video/mp4"
    assert mime_for("a.ts").startswith("video/")
    assert mime_for("a.MKV").startswith("video/")
    assert mime_for("a.pdf") == "application/pdf"
    assert mime_for("noext") is None


async def test_settings_backup_export_and_import(app, tmp_path, monkeypatch):
    from tgdl.backup import loads
    from tgdl.links.rules import LinkRule

    st = app.state
    st.cfg.api_id, st.cfg.api_hash = 25073761, "0123456789abcdef0123456789abcdef"
    st.rules.add(LinkRule(id="mirror", pattern="mysite.com/{channel}/{msg}"))
    st.library.add(LibraryEntry(chat_id=-1001, title="Movies"))

    async def no_dialog(**kw):
        raise RuntimeError("no system dialog here")

    monkeypatch.setattr(app.file_picker, "save_file", no_dialog)
    path = await app.settings.backup.export(password="pw")
    assert path and path.startswith(st.manager.downloads_dir) and "tg-downloader-backup" in path
    raw = open(path, "rb").read()
    assert b"0123456789abcdef" not in raw
    assert loads(raw, "pw").rule_count == 1

    # a fresh phone: nothing set up yet
    st.cfg.api_id, st.cfg.api_hash = 0, ""
    st.rules.remove("mirror")
    st.library.remove(-1001)
    calls = []

    async def set_creds(api_id, api_hash):
        calls.append((api_id, api_hash))

    async def not_yet():
        return False

    async def nothing():
        return None

    monkeypatch.setattr(st.session, "set_api_credentials", set_creds)
    monkeypatch.setattr(st.session, "is_authorized", not_yet)
    monkeypatch.setattr(app, "on_login_changed", nothing)
    opened = []
    monkeypatch.setattr(app, "open_login", lambda *a: opened.append(1))

    await app.settings.backup.read(raw)              # asks for the password first
    dialog = app.page.show_dialog.call_args[0][0]
    dialog.content.value = "pw"
    await dialog.actions[1].on_click(None)
    confirm = app.page.show_dialog.call_args[0][0]
    assert "Import" in confirm.title.value
    await confirm.actions[1].on_click(None)

    assert calls == [(25073761, "0123456789abcdef0123456789abcdef")]
    assert st.rules.get("mirror") is not None and -1001 in st.library
    assert "Imported" in toast(app) and opened == [1]   # still needs the one-time phone login


async def test_backup_wrong_password_asks_again(app):
    from tgdl.backup import dumps, make_backup

    raw = dumps(make_backup(app.state.cfg), password="right")
    await app.settings.backup.read(raw)
    dialog = app.page.show_dialog.call_args[0][0]
    dialog.content.value = "wrong"
    await dialog.actions[1].on_click(None)
    again = app.page.show_dialog.call_args[0][0]
    assert again.content.error == "Wrong password, or the file was changed."


async def test_download_to_a_custom_folder(app, monkeypatch, tmp_path):
    entity = chan(-1001, "Dev")
    msgs = [FakeMessage(id=i, text=f"clip {i}", size=1000 * i) for i in (5, 4, 3, 2)]

    async def fake_fetch(client, ent, f, offset_id=0, min_id=0, limit=40):
        return MediaPage([entry_for(m) for m in msgs], 0, 5)

    async def connected():
        return "client"

    monkeypatch.setattr(channel_mod, "fetch_media", fake_fetch)
    monkeypatch.setattr(app.state.session, "ensure_connected", connected)
    st = app.state
    st.library.add(LibraryEntry(chat_id=-1001, title="Dev"))
    (tmp_path / "dl" / "Dev" / "session").mkdir(parents=True)
    (tmp_path / "dl" / "Dev" / "session" / "old.mp4").write_bytes(b"1")

    view = app.open_channel(-1001, "Dev", entity=entity)
    await view.reload()

    # select two, tap "To folder...": the sheet lists the main folder, the existing subfolder, and a field
    view.on_row_long_press(view.rows[5].entry)
    view.toggle(4)
    assert view.to_folder_btn.visible
    view.download_selected_to_folder()
    sheet = app.page.show_dialog.call_args[0][0]
    tiles = [c for c in sheet.content.content.controls if isinstance(c, ft.ListTile)]
    assert [t.title.value for t in tiles] == ["Dev (main folder)", "session"]
    assert tiles[1].subtitle.value == "1 file"

    # type a new name: both are queued into it, remembered for the picker, and the selection ends
    field = next(c for c in sheet.content.content.controls if isinstance(c, ft.Row)).controls[0]
    field.value = " auth "
    app.page.pop_dialog = MagicMock()
    next(c for c in sheet.content.content.controls if isinstance(c, ft.Row)).controls[1].on_click(None)
    items = st.manager.items
    assert [(i.msg_id, i.subfolder) for i in items] == [(5, "auth"), (4, "auth")]
    assert st.library.get(-1001).subfolders == ["auth"] and not view.selecting
    assert "to auth" in toast(app)
    assert [n for n, _ in st.subfolder_choices(-1001, "Dev")] == ["auth", "session"]

    # picking the main folder keeps the old behaviour
    view.download_to_folder([view.rows[3].entry])
    sheet = app.page.show_dialog.call_args[0][0]
    next(c for c in sheet.content.content.controls if isinstance(c, ft.ListTile)).on_click(None)
    assert items[-1].msg_id == 3 and items[-1].subfolder == ""

    # an empty name is refused, nothing is queued
    view.download_to_folder([view.rows[2].entry])
    sheet = app.page.show_dialog.call_args[0][0]
    row = next(c for c in sheet.content.content.controls if isinstance(c, ft.Row))
    row.controls[0].value = "  "
    row.controls[1].on_click(None)
    assert len(items) == 3

    # queue rows and finished rows show where the file went
    assert "Dev / auth" in app.queue.tiles[items[0].id].detail.value if items[0].id in app.queue.tiles else True
    path = tmp_path / "dl" / "Dev" / "auth" / "clip 5.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"1")
    st.history.add(HistoryRecord(path=str(path), filename="clip 5.mp4", chat_id=-1001, msg_id=5))
    assert view.subfolder_tag(view.rows[5].entry) == "auth"
    assert view.subfolder_tag(view.rows[3].entry) == ""
