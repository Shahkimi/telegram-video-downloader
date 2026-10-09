"""Full-screen player: streams a Telegram video before it is downloaded, or plays a file that is already saved."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

import flet as ft

from tgdl.stream import StreamSource
from tgdl.util import format_size

from ..widgets import muted, safe_update

try:  # packaged with the app; missing only in a bare desktop dev setup
    import flet_video as fv
except ImportError:  # pragma: no cover - depends on the environment
    fv = None

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger("tgdl.ui.player")


def available() -> bool:
    return fv is not None


class PlayerPage:
    def __init__(self, app: "App", title: str, *, source: StreamSource | None = None, path: str | None = None,
                 on_download: Callable[[], Any] | None = None):
        self.app = app
        self.title = title
        self.source = source
        self.path = path
        self.on_download = on_download
        self.url: str | None = None
        self.video: Any = None
        self.status = muted("Loading...", size=12)

    async def open(self) -> None:
        if fv is None:
            self.app.toast("The video player is not part of this build", error=True)
            return
        try:
            if self.path:
                self.url = self.path
            else:
                if self.source is None:
                    return
                self.url = await self.app.state.streams.url_for(self.source)
        except Exception as exc:  # noqa: BLE001
            log.exception("cannot start the stream")
            self.app.toast(f"Cannot play this video: {exc}", error=True)
            return

        self.video = fv.Video(
            playlist=[fv.VideoMedia(self.url)],
            autoplay=True,
            expand=True,
            fill_color=ft.Colors.BLACK,
            fit=ft.BoxFit.CONTAIN,
            title=self.title,
            on_load=self._on_load,
            on_error=self._on_error,
        )
        if self.path:
            self.status.value = "Playing the downloaded file"
        else:
            self.status.value = (f"Streaming from Telegram, nothing is saved  -  {format_size(self.source.size)}"
                                 if self.source else "")
        buttons: list[ft.Control] = []
        actions: list[ft.Control] = []
        if self.on_download is not None:
            buttons.append(ft.Button("Download", icon=ft.Icons.DOWNLOAD, on_click=self._download))
            actions.append(ft.IconButton(ft.Icons.DOWNLOAD, tooltip="Download", on_click=self._download))
        if self.path:
            buttons.append(ft.OutlinedButton("Share", icon=ft.Icons.SHARE,
                                             on_click=lambda e: self.app.page.run_task(self.app.share_file, self.path)))
        root = ft.Column(
            expand=True,
            spacing=10,
            controls=[
                ft.Container(content=self.video, expand=True, bgcolor=ft.Colors.BLACK, border_radius=8,
                             clip_behavior=ft.ClipBehavior.ANTI_ALIAS),
                self.status,
                ft.Row(buttons, wrap=True, spacing=8),
                ft.Container(height=12),
            ],
        )
        self.app.push(self.title, root, owner=self, actions=actions)

    def on_close(self) -> None:
        if self.url and not self.path:
            self.app.state.streams.forget(self.url)
        self.video = None

    def _on_load(self, e: ft.Event) -> None:
        if not self.path:
            self.status.value = "Streaming from Telegram, nothing is saved. Seeking may take a moment."
            safe_update(self.status)

    def _on_error(self, e: ft.Event) -> None:
        detail = getattr(e, "data", None) or "unknown error"
        log.warning("player error: %s", detail)
        self.status.value = f"Cannot play this video: {detail}"
        self.status.color = ft.Colors.ERROR
        safe_update(self.status)

    def _download(self, e: ft.Event) -> None:
        if self.on_download is not None:
            self.on_download()
