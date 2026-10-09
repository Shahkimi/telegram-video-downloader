"""Builds the real Flet controls with a mocked page. Catches typos, bad keyword arguments and missing attributes
that would otherwise only show up on a phone."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import flet as ft
import pytest

from tgdl.engine.models import DownloadItem, ItemState
from tgdl.links.rules import LinkRule
from tgdl_ui import state as ui_state
from tgdl_ui.app import TAB_KEYS, App, worth_offering
from tgdl_ui.views.diagnostics import DiagnosticsView
from tgdl_ui.views.rules import RulesView


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("TGDL_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setattr(ui_state, "_state", None)
    state = ui_state.get_state()
    page = MagicMock()
    page.platform = ft.PagePlatform.WINDOWS
    instance = App(page, state)
    yield instance
    state.active_app = None
    monkeypatch.setattr(ui_state, "_state", None)


def last_toast(app: App) -> str:
    return app.toast_text.value


def test_every_tab_renders(app):
    assert TAB_KEYS == ["library", "updates", "history", "browse", "more"]
    for key in TAB_KEYS:
        app.show(key)
        assert app.body.content is app.views[key].root
        assert app.nav.selected_index == TAB_KEYS.index(key)
        assert app.page.appbar is app.views[key].appbar


def test_pages_stack_and_close(app):
    app.page.views = [object()]
    app.open_rules()
    app.open_diagnostics()
    assert app.current == "panel" and len(app._stack) == 2 and len(app.page.views) == 3
    app.back()
    assert len(app._stack) == 1 and app.current == "panel"
    app._on_view_pop(ft.ViewPopEvent(name="view_pop", control=app.page, route="/page1", view=app._stack[0][0]))
    assert not app._stack and app.current == app.tab and len(app.page.views) == 1
    app.show("settings")
    assert app.current == "panel" and app.top_owner() is app.settings
    app.show("history")  # switching tabs closes pages on top
    assert not app._stack and app.current == "history"


def test_toast_shows_error_and_action(app):
    clicked = []
    app.toast("Hello", action="Undo", on_action=lambda e: clicked.append(1))
    assert app.toast_box.visible and last_toast(app) == "Hello"
    assert app.toast_action.visible
    app.toast_action.on_click(None)
    assert clicked == [1]
    assert not app.toast_box.visible

    app.toast("Broken", error=True)
    assert not app.toast_action.visible
    assert app.toast_box.bgcolor == ft.Colors.ERROR_CONTAINER


async def test_incoming_text_without_a_link(app):
    await app.handle_incoming("hello there, nothing to see")
    assert app.current == "browse" and app.browse.section == "links"
    assert "No supported link" in last_toast(app)


async def test_incoming_telegram_link_waits_for_confirmation(app):
    await app.handle_incoming("look https://t.me/c/1234567890/15 nice", autostart=False)
    assert "t.me/c/1234567890/15" in app.download.field.value
    assert "Link ready" in last_toast(app)
    assert app.download.results.controls  # the preview lines are drawn


async def test_second_share_is_appended_not_lost(app):
    await app.handle_incoming("https://t.me/c/1234567890/15", autostart=False)
    await app.handle_incoming("https://t.me/c/1234567890/16", autostart=False)
    assert app.download.field.value.count("t.me/c/") == 2


def test_queue_view_follows_manager_events(app):
    manager, sink = app.state.manager, app.state.sink
    item = DownloadItem(id="a1", kind="telegram", title="clip", filename="clip.mp4", total=1000, done=250, state=ItemState.DOWNLOADING)
    manager.items.append(item)
    sink.added(item)
    app.show("queue")
    assert "a1" in app.queue.tiles
    item.done = 500
    sink.progress(item)
    app.queue.tick()
    assert "50%" in app.queue.tiles["a1"].detail.value

    item.state, item.path = ItemState.DONE, "x.mp4"
    sink.finished(item)
    finished = app.queue.tick()
    assert [i.id for i in finished] == ["a1"]


def test_settings_changes_are_saved(app):
    s = app.settings
    s.max_gb.value = "5"
    s._save_max_gb(None)
    s.default_channel.value = "-1001234567890"
    s._save_default_channel(None)
    s.connections.value = "12"
    s._save_connections(None)
    s.height.value = "0"
    s._save_height(None)

    saved = json.loads(app.state.paths.config_file.read_text(encoding="utf-8"))
    assert saved["max_total_size_bytes"] == 5 * 1024 ** 3
    assert saved["default_channel"] == -1001234567890
    assert saved["parallel_connections"] == 12
    assert saved["ytdlp"]["max_height"] in (None, 0)


def test_settings_reject_bad_size(app):
    before = app.state.cfg.max_total_size_bytes
    app.settings.max_gb.value = "abc"
    app.settings._save_max_gb(None)
    assert app.state.cfg.max_total_size_bytes == before
    assert "number" in last_toast(app)


def test_rules_view_lists_tests_and_removes(app):
    app.state.rules.add(LinkRule(id="mirror", name="My mirror", pattern="mysite.com/{channel}/{msg}"))
    app.state.save_rules()
    view = RulesView(app)
    assert any("My mirror" in str(getattr(c, "title", "")) or getattr(getattr(c, "title", None), "value", "") == "My mirror"
               for c in view.list.controls)

    view.test_field.value = "https://mysite.com/durov/12-13"
    view._on_test(None)
    assert any("durov" in c.value for c in view.test_out.controls)

    view._toggle("mirror", False)
    assert app.state.rules.get("mirror").enabled is False
    saved = json.loads(app.state.paths.rules_file.read_text(encoding="utf-8"))
    assert saved["rules"][0]["enabled"] is False


def test_rules_editor_opens_for_new_and_existing(app):
    view = RulesView(app)
    view.edit(None)
    app.state.rules.add(LinkRule(id="r1", pattern="a.example/{msg}", target="ytdlp"))
    view.refresh()
    view.edit(app.state.rules.get("r1"))
    assert app.page.show_dialog.call_count == 2


def test_diagnostics_view_has_rows(app):
    view = DiagnosticsView(app)
    assert len(view.facts.controls) >= 10
    assert view.log_box.value


def test_worth_offering(app):
    from tgdl.links.router import route

    st = app.state
    assert worth_offering(route("https://t.me/c/1234567890/15", st.rules))
    assert worth_offering(route("https://www.youtube.com/watch?v=abcdefghijk", st.rules))
    assert not worth_offering(route("https://example.com/some/article", st.rules))
    assert not worth_offering(route("just words", st.rules))


def test_batch_progress_ignores_older_items(app):
    manager = app.state.manager
    old = DownloadItem(id="old", kind="telegram", title="old", total=100, done=100, state=ItemState.DONE)
    new = DownloadItem(id="new", kind="telegram", title="new", total=200, done=50, state=ItemState.DOWNLOADING)
    manager.items.extend([old, new])
    app._batch = set()
    done, total = app._batch_progress()
    assert (done, total) == (50, 200)  # the finished item from before this batch does not dilute the percentage
