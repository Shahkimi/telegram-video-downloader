"""Bottom sheet: choose a folder inside the chat's folder for the media about to be downloaded."""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

import flet as ft

from tgdl.storage import subfolder_name

from ..widgets import muted, safe_update

if TYPE_CHECKING:
    from ..app import App


def open_folder_picker(app: "App", chat_id: int, chat_title: str, count: int, on_pick: Callable[[str], None]) -> ft.BottomSheet:
    """Calls on_pick('') for the chat's own folder or on_pick(name) for a subfolder (new names are created on download)."""
    page = app.page
    st = app.state
    base = st.folder_for(chat_id, chat_title)
    what = f"{count} item{'s' if count != 1 else ''}"

    def pick(name: str) -> Callable[[ft.Event], None]:
        def handler(e: ft.Event) -> None:
            page.pop_dialog()
            on_pick(name)
        return handler

    rows: list[ft.Control] = [
        ft.Text(f"Download {what} to...", size=16, weight=ft.FontWeight.BOLD),
        ft.ListTile(leading=ft.Icon(ft.Icons.FOLDER_SPECIAL), title=ft.Text(f"{chat_title} (main folder)"),
                    subtitle=muted(base, size=11, max_lines=1), on_click=pick("")),
    ]
    for name, files in st.subfolder_choices(chat_id, chat_title):
        rows.append(ft.ListTile(leading=ft.Icon(ft.Icons.FOLDER), title=ft.Text(name),
                                subtitle=muted(f"{files} file{'s' if files != 1 else ''}", size=11), on_click=pick(name)))

    field = ft.TextField(label="New folder name", dense=True, expand=True, max_length=60)
    error = ft.Text("", size=12, color=ft.Colors.ERROR)

    def create(e: ft.Event) -> None:
        name = subfolder_name(field.value or "")
        if not name:
            error.value = "Type a folder name."
            safe_update(error)
            return
        pick(name)(e)

    field.on_submit = create
    rows.append(ft.Divider())
    rows.append(ft.Row([field, ft.Button("Create", icon=ft.Icons.CREATE_NEW_FOLDER, on_click=create)], spacing=8))
    rows.append(error)

    sheet = ft.BottomSheet(
        scrollable=True,
        content=ft.Container(padding=ft.Padding.only(left=16, right=16, top=16, bottom=28),
                             content=ft.Column(rows, tight=True, spacing=4, scroll=ft.ScrollMode.AUTO)),
    )
    page.show_dialog(sheet)
    return sheet
