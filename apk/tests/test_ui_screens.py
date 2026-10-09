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
