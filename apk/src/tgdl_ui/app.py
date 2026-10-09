"""
The app shell, laid out like Tachiyomi: Library, Updates, History, Browse and More along the bottom, and full-screen
pages (a channel, the download queue, settings) pushed on top so Android's back gesture closes them.
Also: toasts, incoming links, the clipboard offer and the download ticker.
"""
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
from tgdl.storage import NOMEDIA, hidden_by, media_files, set_nomedia
from tgdl.telegram.browse import chat_cover, message_thumb
from tgdl.telegram.dialogs import classify, peer_id
from tgdl.util import format_speed

from .native import Native
from .state import AppState, get_state
from .views.browse import BrowseView
from .views.channel import ChannelView
from .views.diagnostics import DiagnosticsView
from .views.download import DownloadView
from .views.history import HistoryView
from .views.library import LibraryView
from .views.login import LoginDialog
from .views.more import MoreView
from .views.queue import QueueView
from .views.rules import RulesView
from .views.settings import SettingsView
from .views.updates import UpdatesView
from .widgets import safe_update

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
    ("library", "Library", ft.Icons.COLLECTIONS_BOOKMARK_OUTLINED, ft.Icons.COLLECTIONS_BOOKMARK),
    ("updates", "Updates", ft.Icons.NEW_RELEASES_OUTLINED, ft.Icons.NEW_RELEASES),
    ("history", "History", ft.Icons.HISTORY_OUTLINED, ft.Icons.HISTORY),
    ("browse", "Browse", ft.Icons.EXPLORE_OUTLINED, ft.Icons.EXPLORE),
    ("more", "More", ft.Icons.MORE_HORIZ_OUTLINED, ft.Icons.MORE_HORIZ),
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
        self.tab = "library"
        self.current = "library"
        self.visible = True
        self._toast_seq = 0
        self._batch: set[str] = set()
        self._asked_notifications = False
        self._native_failures = 0
        self._stack: list[tuple[ft.View, Any]] = []     # pushed pages and the object behind each
        self._covers: dict[int, bytes | None] = {}
        self._history_dirty = False

        # Flet drops a service as soon as nothing references it, so these stay on the instance.
        self.clipboard = ft.Clipboard()
        self.share = ft.Share()
        self.file_picker = ft.FilePicker()
        self.wakelock = ft.Wakelock()
        self.storage = ft.StoragePaths()
        self.native = Native(page, self.on_shared)

        self.download = DownloadView(self)
        self.queue = QueueView(self)
        self.library = LibraryView(self)
        self.updates = UpdatesView(self)
        self.history = HistoryView(self)
        self.browse = BrowseView(self)
        self.more = MoreView(self)
        self.settings = SettingsView(self)
        self.views: dict[str, Any] = {
            "library": self.library,
            "updates": self.updates,
            "history": self.history,
            "browse": self.browse,
            "more": self.more,
        }

        self.body = ft.Container(expand=True, padding=ft.Padding.only(left=16, right=16, top=0, bottom=0))
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
        page.on_view_pop = self._on_view_pop
        page.navigation_bar = self.nav
        page.overlay.append(self.toast_box)
        page.add(ft.SafeArea(expand=True, content=self.body))

        self.state.manager.on_busy_change = self._on_busy_change
        self.show("library")
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
        """Make sure there is a folder we can write to; fall back to app storage when the chosen one is blocked."""
        st = self.state
        target = st.cfg.downloads_path(st.paths)
        status = writable_dir(target)
        if status == "ok":
            st.manager.downloads_dir_override = None
            return
        log.warning("download folder %s: %s", target, status)
        if st.cfg.downloads_dir and not await self.native.has_all_files_access():
            self.toast("TG Downloader needs All files access to save into the folder you picked.", action="Allow",
                       on_action=lambda e: self.page.run_task(self.request_all_files_access), error=True)
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

    # ======================================================================= tabs
    def _on_nav(self, e: ft.Event) -> None:
        self.show(TAB_KEYS[int(self.nav.selected_index or 0)])

    def show(self, key: str) -> None:
        """Switch the bottom tab. Pages pushed on top are closed first."""
        if key == "download":
            key = "browse"
            self.browse.select("links")
        elif key == "queue":
            self.open_queue()
            return
        elif key == "settings":
            self.open_settings()
            return
        self._close_pages()
        view = self.views[key]
        self.tab = self.current = key
        self.body.content = view.root
        self.page.appbar = view.appbar
        index = TAB_KEYS.index(key)
        if self.nav.selected_index != index:
            self.nav.selected_index = index
        self.page.update()
        view.on_show()

    def set_badge(self, key: str, count: int) -> None:
        destination = self.nav.destinations[TAB_KEYS.index(key)]
        badge = str(count) if count else None
        if destination.badge != badge:
            destination.badge = badge
            safe_update(self.nav)

    # ======================================================================= pages on top
    def push(self, title: str | ft.Control, root: ft.Control, *, owner: Any = None,
             actions: list[ft.Control] | None = None, fab: ft.FloatingActionButton | None = None) -> ft.View:
        """Open a full-screen page above the tabs. Android's back gesture and the arrow in its bar close it."""
        bar = ft.AppBar(title=ft.Text(title) if isinstance(title, str) else title, actions=actions or [])
        view = ft.View(
            route=f"/page{len(self._stack) + 1}",
            padding=0,
            appbar=bar,
            floating_action_button=fab,
            controls=[ft.SafeArea(expand=True, content=ft.Container(root, expand=True, padding=ft.Padding.only(left=16, right=16)))],
        )
        self._stack.append((view, owner))
        self.page.views.append(view)
        self.current = "panel"
        self.page.update()
        return view

    def back(self) -> None:
        if self._stack:
            self._pop(self._stack[-1][0])

    def _on_view_pop(self, e: ft.ViewPopEvent) -> None:
        view = e.view or (self._stack[-1][0] if self._stack else None)
        if view is not None:
            self._pop(view)

    def _pop(self, view: ft.View) -> None:
        for n, (v, owner) in enumerate(self._stack):
            if v is view:
                del self._stack[n]
                if hasattr(owner, "on_close"):
                    try:
                        owner.on_close()
                    except Exception:  # noqa: BLE001
                        log.exception("closing a page failed")
                break
        try:
            self.page.views.remove(view)
        except ValueError:
            pass
        if not self._stack:
            self.current = self.tab
            self.views[self.tab].on_show()
        self.page.update()

    def _close_pages(self) -> None:
        while self._stack:
            view, owner = self._stack.pop()
            try:
                self.page.views.remove(view)
            except ValueError:
                pass
            if hasattr(owner, "on_close"):
                owner.on_close()

    def top_owner(self) -> Any:
        return self._stack[-1][1] if self._stack else None

    def open_queue(self) -> None:
        if isinstance(self.top_owner(), QueueView):
            return
        self.push("Download queue", self.queue.root, owner=self.queue, actions=self.queue.actions)
        self.queue.on_show()

    def open_settings(self) -> None:
        self.push("Settings", self.settings.root, owner=self.settings)
        self.settings.on_show()

    def settings_reloaded(self) -> None:
        """Settings changed underneath the screens (backup import): rebuild the settings page from the new values."""
        was_open = self.top_owner() is self.settings
        self.settings = SettingsView(self)
        self.more.hide_switch.value = self.state.cfg.nomedia
        self.library.render()
        if was_open:
            self.back()
            self.open_settings()

    def open_rules(self) -> None:
        self.push("Link rules", RulesView(self).root)

    def open_diagnostics(self) -> None:
        self.push("Diagnostics", DiagnosticsView(self).root)

    def open_channel(self, chat_id: int, title: str, entity: Any = None, username: str | None = None, category: str = "") -> ChannelView:
        view = ChannelView(self, chat_id, title, entity=entity, username=username, category=category)
        view.open()
        return view

    def open_entity(self, entity: Any) -> ChannelView | None:
        chat_id = peer_id(entity)
        if chat_id is None:
            self.toast("That is not a channel or group", error=True)
            return None
        self.state.entities[chat_id] = entity
        cat = classify(entity)
        return self.open_channel(chat_id, str(getattr(entity, "title", "") or chat_id), entity=entity,
                                 username=getattr(entity, "username", None), category=cat.value if cat else "")

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
        safe_update(self.toast_box)
        self.page.run_task(self._hide_toast_later, seq, 8.0 if (action or error) else 3.5)

    async def _hide_toast_later(self, seq: int, seconds: float) -> None:
        await asyncio.sleep(seconds)
        if seq == self._toast_seq:
            self._hide_toast()

    def _hide_toast(self) -> None:
        self.toast_box.visible = False
        safe_update(self.toast_box)

    _safe_update = staticmethod(safe_update)

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
        self.state.entities.clear()
        await self.download.refresh_login_banner()
        await self.settings.refresh_account()
        await self.more.refresh_account()
        if self.current == "browse":
            self.browse.on_show()

    # ======================================================================= pictures
    async def cover_for(self, chat_id: int | None, entity: Any = None) -> bytes | None:
        """A chat's photo from memory, disk, or Telegram (when the entity is known)."""
        if chat_id is None:
            return None
        if chat_id in self._covers:
            return self._covers[chat_id]
        st = self.state
        entity = entity or st.entities.get(chat_id)
        if entity is None:
            from tgdl.telegram.browse import cover_path

            path = cover_path(st.paths.cache_dir, chat_id)
            try:
                return path.read_bytes() or None
            except OSError:
                return None
        try:
            client = await st.session.ensure_connected()
            data = await chat_cover(client, entity, st.paths.cache_dir)
        except Exception:  # noqa: BLE001 - offline or logged out: initials are shown instead
            return None
        self._covers[chat_id] = data
        return data

    async def thumb_for(self, message: Any, chat_id: int | None) -> bytes | None:
        try:
            client = await self.state.session.ensure_connected()
            return await message_thumb(client, message, self.state.paths.cache_dir, chat_id)
        except Exception:  # noqa: BLE001
            return None

    async def entity_for(self, chat_id: int, peer: Any = None) -> Any:
        """Find a chat by id (or username). Loads the chat list once when Telethon does not know it yet."""
        st = self.state
        if chat_id in st.entities:
            return st.entities[chat_id]
        client = await st.session.ensure_connected()
        for attempt in (peer or chat_id, chat_id):
            try:
                entity = await client.get_entity(attempt)
                st.entities[chat_id] = entity
                return entity
            except Exception:  # noqa: BLE001 - not cached yet
                continue
        await client.get_dialogs(limit=None)
        entity = await client.get_entity(chat_id)
        st.entities[chat_id] = entity
        return entity

    # ======================================================================= storage: folders and .nomedia
    async def request_all_files_access(self) -> bool:
        """Android 11+: open the system switch for All files access. True when it is already on."""
        if await self.native.has_all_files_access():
            return True
        outcome = await self.native.request_all_files_access()
        if outcome == "failed":
            self.toast("Open Android Settings > Apps > TG Downloader > Permissions > Files and allow all files", error=True)
        elif outcome == "asked":
            self.toast("Turn on 'Allow access to manage all files', then come back")
        return outcome == "granted"

    async def pick_folder(self, title: str = "Choose a download folder") -> str | None:
        if self.state.paths.is_android and not await self.native.has_all_files_access():
            await self.request_all_files_access()
            return None
        try:
            path = await self.file_picker.get_directory_path(dialog_title=title)
        except Exception as exc:  # noqa: BLE001
            self.toast(f"Cannot open the folder picker: {exc}", error=True)
            return None
        if not path:
            return None
        if path.startswith("content://") or path.startswith("/tree/"):
            self.toast("That location cannot be used as a normal folder. Pick a folder on the phone or SD card.", error=True)
            return None
        status = writable_dir(path)
        if status != "ok":
            self.toast(f"Cannot save there: {status}", error=True)
            return None
        return path

    async def set_download_root_hidden(self, hidden: bool) -> None:
        """The global switch: .nomedia in the main download folder hides it and every channel folder inside."""
        st = self.state
        root = st.manager.downloads_dir
        st.cfg.nomedia = hidden
        st.save_config()
        try:
            set_nomedia(root, hidden)
        except OSError as exc:
            self.toast(f"Cannot change {root}: {exc}", error=True)
            return
        await self.refresh_gallery(root)
        self.toast("Downloads are hidden from the gallery" if hidden else "Downloads show in the gallery again")

    async def set_chat_hidden(self, chat_id: int, title: str, hidden: bool) -> None:
        """Per chat: .nomedia in that chat's folder. The chat joins the library so the choice is remembered."""
        st = self.state
        entry = st.library.get(chat_id) or self.library.add_chat(chat_id, title, st.entities.get(chat_id))
        entry.nomedia = hidden
        st.save_library()
        folder = st.folder_for(chat_id, title)
        try:
            set_nomedia(folder, hidden)
        except OSError as exc:
            self.toast(f"Cannot change {folder}: {exc}", error=True)
            return
        await self.refresh_gallery(folder)
        if not hidden and hidden_by(folder, None):
            self.toast("This folder is still hidden: the main download folder hides everything (More > Hide downloads).")
        else:
            self.toast(f"{title}: " + ("hidden from the gallery" if hidden else "shown in the gallery"))
        self.library.render()
        owner = self.top_owner()
        if isinstance(owner, ChannelView) and owner.chat_id == chat_id:
            owner.refresh_header()

    async def channel_folder_dialog(self, chat_id: int, title: str) -> None:
        """Let the user give one chat its own folder, or go back to the default."""
        st = self.state
        page = self.page
        entry = st.library.get(chat_id)
        current = st.folder_for(chat_id, title)

        async def choose(e: ft.Event) -> None:
            page.pop_dialog()
            picked = await self.pick_folder(f"Folder for {title}")
            if not picked:
                return
            chosen = st.library.get(chat_id) or self.library.add_chat(chat_id, title, st.entities.get(chat_id))
            chosen.folder = picked
            st.save_library()
            if chosen.nomedia:
                set_nomedia(picked, True)
            self.toast(f"{title} now saves to {picked}")
            self._after_folder_change(chat_id)

        def reset(e: ft.Event) -> None:
            page.pop_dialog()
            if entry is not None:
                entry.folder = None
                st.save_library()
            self.toast(f"{title} saves to {st.folder_for(chat_id, title)}")
            self._after_folder_change(chat_id)

        actions: list[ft.Control] = [ft.TextButton("Close", on_click=lambda e: page.pop_dialog())]
        if entry is not None and entry.folder:
            actions.insert(0, ft.TextButton("Use default", on_click=reset))
        actions.append(ft.Button("Choose folder", icon=ft.Icons.FOLDER_OPEN, on_click=choose))
        page.show_dialog(ft.AlertDialog(
            title=ft.Text("Download folder"),
            content=ft.Column(tight=True, spacing=8, controls=[
                ft.Text(current, selectable=True, size=13),
                ft.Text("New downloads from this chat go here. Files that are already saved are not moved.", size=12,
                        color=ft.Colors.ON_SURFACE_VARIANT),
            ]),
            actions=actions,
        ))

    def _after_folder_change(self, chat_id: int) -> None:
        self.library.render()
        owner = self.top_owner()
        if isinstance(owner, ChannelView) and owner.chat_id == chat_id:
            owner.refresh_header()

    async def refresh_gallery(self, folder: str) -> None:
        """Ask Android to look at a folder again so galleries hide or show it after .nomedia changed."""
        paths = media_files(folder)
        marker = os.path.join(folder, NOMEDIA)
        if os.path.exists(marker):
            paths.insert(0, marker)
        if paths:
            await self.native.scan_files(paths)

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
        """Open a whole channel (from a link or a typed id/username)."""
        self.page.run_task(self._open_peer, peer)

    async def _open_peer(self, peer: Any) -> None:
        try:
            if not await self.ensure_login():
                return
            client = await self.state.session.ensure_connected()
            entity = await client.get_entity(peer)
        except Exception as exc:  # noqa: BLE001
            self.toast(f"Cannot open {peer}: {exc}", error=True)
            return
        self.open_entity(entity)

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
            if isinstance(self.top_owner(), SettingsView):
                self.settings.on_show()  # the user may be back from the All files access screen
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
        if self.state.manager.paused:
            self.toast("Downloads paused", action="Queue", on_action=lambda e: self.open_queue())
            return
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
                   action="Queue" if not isinstance(self.top_owner(), QueueView) else None,
                   on_action=lambda e: self.open_queue(), error=bool(summary.failed and not summary.succeeded))

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

    def _record_finished(self, finished: list[DownloadItem]) -> None:
        st = self.state
        saved = [i for i in finished if i.state in (ItemState.DONE, ItemState.SKIPPED) and i.path]
        for item in saved:
            st.history.add_item(item)
        if saved:
            self._history_dirty = True
            fresh = [i.path for i in saved if i.state is ItemState.DONE]
            # Files under a .nomedia folder are still scanned: Android then files them as hidden instead of showing them.
            if fresh:
                self.page.run_task(self._scan_files, fresh)

    async def _ticker(self) -> None:
        """Drives the screen while this App is the active one. A newer App (after Android recreated the page) ends it."""
        last_slow = 0.0
        while self.state.active_app is self:
            try:
                dirty, structure, finished = self.state.sink.take()
                self.queue.apply(dirty, structure)
                if finished:
                    self._record_finished(finished)
                if dirty or structure:
                    owner = self.top_owner()
                    if isinstance(owner, ChannelView):
                        owner.on_downloads_changed()
                    elif self.current == "updates":
                        self.updates.on_downloads_changed()
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

        self.set_badge("more", len(active) + waiting)
        self.set_badge("updates", sum(e.unread for e in self.state.library.entries()))
        if self.current == "more":
            self.more.refresh_queue_line()
        if self._history_dirty:
            self._history_dirty = False
            self.state.save_history()
            if self.current == "history":
                self.history.on_show()

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
        await self.native.scan_files(paths[:200])


async def main(page: ft.Page) -> None:
    state = get_state()
    await state.bind_loop()
    app = App(page, state)
    await app.start()
