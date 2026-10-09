"""Small reusable UI pieces."""
from __future__ import annotations

import os
from typing import Callable

import flet as ft

from tgdl.engine.models import DownloadItem, ItemState
from tgdl.storage import is_video
from tgdl.util import format_eta, format_size, format_speed

MUTED = ft.Colors.ON_SURFACE_VARIANT


def title(text: str) -> ft.Text:
    return ft.Text(text, size=22, weight=ft.FontWeight.BOLD)


def section(text: str) -> ft.Text:
    return ft.Text(text, size=13, weight=ft.FontWeight.BOLD, color=ft.Colors.PRIMARY)


def muted(text: str, size: int = 12, **kw) -> ft.Text:
    return ft.Text(text, size=size, color=MUTED, **kw)


def banner(text: str, kind: str = "info", action: ft.Control | None = None) -> ft.Container:
    colors = {
        "info": (ft.Colors.PRIMARY_CONTAINER, ft.Colors.ON_PRIMARY_CONTAINER, ft.Icons.INFO_OUTLINE),
        "warn": (ft.Colors.TERTIARY_CONTAINER, ft.Colors.ON_TERTIARY_CONTAINER, ft.Icons.WARNING_AMBER),
        "error": (ft.Colors.ERROR_CONTAINER, ft.Colors.ON_ERROR_CONTAINER, ft.Icons.ERROR_OUTLINE),
    }
    bg, fg, icon = colors.get(kind, colors["info"])
    row: list[ft.Control] = [
        ft.Icon(icon, color=fg, size=20),
        ft.Text(text, color=fg, size=13, expand=True),
    ]
    if action is not None:
        row.append(action)
    return ft.Container(
        content=ft.Row(row, vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=10),
        bgcolor=bg,
        border_radius=12,
        padding=ft.Padding.symmetric(horizontal=12, vertical=10),
    )


COVER_COLORS = [
    ft.Colors.BLUE_400, ft.Colors.TEAL_400, ft.Colors.DEEP_PURPLE_400, ft.Colors.PINK_400,
    ft.Colors.ORANGE_400, ft.Colors.GREEN_500, ft.Colors.INDIGO_400, ft.Colors.RED_400,
]


def initials(text: str) -> str:
    words = [w for w in (text or "").replace("_", " ").split() if w[:1].isalnum()]
    return "".join(w[0] for w in words[:2]).upper() or "?"


def cover(data: bytes | None, label: str, *, width: float | None = None, height: float | None = None,
          radius: float = 10, text_size: int = 28) -> ft.Container:
    """A chat's picture, or its initials on a colour picked from the name when it has none."""
    if data:
        content: ft.Control = ft.Image(src=data, fit=ft.BoxFit.COVER, width=width, height=height)
        bg = None
    else:
        content = ft.Text(initials(label), size=text_size, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE)
        bg = COVER_COLORS[sum(map(ord, label or "?")) % len(COVER_COLORS)]
    return ft.Container(content=content, width=width, height=height, bgcolor=bg, border_radius=radius,
                        alignment=ft.Alignment.CENTER, clip_behavior=ft.ClipBehavior.ANTI_ALIAS)


def pill(text: str, bg: str = ft.Colors.PRIMARY, fg: str = ft.Colors.ON_PRIMARY) -> ft.Container:
    """A small count badge, like the unread/downloaded numbers on a Tachiyomi library cover."""
    return ft.Container(
        content=ft.Text(text, size=11, weight=ft.FontWeight.BOLD, color=fg),
        bgcolor=bg,
        padding=ft.Padding.symmetric(horizontal=6, vertical=1),
        border_radius=4,
    )


def empty_state(icon: str, text: str, action: ft.Control | None = None) -> ft.Container:
    controls: list[ft.Control] = [
        ft.Icon(icon, size=56, color=MUTED),
        ft.Text(text, size=14, color=MUTED, text_align=ft.TextAlign.CENTER),
    ]
    if action is not None:
        controls.append(action)
    return ft.Container(
        content=ft.Column(controls, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=10, tight=True),
        alignment=ft.Alignment.CENTER,
        padding=ft.Padding.symmetric(vertical=48, horizontal=24),
    )


def safe_update(control: ft.Control) -> None:
    try:
        control.update()
    except Exception:  # noqa: BLE001 - the control may not be on a page yet
        pass


STATE_ICON = {
    ItemState.QUEUED: (ft.Icons.SCHEDULE, MUTED),
    ItemState.PAUSED: (ft.Icons.PAUSE_CIRCLE, ft.Colors.TERTIARY),
    ItemState.RESOLVING: (ft.Icons.SYNC, ft.Colors.PRIMARY),
    ItemState.DOWNLOADING: (ft.Icons.DOWNLOAD, ft.Colors.PRIMARY),
    ItemState.DONE: (ft.Icons.CHECK_CIRCLE, ft.Colors.GREEN),
    ItemState.SKIPPED: (ft.Icons.DONE_ALL, ft.Colors.GREEN),
    ItemState.FAILED: (ft.Icons.ERROR, ft.Colors.ERROR),
    ItemState.CANCELLED: (ft.Icons.CANCEL, MUTED),
}


def describe_item(item: DownloadItem) -> str:
    s = item.state
    if s is ItemState.DOWNLOADING or s is ItemState.RESOLVING:
        if item.total:
            return (f"{item.percent:.0f}%  -  {format_size(item.done)} / {format_size(item.total)}"
                    f"  -  {format_speed(item.speed)}  -  ETA {format_eta(item.eta)}")
        return f"{format_size(item.done)}  -  {format_speed(item.speed)}"
    if s is ItemState.QUEUED:
        return "Waiting for a free slot" + (f"  -  {format_size(item.total)}" if item.total else "")
    if s is ItemState.PAUSED:
        return "Paused" + (f"  -  {format_size(item.total)}" if item.total else "")
    if s is ItemState.DONE:
        return f"Saved  -  {format_size(item.total or item.done)}"
    if s is ItemState.SKIPPED:
        return "Already downloaded, skipped"
    if s is ItemState.CANCELLED:
        return "Cancelled"
    return item.error or "Failed"


MENU_LABELS = {
    "pause": ("Pause", ft.Icons.PAUSE),
    "resume": ("Resume", ft.Icons.PLAY_ARROW),
    "top": ("Move to top", ft.Icons.VERTICAL_ALIGN_TOP),
    "cancel": ("Cancel", ft.Icons.CLOSE),
    "retry": ("Try again", ft.Icons.REPLAY),
    "play": ("Play", ft.Icons.PLAY_ARROW),
    "open_with": ("Open with...", ft.Icons.OPEN_IN_NEW),
    "share": ("Share", ft.Icons.SHARE),
    "channel": ("Open channel", ft.Icons.FOLDER_SPECIAL),
}


def item_actions(item: DownloadItem) -> list[str]:
    """What the menu of a queue row offers. The first entry is also the quick button."""
    s = item.state
    if s in (ItemState.DOWNLOADING, ItemState.RESOLVING):
        out = ["pause", "cancel"]
    elif s is ItemState.QUEUED:
        out = ["pause", "top", "cancel"]
    elif s is ItemState.PAUSED:
        out = ["resume", "cancel"]
    elif s in (ItemState.FAILED, ItemState.CANCELLED):
        out = ["retry"]
    elif item.path and os.path.isfile(item.path):
        out = (["play"] if is_video(item.path) else []) + ["open_with", "share"]
    else:
        out = []
    if item.chat_id is not None:
        out.append("channel")
    return out


class DownloadTile:
    """One row of the download queue. update_from() changes the existing controls in place."""

    QUICK = {"pause": ft.Icons.PAUSE, "resume": ft.Icons.PLAY_ARROW, "retry": ft.Icons.REPLAY,
             "play": ft.Icons.PLAY_CIRCLE_OUTLINE, "open_with": ft.Icons.OPEN_IN_NEW}

    def __init__(self, item: DownloadItem, on_action: Callable[[str, DownloadItem], None]):
        self.item_id = item.id
        self.on_action = on_action
        self.icon = ft.Icon(ft.Icons.SCHEDULE, size=24)
        self.name = ft.Text("", size=14, weight=ft.FontWeight.W_500, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)
        self.detail = ft.Text("", size=12, color=MUTED, max_lines=3, overflow=ft.TextOverflow.ELLIPSIS)
        self.bar = ft.ProgressBar(value=0, bar_height=4, border_radius=2)
        self.action = ft.IconButton(icon=ft.Icons.CANCEL, icon_size=22, on_click=lambda e: self._quick())
        self.menu = ft.PopupMenuButton(icon=ft.Icons.MORE_VERT, items=[])
        self._item = item
        self._actions: list[str] | None = None   # None, so the first update_from() always fills the menu
        self.control = ft.Container(
            padding=ft.Padding.only(left=12, right=0, top=8, bottom=8),
            content=ft.Column(
                spacing=6,
                controls=[
                    ft.Row(
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=10,
                        controls=[self.icon, ft.Column([self.name, self.detail], spacing=2, expand=True), self.action, self.menu],
                    ),
                    self.bar,
                ],
            ),
        )
        self.update_from(item)

    def _quick(self) -> None:
        if self._actions and self._actions[0] in self.QUICK:
            self.on_action(self._actions[0], self._item)

    def _menu_item(self, action: str) -> ft.PopupMenuItem:
        label, icon = MENU_LABELS[action]
        return ft.PopupMenuItem(content=label, icon=icon, on_click=lambda e, a=action: self.on_action(a, self._item))

    def update_from(self, item: DownloadItem) -> None:
        self._item = item
        icon, color = STATE_ICON[item.state]
        self.icon.icon, self.icon.color = icon, color
        self.name.value = item.display_name
        detail = describe_item(item)
        if item.chat_title and item.state is not ItemState.FAILED:
            where = f"{item.chat_title} / {item.subfolder}" if item.subfolder else item.chat_title
            detail = f"{where}  -  {detail}"
        self.detail.value = detail
        self.detail.color = ft.Colors.ERROR if item.state is ItemState.FAILED else MUTED

        active = item.state in (ItemState.DOWNLOADING, ItemState.RESOLVING, ItemState.QUEUED)
        self.bar.visible = active
        if item.state is ItemState.QUEUED or (active and not item.total):
            self.bar.value = None if item.state is not ItemState.QUEUED else 0
        else:
            self.bar.value = min(1.0, item.done / item.total) if item.total else 0

        actions = item_actions(item)
        if actions != self._actions:
            self._actions = actions
            self.menu.items = [self._menu_item(a) for a in actions]
            self.menu.visible = bool(actions)
        quick = actions[0] if actions and actions[0] in self.QUICK else None
        self.action.visible = quick is not None
        if quick:
            self.action.icon = self.QUICK[quick]
            self.action.tooltip = MENU_LABELS[quick][0]
