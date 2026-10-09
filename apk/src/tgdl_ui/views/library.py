"""Library tab: the chats you follow, as a grid of covers with downloaded and new counts (like a Tachiyomi library)."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import flet as ft

from tgdl.library import LibraryEntry
from tgdl.telegram.browse import newest_message_id

from ..widgets import cover, empty_state, pill, safe_update

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger("tgdl.ui.library")

SORTS = {
    "title": "Title",
    "unread": "New first",
    "added": "Recently added",
    "downloaded": "Most downloaded",
}


class LibraryView:
    def __init__(self, app: "App"):
        self.app = app
        self.sort = "title"
        self.as_list = False
        self._slots: dict[int, ft.Container] = {}

        self.search = ft.TextField(hint_text="Search library", prefix_icon=ft.Icons.SEARCH, dense=True, visible=False,
                                   on_change=lambda e: self.render())
        self.sort_menu = ft.PopupMenuButton(icon=ft.Icons.SORT, tooltip="Sort", items=[])
        self.appbar = ft.AppBar(
            title=ft.Text("Library"),
            actions=[
                ft.IconButton(ft.Icons.SEARCH, tooltip="Search", on_click=self._toggle_search),
                self.sort_menu,
                ft.IconButton(ft.Icons.VIEW_LIST, tooltip="List or grid", on_click=self._toggle_layout),
                ft.IconButton(ft.Icons.REFRESH, tooltip="Check for new media", on_click=self._on_refresh),
            ],
        )
        self.grid = ft.GridView(expand=True, max_extent=150, child_aspect_ratio=0.68, spacing=8, run_spacing=8,
                                padding=ft.Padding.only(top=4, bottom=24))
        self.list = ft.ListView(expand=True, spacing=0, visible=False, padding=ft.Padding.only(bottom=24))
        self.empty = empty_state(ft.Icons.COLLECTIONS_BOOKMARK_OUTLINED,
                                 "Your library is empty.\nOpen Browse and tap the heart next to a channel.",
                                 ft.Button("Browse chats", icon=ft.Icons.EXPLORE, on_click=lambda e: self.app.show("browse")))
        self.root = ft.Column(expand=True, spacing=6, controls=[self.search, self.empty, self.grid, self.list])
        self._build_sort_menu()

    # ---- toolbar --------------------------------------------------------------------
    def _build_sort_menu(self) -> None:
        self.sort_menu.items = [
            ft.PopupMenuItem(content=label, checked=self.sort == key, on_click=lambda e, k=key: self._set_sort(k))
            for key, label in SORTS.items()
        ]

    def _set_sort(self, key: str) -> None:
        self.sort = key
        self._build_sort_menu()
        self.render()

    def _toggle_search(self, e: ft.Event) -> None:
        self.search.visible = not self.search.visible
        if not self.search.visible:
            self.search.value = ""
        self.render()

    def _toggle_layout(self, e: ft.Event) -> None:
        self.as_list = not self.as_list
        e.control.icon = ft.Icons.GRID_VIEW if self.as_list else ft.Icons.VIEW_LIST
        safe_update(self.appbar)
        self.render()

    def _on_refresh(self, e: ft.Event) -> None:
        self.app.show("updates")
        self.app.page.run_task(self.app.updates.refresh)

    # ---- data -------------------------------------------------------------------------------
    def add_chat(self, chat_id: int, title: str, entity: Any = None, category: str = "") -> LibraryEntry:
        """Add a chat. Posts that already exist do not count as new; the newest message id is looked up in the background."""
        entry = self.app.state.add_to_library(chat_id, title, getattr(entity, "username", None), category)
        if entity is not None:
            self.app.state.entities[chat_id] = entity
            self.app.page.run_task(self._mark_seen, entry, entity)
        return entry

    async def _mark_seen(self, entry: LibraryEntry, entity: Any) -> None:
        try:
            client = await self.app.state.session.ensure_connected()
            newest = await newest_message_id(client, entity)
        except Exception:  # noqa: BLE001
            return
        if newest > entry.last_seen_id:
            entry.last_seen_id = newest
            self.app.state.save_library()

    def _entries(self) -> list[LibraryEntry]:
        st = self.app.state
        entries = st.library.entries()
        needle = (self.search.value or "").strip().lower() if self.search.visible else ""
        if needle:
            entries = [e for e in entries if needle in e.title.lower()]
        if self.sort == "unread":
            entries.sort(key=lambda e: -e.unread)
        elif self.sort == "added":
            entries.sort(key=lambda e: -e.added_at)
        elif self.sort == "downloaded":
            counts = self._counts()
            entries.sort(key=lambda e: -counts.get(e.chat_id, 0))
        return entries

    def _counts(self) -> dict[int, int]:
        counts: dict[int, int] = {}
        for r in self.app.state.history.records:
            if r.chat_id is not None and r.msg_id is not None:
                counts[r.chat_id] = counts.get(r.chat_id, 0) + 1
        return counts

    # ---- drawing -----------------------------------------------------------------------------
    def on_show(self) -> None:
        self.render()
        self.app.page.run_task(self._load_covers)

    def render(self) -> None:
        entries = self._entries()
        counts = self._counts()
        self._slots = {}
        self.empty.visible = not len(self.app.state.library)
        self.grid.visible = not self.as_list and bool(entries)
        self.list.visible = self.as_list and bool(entries)
        if self.as_list:
            self.list.controls = [self._list_row(e, counts.get(e.chat_id, 0)) for e in entries]
            self.grid.controls = []
        else:
            self.grid.controls = [self._card(e, counts.get(e.chat_id, 0)) for e in entries]
            self.list.controls = []
        safe_update(self.root)

    def _badges(self, entry: LibraryEntry, downloaded: int) -> ft.Row:
        badges: list[ft.Control] = []
        if downloaded:
            badges.append(pill(str(downloaded), ft.Colors.TERTIARY, ft.Colors.ON_TERTIARY))
        if entry.unread:
            badges.append(pill(str(entry.unread)))
        if entry.nomedia:
            badges.append(pill("hidden", ft.Colors.SURFACE_CONTAINER_HIGHEST, ft.Colors.ON_SURFACE))
        return ft.Row(badges, spacing=2)

    def _card(self, entry: LibraryEntry, downloaded: int) -> ft.Control:
        slot = ft.Container(content=cover(None, entry.title, radius=8, text_size=34), expand=True)
        self._slots[entry.chat_id] = slot
        shade = ft.Container(
            left=0, right=0, bottom=0, height=64,
            border_radius=ft.BorderRadius.only(bottom_left=8, bottom_right=8),
            gradient=ft.LinearGradient(begin=ft.Alignment.TOP_CENTER, end=ft.Alignment.BOTTOM_CENTER,
                                       colors=[ft.Colors.TRANSPARENT, "#CC000000"]),
        )
        name = ft.Container(
            left=6, right=6, bottom=6,
            content=ft.Text(entry.title, size=12, weight=ft.FontWeight.W_600, color=ft.Colors.WHITE, max_lines=2,
                            overflow=ft.TextOverflow.ELLIPSIS),
        )
        return ft.Container(
            border_radius=8,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            ink=True,
            on_click=lambda e, en=entry: self.open(en),
            on_long_press=lambda e, en=entry: self.show_actions(en),
            content=ft.Stack(
                expand=True,
                controls=[
                    ft.Container(content=slot, left=0, right=0, top=0, bottom=0),
                    shade,
                    name,
                    ft.Container(content=self._badges(entry, downloaded), left=4, top=4),
                ],
            ),
        )

    def _list_row(self, entry: LibraryEntry, downloaded: int) -> ft.Control:
        slot = ft.Container(content=cover(None, entry.title, width=44, height=56, radius=6, text_size=16), width=44, height=56)
        self._slots[entry.chat_id] = slot
        return ft.ListTile(
            leading=slot,
            title=ft.Text(entry.title, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
            subtitle=ft.Text(f"{downloaded} downloaded" + (f"  -  {entry.unread} new" if entry.unread else ""), size=12),
            trailing=self._badges(entry, 0),
            content_padding=ft.Padding.only(left=0, right=0),
            on_click=lambda e, en=entry: self.open(en),
            on_long_press=lambda e, en=entry: self.show_actions(en),
        )

    async def _load_covers(self) -> None:
        for chat_id, slot in list(self._slots.items()):
            entry = self.app.state.library.get(chat_id)
            if entry is None:
                continue
            data = await self.app.cover_for(chat_id)
            if data is None and chat_id not in self.app.state.entities:
                continue
            if data and self._slots.get(chat_id) is slot:
                if self.as_list:
                    slot.content = cover(data, entry.title, width=44, height=56, radius=6)
                else:
                    slot.content = cover(data, entry.title, radius=8)
                safe_update(slot)

    # ---- actions --------------------------------------------------------------------------------
    def open(self, entry: LibraryEntry) -> None:
        self.app.open_channel(entry.chat_id, entry.title, entity=self.app.state.entities.get(entry.chat_id),
                              username=entry.username, category=entry.category)

    def show_actions(self, entry: LibraryEntry) -> None:
        page = self.app.page
        st = self.app.state

        def run(action: str) -> None:
            page.pop_dialog()
            if action == "open":
                self.open(entry)
            elif action == "folder":
                page.run_task(self.app.channel_folder_dialog, entry.chat_id, entry.title)
            elif action == "hide":
                page.run_task(self.app.set_chat_hidden, entry.chat_id, entry.title, not entry.nomedia)
            elif action == "seen":
                entry.unread = 0
                st.save_library()
                self.render()
            elif action == "remove":
                st.library.remove(entry.chat_id)
                st.save_library()
                self.app.toast(f"Removed {entry.title}. Downloaded files stay on the phone.")
                self.render()

        folder = st.folder_for(entry.chat_id, entry.title)
        tiles = [
            ft.ListTile(leading=ft.Icon(ft.Icons.OPEN_IN_NEW), title=ft.Text("Open"), on_click=lambda e: run("open")),
            ft.ListTile(leading=ft.Icon(ft.Icons.FOLDER_OPEN), title=ft.Text("Download folder"),
                        subtitle=ft.Text(folder, size=11, max_lines=2), on_click=lambda e: run("folder")),
            ft.ListTile(leading=ft.Icon(ft.Icons.VISIBILITY if entry.nomedia else ft.Icons.VISIBILITY_OFF),
                        title=ft.Text("Show in gallery" if entry.nomedia else "Hide from gallery (.nomedia)"),
                        on_click=lambda e: run("hide")),
        ]
        if entry.unread:
            tiles.append(ft.ListTile(leading=ft.Icon(ft.Icons.DONE_ALL), title=ft.Text("Mark as seen"), on_click=lambda e: run("seen")))
        tiles.append(ft.ListTile(leading=ft.Icon(ft.Icons.DELETE_OUTLINE), title=ft.Text("Remove from library"),
                                 on_click=lambda e: run("remove")))
        page.show_dialog(ft.BottomSheet(
            content=ft.Container(
                padding=ft.Padding.only(top=12, bottom=24),
                content=ft.Column([ft.Container(ft.Text(entry.title, size=16, weight=ft.FontWeight.BOLD, max_lines=1),
                                                padding=ft.Padding.symmetric(horizontal=16)), *tiles], tight=True, spacing=0),
            ),
        ))
