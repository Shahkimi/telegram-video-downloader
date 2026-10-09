"""Browse tab: your Telegram chats (open one, or add it to the Library) and the box for pasting links."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import flet as ft

from tgdl.config import parse_channel
from tgdl.telegram.dialogs import ChatCategory, DialogInfo, list_dialogs
from tgdl.telegram.session import LoginError

from ..widgets import banner, cover, empty_state, muted, safe_update

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger("tgdl.ui.browse")

MAX_ROWS = 300
COVERS_PER_PASS = 40

CATEGORY_ICON = {
    ChatCategory.PRIVATE_CHANNEL: ft.Icons.LOCK,
    ChatCategory.PRIVATE_GROUP: ft.Icons.GROUP,
    ChatCategory.PUBLIC_CHANNEL: ft.Icons.CAMPAIGN,
    ChatCategory.PUBLIC_GROUP: ft.Icons.FORUM,
}


class BrowseView:
    def __init__(self, app: "App"):
        self.app = app
        self.section = "chats"
        self.category: ChatCategory | None = None
        self.loading = False
        self._cover_slots: dict[int, ft.Container] = {}

        self.appbar = ft.AppBar(
            title=ft.Text("Browse"),
            actions=[ft.IconButton(ft.Icons.REFRESH, tooltip="Reload chats", on_click=self._on_reload)],
        )
        self.section_chips = ft.Row(spacing=6)
        self.login_banner = ft.Container(visible=False)
        self.search = ft.TextField(hint_text="Search your chats", prefix_icon=ft.Icons.SEARCH, dense=True, on_change=self._on_search)
        self.chips = ft.Row(wrap=False, spacing=6, scroll=ft.ScrollMode.AUTO)
        self.manual = ft.TextField(label="Open by id or username", hint_text="-1001234567890 or @name", dense=True, expand=True,
                                   on_submit=self._on_manual)
        self.status = muted("")
        self.list = ft.ListView(expand=True, spacing=0, padding=ft.Padding.only(bottom=24))

        self.chats_box = ft.Column(
            expand=True,
            spacing=8,
            controls=[
                self.login_banner,
                self.search,
                self.chips,
                ft.Row([self.manual, ft.IconButton(ft.Icons.ARROW_FORWARD, tooltip="Open", on_click=self._on_manual)],
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
                self.status,
                self.list,
            ],
        )
        self.links_box = ft.Container(content=app.download.root, expand=True, visible=False)
        self.root = ft.Column(expand=True, spacing=8, controls=[self.section_chips, self.chats_box, self.links_box])
        self._build_section_chips()
        self._build_chips()

    # ---- sections ------------------------------------------------------------------
    def _build_section_chips(self) -> None:
        self.section_chips.controls = [
            ft.Chip(label=ft.Text("Chats"), leading=ft.Icon(ft.Icons.FORUM_OUTLINED), selected=self.section == "chats",
                    on_select=lambda e: self.select("chats")),
            ft.Chip(label=ft.Text("Links"), leading=ft.Icon(ft.Icons.LINK), selected=self.section == "links",
                    on_select=lambda e: self.select("links")),
        ]

    def select(self, section: str) -> None:
        self.section = section
        self.chats_box.visible = section == "chats"
        self.links_box.visible = section == "links"
        self._build_section_chips()
        safe_update(self.root)
        if section == "links":
            self.app.download.on_show()
        elif self.app.current == "browse":
            self.app.page.run_task(self._show)

    def _build_chips(self) -> None:
        options: list[tuple[ChatCategory | None, str]] = [(None, "All")] + [(c, c.label + "s") for c in ChatCategory]
        self.chips.controls = [
            ft.Chip(label=ft.Text(label), selected=(self.category is cat), on_select=lambda e, cat=cat: self._select_category(cat))
            for cat, label in options
        ]

    def _select_category(self, cat: ChatCategory | None) -> None:
        self.category = cat
        self._build_chips()
        self._render_list()
        safe_update(self.root)

    def _on_search(self, e: ft.Event) -> None:
        self._render_list()
        safe_update(self.root)

    # ---- showing the tab ----------------------------------------------------------
    def on_show(self) -> None:
        if self.section == "links":
            self.app.download.on_show()
        else:
            self.app.page.run_task(self._show)

    async def _show(self) -> None:
        st = self.app.state
        ok, error = await st.session.check()
        if not ok:
            if error is not None and error.kind == "network":
                self.login_banner.content = banner("Cannot reach Telegram. Check your internet connection.", "warn")
            else:
                self.login_banner.content = banner("Log in to Telegram to see your channels and groups.", "info",
                                                   ft.TextButton("Log in", on_click=lambda e: self.app.open_login()))
            self.login_banner.visible = True
            self.list.controls = []
            safe_update(self.root)
            return
        self.login_banner.visible = False
        if st.dialogs is None:
            await self.reload()
        else:
            self._render_list()
            safe_update(self.root)
            await self._load_covers()

    def _on_reload(self, e: ft.Event) -> None:
        self.app.page.run_task(self.reload)

    async def reload(self) -> None:
        if self.loading:
            return
        st = self.app.state
        self.loading = True
        self.status.value = "Loading your chats..."
        safe_update(self.root)
        try:
            client = await st.session.ensure_connected()
            st.dialogs = await list_dialogs(client)
            for d in st.dialogs:
                st.entities[d.id] = d.entity
            self.status.value = ""
        except LoginError as exc:
            self.status.value = exc.message
        except Exception as exc:  # noqa: BLE001
            log.exception("loading chats failed")
            self.status.value = f"Could not load chats: {exc}"
        finally:
            self.loading = False
        self._render_list()
        safe_update(self.root)
        await self._load_covers()

    # ---- the chat list ---------------------------------------------------------------------
    def _shown(self) -> list[DialogInfo]:
        dialogs = self.app.state.dialogs or []
        needle = (self.search.value or "").strip().lower()
        return [d for d in dialogs
                if (self.category is None or d.category is self.category) and (not needle or needle in d.name.lower())]

    def _render_list(self) -> None:
        dialogs = self.app.state.dialogs or []
        shown = self._shown()
        if dialogs and not shown:
            self.status.value = "No chats match."
        elif self.status.value == "No chats match.":
            self.status.value = ""
        self._cover_slots = {}
        rows: list[ft.Control] = [self._row(d) for d in shown[:MAX_ROWS]]
        if len(shown) > MAX_ROWS:
            rows.append(muted(f"+ {len(shown) - MAX_ROWS} more. Search to narrow the list."))
        if not dialogs and not self.loading and not self.status.value:
            rows = [empty_state(ft.Icons.FORUM_OUTLINED, "No chats loaded yet.",
                                ft.TextButton("Load chats", on_click=self._on_reload))]
        self.list.controls = rows

    def _row(self, d: DialogInfo) -> ft.Control:
        name = d.name.replace("\n", " ")
        slot = ft.Container(content=cover(None, name, width=44, height=44, radius=22, text_size=16), width=44, height=44)
        self._cover_slots[d.id] = slot
        in_library = d.id in self.app.state.library
        bookmark = ft.IconButton(
            icon=ft.Icons.FAVORITE if in_library else ft.Icons.FAVORITE_BORDER,
            icon_color=ft.Colors.PRIMARY if in_library else None,
            tooltip="Remove from library" if in_library else "Add to library",
            on_click=lambda e, d=d: self._toggle_library(d, e.control),
        )
        return ft.ListTile(
            leading=slot,
            title=ft.Text(name, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
            subtitle=ft.Row([ft.Icon(CATEGORY_ICON[d.category], size=14, color=ft.Colors.ON_SURFACE_VARIANT),
                             ft.Text(d.category.label, size=12)], spacing=4),
            trailing=bookmark,
            content_padding=ft.Padding.only(left=0, right=0),
            on_click=lambda e, d=d: self._open(d),
        )

    async def _load_covers(self) -> None:
        for chat_id, slot in list(self._cover_slots.items())[:COVERS_PER_PASS * 3]:
            entity = self.app.state.entities.get(chat_id)
            data = await self.app.cover_for(chat_id, entity)
            if data and self._cover_slots.get(chat_id) is slot:
                title = next((d.name for d in self.app.state.dialogs or [] if d.id == chat_id), "")
                slot.content = cover(data, title, width=44, height=44, radius=22)
                safe_update(slot)

    def _open(self, d: DialogInfo) -> None:
        self.app.open_channel(d.id, d.name, entity=d.entity, username=getattr(d.entity, "username", None), category=d.category.value)

    def _toggle_library(self, d: DialogInfo, button: Any) -> None:
        st = self.app.state
        if d.id in st.library:
            st.library.remove(d.id)
            st.save_library()
            self.app.toast(f"Removed {d.name} from the library")
        else:
            self.app.library.add_chat(d.id, d.name, d.entity, d.category.value)
            self.app.toast(f"Added {d.name} to the library")
        in_library = d.id in st.library
        button.icon = ft.Icons.FAVORITE if in_library else ft.Icons.FAVORITE_BORDER
        button.icon_color = ft.Colors.PRIMARY if in_library else None
        button.tooltip = "Remove from library" if in_library else "Add to library"
        safe_update(button)

    def _on_manual(self, e: ft.Event) -> None:
        raw = (self.manual.value or "").strip().lstrip("@")
        if not raw:
            self.app.toast("Type a channel id or username")
            return
        self.app.scan_peer(parse_channel(raw))
