"""Small reusable UI pieces."""
from __future__ import annotations

from typing import Callable

import flet as ft

from tgdl.engine.models import DownloadItem, ItemState
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


STATE_ICON = {
    ItemState.QUEUED: (ft.Icons.SCHEDULE, MUTED),
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
    if s is ItemState.DONE:
        return f"Saved  -  {format_size(item.total or item.done)}"
    if s is ItemState.SKIPPED:
        return "Already downloaded, skipped"
    if s is ItemState.CANCELLED:
        return "Cancelled"
    return item.error or "Failed"


class DownloadTile:
    """One row of the queue. update_from() changes the existing controls in place."""

    def __init__(self, item: DownloadItem, on_action: Callable[[str, DownloadItem], None]):
        self.item_id = item.id
        self.on_action = on_action
        self.icon = ft.Icon(ft.Icons.SCHEDULE, size=24)
        self.name = ft.Text("", size=14, weight=ft.FontWeight.W_500, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)
        self.detail = ft.Text("", size=12, color=MUTED, max_lines=3, overflow=ft.TextOverflow.ELLIPSIS)
        self.bar = ft.ProgressBar(value=0, bar_height=4, border_radius=2)
        self.action = ft.IconButton(icon=ft.Icons.CANCEL, icon_size=22, on_click=lambda e: self._clicked(item))
        self._item = item
        self.control = ft.Card(
            content=ft.Container(
                padding=ft.Padding.symmetric(horizontal=12, vertical=10),
                content=ft.Column(
                    spacing=6,
                    controls=[
                        ft.Row(
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            spacing=10,
                            controls=[self.icon, ft.Column([self.name, self.detail], spacing=2, expand=True), self.action],
                        ),
                        self.bar,
                    ],
                ),
            )
        )
        self.update_from(item)

    def _clicked(self, item: DownloadItem) -> None:
        self.on_action(self._action_name(), self._item)

    def _action_name(self) -> str:
        s = self._item.state
        if s in (ItemState.QUEUED, ItemState.DOWNLOADING, ItemState.RESOLVING):
            return "cancel"
        if s in (ItemState.FAILED, ItemState.CANCELLED):
            return "retry"
        return "share"

    def update_from(self, item: DownloadItem) -> None:
        self._item = item
        icon, color = STATE_ICON[item.state]
        self.icon.icon, self.icon.color = icon, color
        self.name.value = item.display_name
        self.detail.value = describe_item(item)
        self.detail.color = ft.Colors.ERROR if item.state is ItemState.FAILED else MUTED

        active = item.state in (ItemState.DOWNLOADING, ItemState.RESOLVING, ItemState.QUEUED)
        self.bar.visible = active
        if item.state is ItemState.QUEUED or (active and not item.total):
            self.bar.value = None if item.state is not ItemState.QUEUED else 0
        else:
            self.bar.value = min(1.0, item.done / item.total) if item.total else 0

        act = self._action_name()
        self.action.icon = {"cancel": ft.Icons.CANCEL, "retry": ft.Icons.REPLAY, "share": ft.Icons.SHARE}[act]
        self.action.tooltip = {"cancel": "Cancel", "retry": "Try again", "share": "Share file"}[act]
        self.action.visible = not (act == "share" and not item.path)
