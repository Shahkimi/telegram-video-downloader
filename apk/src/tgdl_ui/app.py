"""The app shell: navigation, toasts, incoming links, clipboard offer and the download ticker."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Callable
from urllib.parse import urlparse

import flet as ft

from tgdl.diagnostics import writable_dir
from tgdl.engine.models import DownloadItem, ItemState
from tgdl.links.model import RouteResult
from tgdl.links.router import route
from tgdl.paths import DOWNLOAD_SUBDIR
from tgdl.telegram.session import LoginError
from tgdl.util import format_speed

from .native import Native
from .state import AppState, get_state
from .views.channels import ChannelsView
from .views.diagnostics import DiagnosticsView
from .views.download import DownloadView
from .views.login import LoginDialog
from .views.queue import QueueView
from .views.rules import RulesView
from .views.settings import SettingsView

log = logging.getLogger("tgdl.ui")

SEED_COLOR = ft.Colors.BLUE_700
FAST_TICK = 0.3
SLOW_TICK = 2.0
KEEP_ALIVE_EVERY = 2.0
NAV_BOTTOM_SPACE = 96

PERM_STORAGE = "android.permission.WRITE_EXTERNAL_STORAGE"
PERM_NOTIFY = "android.permission.POST_NOTIFICATIONS"

# Hosts worth a "download this?" offer when copied. Other web pages are left alone.
VIDEO_HOSTS = (
    "youtube.com", "youtu.be", "tiktok.com", "instagram.com", "facebook.com", "fb.watch", "x.com", "twitter.com",
    "vimeo.com", "dailymotion.com", "reddit.com", "twitch.tv", "bilibili.com", "streamable.com", "rumble.com",
)

TABS = [
    ("download", "Download", ft.Icons.DOWNLOAD_OUTLINED, ft.Icons.DOWNLOAD),
    ("channels", "Channels", ft.Icons.FORUM_OUTLINED, ft.Icons.FORUM),
    ("queue", "Queue", ft.Icons.LIST_ALT_OUTLINED, ft.Icons.LIST_ALT),
    ("settings", "Settings", ft.Icons.SETTINGS_OUTLINED, ft.Icons.SETTINGS),
]
TAB_KEYS = [t[0] for t in TABS]


def worth_offering(routed: RouteResult) -> bool:
    """Should a copied link trigger the 'download this?' prompt?"""
    if routed.telegram or routed.channels:
        return True
    for ext in routed.external:
        if ext.rule_id != "builtin:ytdlp":
            return True  # the user wrote a rule for it
        host = (urlparse(ext.url).hostname or "").lower()
        if any(host == h or host.endswith("." + h) for h in VIDEO_HOSTS):
            return True
    return False


class App:
    def __init__(self, page: ft.Page, state: AppState):
        self.page = page
        self.state = state
        state.active_app = self
        self.current = "download"
        self.visible = True
        self._toast_seq = 0
        self._batch: set[str] = set()
        self._asked_notifications = False
        self._native_failures = 0

        # Flet drops a service as soon as nothing references it, so these stay on the instance.
        self.clipboard = ft.Clipboard()
        self.share = ft.Share()
        self.file_picker = ft.FilePicker()
        self.wakelock = ft.Wakelock()
        self.storage = ft.StoragePaths()
        self.native = Native(page, self.on_shared)

        self.download = DownloadView(self)
        self.channels = ChannelsView(self)
        self.queue = QueueView(self)
        self.settings = SettingsView(self)
        self.views: dict[str, Any] = {
            "download": self.download,
            "channels": self.channels,
            "queue": self.queue,
            "settings": self.settings,
        }

        self.body = ft.Container(expand=True, padding=ft.Padding.only(left=16, right=16, top=8, bottom=0))
        self.nav = ft.NavigationBar(
            selected_index=0,
            destinations=[ft.NavigationBarDestination(icon=icon, selected_icon=selected, label=label) for _, label, icon, selected in TABS],
            on_change=self._on_nav,
        )
        self._build_toast()

    # ======================================================================= startup
    async def start(self) -> None:
        page = self.page
        page.title = "TG Downloader"
        page.padding = 0
        page.theme_mode = ft.ThemeMode.SYSTEM
        page.theme = ft.Theme(color_scheme_seed=SEED_COLOR)
        page.dark_theme = ft.Theme(color_scheme_seed=SEED_COLOR)
        if page.platform in (ft.PagePlatform.WINDOWS, ft.PagePlatform.LINUX, ft.PagePlatform.MACOS):
            page.window.width, page.window.height = 440, 860  # roughly a phone, handy while developing
        page.on_app_lifecycle_state_change = self._on_lifecycle
        page.navigation_bar = self.nav
        page.overlay.append(self.toast_box)
        page.add(ft.SafeArea(expand=True, content=self.body))

        self.state.manager.on_busy_change = self._on_busy_change
        self.show("download")
        page.run_task(self._ticker)
        page.run_task(self._startup)

    async def _startup(self) -> None:
        st = self.state
        try:
            sdk = await self.native.sdk_int()
            if st.paths.is_android and 0 < sdk <= 29:
                await self.native.request_permission(PERM_STORAGE)
            await self._prepare_download_dir()

            pending = await self.native.take_pending()
            for text in pending:
                await self.handle_incoming(text, autostart=st.cfg.android.autostart_shared)

            if st.manager.busy:
                await self._apply_busy(True)
            if not pending:
                if not (st.cfg.api_id and st.cfg.api_hash):
                    self.open_login()  # first run: nothing works until they log in
                else:
                    await self._offer_clipboard()
        except Exception:  # noqa: BLE001 - startup must never leave a blank screen
            log.exception("startup failed")

    async def _prepare_download_dir(self) -> None:
        """Make sure there is a folder we can write to; fall back to app storage when the public one is blocked."""
        st = self.state
        target = st.cfg.downloads_path(st.paths)
        status = writable_dir(target)
        if status == "ok":
            st.manager.downloads_dir_override = None
            return
        log.warning("download folder %s: %s", target, status)
        candidates: list[str] = []
        try:
            external = await self.storage.get_external_storage_directory()
            if external:
                candidates.append(os.path.join(external, DOWNLOAD_SUBDIR))
        except Exception:  # noqa: BLE001
            pass
        candidates.append(str(st.paths.data_dir / "downloads"))
        for candidate in candidates:
            if writable_dir(candidate) == "ok":
                st.manager.downloads_dir_override = candidate
                self.toast(f"Cannot save to {target}. Using {candidate} instead.", error=True)
                return
        self.toast(f"No writable download folder found. {status}", error=True)

    # ======================================================================= navigation
    def _on_nav(self, e: ft.Event) -> None:
        self.show(TAB_KEYS[int(self.nav.selected_index or 0)])

    def show(self, key: str) -> None:
        view = self.views[key]
        self.current = key
        self.body.content = view.root
        index = TAB_KEYS.index(key)
        if self.nav.selected_index != index:
            self.nav.selected_index = index
        self.page.update()
        view.on_show()

    def show_panel(self, title: str, root: ft.Control, back_to: str = "settings") -> None:
        header = ft.Row(
            [ft.IconButton(ft.Icons.ARROW_BACK, tooltip="Back", on_click=lambda e: self.show(back_to)),
             ft.Text(title, size=22, weight=ft.FontWeight.BOLD)],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self.current = "panel"
        self.body.content = ft.Column([header, root], expand=True, spacing=4)
        self.page.update()

    def open_rules(self) -> None:
        self.show_panel("Link rules", RulesView(self).root)

    def open_diagnostics(self) -> None:
        self.show_panel("Diagnostics", DiagnosticsView(self).root)

    # ======================================================================= toasts
    def _build_toast(self) -> None:
        self.toast_text = ft.Text("", size=14, color=ft.Colors.ON_INVERSE_SURFACE, expand=True, max_lines=4, overflow=ft.TextOverflow.ELLIPSIS)
        self.toast_action = ft.TextButton("", visible=False, style=ft.ButtonStyle(color=ft.Colors.INVERSE_PRIMARY))
        self.toast_close = ft.IconButton(ft.Icons.CLOSE, icon_size=18, icon_color=ft.Colors.ON_INVERSE_SURFACE, on_click=lambda e: self._hide_toast())
        self.toast_box = ft.Container(
            visible=False,
            left=12,
            right=12,
            bottom=NAV_BOTTOM_SPACE,
            padding=ft.Padding.only(left=16, right=4, top=2, bottom=2),
            border_radius=12,
            bgcolor=ft.Colors.INVERSE_SURFACE,
            content=ft.Row([self.toast_text, self.toast_action, self.toast_close], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=0),
        )

    def toast(self, text: str, *, action: str | None = None, on_action: Callable[[ft.Event], Any] | None = None, error: bool = False) -> None:
        """A short message above the navigation bar. Own overlay on purpose: a snack bar would sit on Flet's dialog stack
        and make pop_dialog() close the wrong thing."""
        self._toast_seq += 1
        seq = self._toast_seq
        self.toast_text.value = text
        self.toast_box.bgcolor = ft.Colors.ERROR_CONTAINER if error else ft.Colors.INVERSE_SURFACE
        self.toast_text.color = ft.Colors.ON_ERROR_CONTAINER if error else ft.Colors.ON_INVERSE_SURFACE
        self.toast_close.icon_color = self.toast_text.color
        self.toast_action.visible = bool(action)
        if action:
            self.toast_action.content = action

            def clicked(e: ft.Event) -> None:
                self._hide_toast()
                if on_action:
                    on_action(e)  # must be a plain function: Flet only awaits handlers that are coroutine functions

            self.toast_action.on_click = clicked
        self.toast_box.visible = True
        self._safe_update(self.toast_box)
        self.page.run_task(self._hide_toast_later, seq, 8.0 if (action or error) else 3.5)

    async def _hide_toast_later(self, seq: int, seconds: float) -> None:
        await asyncio.sleep(seconds)
        if seq == self._toast_seq:
            self._hide_toast()

    def _hide_toast(self) -> None:
        self.toast_box.visible = False
        self._safe_update(self.toast_box)

    @staticmethod
    def _safe_update(control: ft.Control) -> None:
        try:
            control.update()
        except Exception:  # noqa: BLE001 - the control may not be on a page yet
            pass

    # ======================================================================= login
    async def ensure_login(self) -> bool:
        ok, error = await self.state.session.check()
        if ok:
            return True
        if error is not None and error.kind == "network":
            self.toast(error.message, error=True)
        else:
            self.open_login()
        return False

    def open_login(self, on_done: Callable[[bool], None] | None = None) -> None:
        LoginDialog(self, on_done).open()

    async def on_login_changed(self) -> None:
        self.state.dialogs = None
        await self.download.refresh_login_banner()
        await self.settings.refresh_account()
        if self.current == "channels":
            self.channels.on_show()

    # ======================================================================= incoming links
    def on_shared(self, text: str) -> None:
        """Called by the native extension when another app shares text to us while we are running."""
        self.page.run_task(self.handle_incoming, text, self.state.cfg.android.autostart_shared)

    async def handle_incoming(self, text: str, autostart: bool = False) -> None:
        text = (text or "").strip()
        if not text:
            return
        st = self.state
        routed = route(text, st.rules, default_channel=st.cfg.default_channel,
                       ytdlp_enabled=st.cfg.ytdlp.enabled, range_cap=st.cfg.range_cap)
        self.show("download")
        existing = (self.download.field.value or "").strip()
        if existing and not autostart and text not in existing:
            text = existing + "\n" + text
        self.download.set_text(text)
        if routed.empty:
            self.download.render_route(routed)
            self.toast("No supported link found in that text", error=True)
            return
        if autostart:
            for _ in range(300):  # another add may still be resolving; wait for it instead of dropping this one
                if not self.download._busy:
                    break
                await asyncio.sleep(0.2)
            await self.download.add_to_queue(auto=True)
        else:
            self.download.render_route(routed)
            self.toast("Link ready. Tap Add to queue.")

    async def _offer_clipboard(self) -> None:
        st = self.state
        if not st.cfg.android.clipboard_autodetect:
            return
        await asyncio.sleep(0.4)  # Android only lets the focused window read the clipboard
        try:
            text = ((await self.clipboard.get()) or "").strip()
        except Exception:  # noqa: BLE001
            return
        if not text or len(text) > 4000 or text == st.last_clipboard_seen:
            return
        st.last_clipboard_seen = text
        routed = route(text, st.rules, default_channel=None, ytdlp_enabled=st.cfg.ytdlp.enabled, range_cap=st.cfg.range_cap)
        if not worth_offering(routed):
            return
        self.toast("There is a link in your clipboard", action="Download",
                   on_action=lambda e: self.page.run_task(self.handle_incoming, text, True))

    def scan_peer(self, peer: Any) -> None:
        self.show("channels")
        self.page.run_task(self.channels.scan_peer, peer)

    async def share_file(self, path: str) -> None:
        try:
            await self.share.share_files([ft.ShareFile.from_path(path)])
        except Exception as exc:  # noqa: BLE001
            self.toast(f"Cannot share this file: {exc}", error=True)

    # ======================================================================= app lifecycle
    def _on_lifecycle(self, e: ft.AppLifecycleStateChangeEvent) -> None:
        state = e.state
        self.visible = state in (ft.AppLifecycleState.RESUME, ft.AppLifecycleState.SHOW)
        if state is ft.AppLifecycleState.RESUME:
            self.page.run_task(self._on_resume)

    async def _on_resume(self) -> None:
        try:
            self._native_failures = 0
            for text in await self.native.take_pending():
                await self.handle_incoming(text, autostart=self.state.cfg.android.autostart_shared)
            await self._offer_clipboard()
        except Exception:  # noqa: BLE001
            log.exception("resume handling failed")

    # ======================================================================= downloads in the background
    def _on_busy_change(self, busy: bool) -> None:
        self.page.run_task(self._apply_busy, busy)

    async def _apply_busy(self, busy: bool) -> None:
        cfg = self.state.cfg
        try:
            if busy:
                self._batch = set()
                if cfg.android.keep_alive:
                    await self.native.start_keep_alive("TG Downloader", "Downloading...")
                    if not self._asked_notifications:
                        self._asked_notifications = True
                        if await self.native.sdk_int() >= 33:
                            await self.native.request_permission(PERM_NOTIFY)
                if cfg.android.keep_screen_on:
                    await self.wakelock.enable()
            else:
                await self.native.stop_keep_alive()
                await self.wakelock.disable()
                self._announce_finished()
        except Exception:  # noqa: BLE001
            log.exception("could not apply busy state %s", busy)

    def _announce_finished(self) -> None:
        summary = self.state.manager.summary(self._batch_items())
        if not summary.total:
            return
        parts = []
        if summary.succeeded:
            parts.append(f"{summary.succeeded} saved")
        if summary.failed:
            parts.append(f"{summary.failed} failed")
        if summary.cancelled:
            parts.append(f"{summary.cancelled} cancelled")
        self.toast("Downloads finished: " + ", ".join(parts or ["nothing new"]),
                   action="Queue" if self.current != "queue" else None,
                   on_action=lambda e: self.show("queue"), error=bool(summary.failed and not summary.succeeded))

    def _batch_items(self) -> list[DownloadItem]:
        by_id = {i.id: i for i in self.state.manager.items}
        return [by_id[i] for i in self._batch if i in by_id]

    def _batch_progress(self) -> tuple[int, int]:
        manager = self.state.manager
        for item in manager.items:
            if not item.state.finished:
                self._batch.add(item.id)
        done = total = 0
        for item in self._batch_items():
            size = item.total or 0
            total += size
            done += size if item.state in (ItemState.DONE, ItemState.SKIPPED) else min(item.done, size)
        return done, total

    async def _ticker(self) -> None:
        """Drives the screen while this App is the active one. A newer App (after Android recreated the page) ends it."""
        last_slow = 0.0
        while self.state.active_app is self:
            try:
                finished = self.queue.tick()
                paths = [i.path for i in finished if i.state is ItemState.DONE and i.path]
                if paths:
                    self.page.run_task(self._scan_files, paths)
                now = time.monotonic()
                if now - last_slow >= KEEP_ALIVE_EVERY:
                    last_slow = now
                    await self._slow_tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("ticker error")
            await asyncio.sleep(FAST_TICK if self.visible else SLOW_TICK)

    async def _slow_tick(self) -> None:
        manager = self.state.manager
        items = manager.items
        active = [i for i in items if i.state is ItemState.DOWNLOADING]
        waiting = sum(1 for i in items if i.state is ItemState.QUEUED)

        label = f"Queue ({len(active) + waiting})" if (active or waiting) else "Queue"
        destination = self.nav.destinations[TAB_KEYS.index("queue")]
        if destination.label != label:
            destination.label = label
            self._safe_update(self.nav)

        if manager.busy and self.state.cfg.android.keep_alive and self.native.available and self._native_failures < 3:
            done, total = self._batch_progress()
            speed = sum(i.speed for i in active)
            text = f"{len(active)} downloading at {format_speed(speed)}" if active else "Starting..."
            if waiting:
                text += f", {waiting} waiting"
            percent = int(done * 100 / total) if total else -1
            try:
                await asyncio.wait_for(self.native.update_keep_alive(text, percent), timeout=3)
                self._native_failures = 0
            except Exception:  # noqa: BLE001 - the screen may be gone; the service keeps its last text
                self._native_failures += 1

    async def _scan_files(self, paths: list[str]) -> None:
        for path in paths[:50]:
            try:
                await self.native.scan_file(path)
            except Exception:  # noqa: BLE001
                log.debug("media scan failed for %s", path, exc_info=True)


async def main(page: ft.Page) -> None:
    state = get_state()
    await state.bind_loop()
    app = App(page, state)
    await app.start()
