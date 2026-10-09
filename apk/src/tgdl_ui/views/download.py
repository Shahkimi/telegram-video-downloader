"""Links section of Browse: paste links, see what they mean, add them to the download queue."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import flet as ft

from tgdl.links.model import RouteResult
from tgdl.links.router import route
from tgdl.telegram.resolve import MediaFilter, Note, resolve_targets
from tgdl.telegram.session import LoginError

from ..widgets import banner, muted

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger("tgdl.ui.download")


class DownloadView:
    def __init__(self, app: "App"):
        self.app = app
        self._busy = False

        self.login_banner = ft.Container(visible=False)
        self.field = ft.TextField(
            hint_text="Paste Telegram links, message ids, or any video page link. One or many.",
            multiline=True,
            min_lines=3,
            max_lines=6,
            text_size=14,
        )
        self.filter = ft.SegmentedButton(
            segments=[
                ft.Segment(value=MediaFilter.ANY.value, label="Everything"),
                ft.Segment(value=MediaFilter.VIDEO.value, label="Videos"),
                ft.Segment(value=MediaFilter.FILE.value, label="Files"),
            ],
            selected=[MediaFilter.ANY.value],
            allow_multiple_selection=False,
            show_selected_icon=False,
        )
        self.add_btn = ft.Button("Add to queue", icon=ft.Icons.DOWNLOAD, on_click=self._on_add)
        self.check_btn = ft.OutlinedButton("Check links", icon=ft.Icons.FACT_CHECK, on_click=self._on_check)
        self.progress = ft.ProgressBar(visible=False)
        self.results = ft.Column(spacing=6)

        self.root = ft.Column(
            expand=True,
            scroll=ft.ScrollMode.AUTO,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            spacing=12,
            controls=[
                self.login_banner,
                self.field,
                ft.Row(
                    wrap=True,
                    spacing=8,
                    run_spacing=8,
                    controls=[
                        ft.TextButton("Paste", icon=ft.Icons.CONTENT_PASTE, on_click=self._on_paste),
                        ft.TextButton("Clear", icon=ft.Icons.CLEAR, on_click=self._on_clear),
                    ],
                ),
                muted("What to download from Telegram posts", size=12),
                self.filter,
                ft.Row(wrap=True, spacing=8, run_spacing=8, controls=[self.add_btn, self.check_btn]),
                self.progress,
                self.results,
            ],
        )

    # ---- visibility -----------------------------------------------------------
    def on_show(self) -> None:
        self.app.page.run_task(self.refresh_login_banner)

    async def refresh_login_banner(self) -> None:
        ok, error = await self.app.state.session.check()
        if ok:
            self.login_banner.visible = False
            self.login_banner.content = None
        elif error is not None and error.kind == "network":
            self.login_banner.content = banner("Cannot reach Telegram. Check your internet connection. Other websites still work.", "warn")
            self.login_banner.visible = True
        else:
            self.login_banner.content = banner(
                "Log in to Telegram to download from Telegram. Other websites work without logging in.",
                "info",
                ft.TextButton("Log in", on_click=lambda e: self.app.open_login()),
            )
            self.login_banner.visible = True
        self._update()

    # ---- helpers ----------------------------------------------------------------
    def set_text(self, text: str) -> None:
        self.field.value = text
        self._update()

    def media_filter(self) -> MediaFilter:
        chosen = (self.filter.selected or [MediaFilter.ANY.value])[0]
        return MediaFilter(chosen)

    def _update(self) -> None:
        try:
            self.root.update()
        except Exception:  # noqa: BLE001 - view may not be on screen
            pass

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.add_btn.disabled = busy
        self.check_btn.disabled = busy
        self.progress.visible = busy
        self._update()

    def _route(self, text: str) -> RouteResult:
        st = self.app.state
        return route(
            text,
            st.rules,
            default_channel=st.cfg.default_channel,
            ytdlp_enabled=st.cfg.ytdlp.enabled,
            range_cap=st.cfg.range_cap,
        )

    # ---- rendering the answer ---------------------------------------------------------
    @staticmethod
    def _line(icon: str, color: str, text: str, extra: ft.Control | None = None) -> ft.Control:
        controls: list[ft.Control] = [ft.Icon(icon, color=color, size=18), ft.Text(text, size=13, expand=True, selectable=True)]
        if extra is not None:
            controls.append(extra)
        return ft.Row(controls, vertical_alignment=ft.CrossAxisAlignment.START, spacing=8)

    def render_route(self, routed: RouteResult) -> None:
        rows: list[ft.Control] = []
        for t in routed.telegram:
            ids = t.msg_ids
            span = f"message {ids[0]}" if len(ids) == 1 else f"messages {ids[0]} to {ids[-1]} ({len(ids)})"
            rows.append(self._line(ft.Icons.SEND, ft.Colors.PRIMARY, f"Telegram {t.peer}: {span}"))
        for c in routed.channels:
            scan = ft.TextButton("Open", on_click=lambda e, peer=c.peer: self.app.scan_peer(peer))
            rows.append(self._line(ft.Icons.FORUM, ft.Colors.TERTIARY, f"Whole channel {c.peer}: open it to pick what to download", scan))
        for ext in routed.external:
            rows.append(self._line(ft.Icons.LANGUAGE, ft.Colors.TERTIARY, f"Website video: {ext.url}"))
        for bad in routed.unmatched:
            rows.append(self._line(ft.Icons.HELP_OUTLINE, ft.Colors.ERROR, f"Not recognised: {bad}"))
        for warning in routed.warnings:
            rows.append(self._line(ft.Icons.WARNING_AMBER, ft.Colors.ORANGE, warning))
        self.results.controls = rows
        self._update()

    def render_notes(self, notes: list[Note]) -> None:
        for n in notes:
            if n.level == "ok":
                continue
            icon, color = (ft.Icons.WARNING_AMBER, ft.Colors.ORANGE) if n.level == "warn" else (ft.Icons.ERROR_OUTLINE, ft.Colors.ERROR)
            self.results.controls.append(self._line(icon, color, n.text))
        self._update()

    # ---- buttons -------------------------------------------------------------------------
    async def _on_paste(self, e: ft.Event) -> None:
        text = await self.app.clipboard.get()
        if text:
            self.field.value = text
            self._update()
        else:
            self.app.toast("The clipboard is empty")

    def _on_clear(self, e: ft.Event) -> None:
        self.field.value = ""
        self.results.controls = []
        self._update()

    def _on_check(self, e: ft.Event) -> None:
        text = (self.field.value or "").strip()
        if not text:
            self.app.toast("Paste a link first")
            return
        self.render_route(self._route(text))

    async def _on_add(self, e: ft.Event) -> None:
        await self.add_to_queue()

    async def add_to_queue(self, auto: bool = False) -> None:
        if self._busy:
            return
        text = (self.field.value or "").strip()
        if not text:
            self.app.toast("Paste a link first")
            return
        st = self.app.state
        routed = self._route(text)
        self.render_route(routed)
        if routed.empty:
            if routed.channels:
                return
            self.app.toast("No supported link found. Check the list below.", error=True)
            return

        self._set_busy(True)
        try:
            found = []
            notes: list[Note] = []
            if routed.telegram:
                if not await self.app.ensure_login():
                    return
                client = await st.session.ensure_connected()
                resolved = await resolve_targets(client, routed.telegram, self.media_filter())
                found, notes = resolved.found, resolved.notes

            items = st.manager.enqueue_telegram(found) + st.manager.enqueue_external(routed.external)
            self.render_route(routed)
            self.render_notes(notes)
            if items:
                self.app.toast(f"Added {len(items)} download{'s' if len(items) != 1 else ''}", action="Queue",
                               on_action=lambda e: self.app.open_queue())
                if not notes or all(n.level == "ok" for n in notes):
                    self.field.value = ""
                if auto:
                    self.app.open_queue()
            else:
                self.app.toast("Nothing could be added. See the details below.", error=True)
        except LoginError as exc:
            self.app.toast(exc.message, error=True)
        except Exception as exc:  # noqa: BLE001
            log.exception("add to queue failed")
            self.app.toast(f"Something went wrong: {exc}", error=True)
        finally:
            self._set_busy(False)
