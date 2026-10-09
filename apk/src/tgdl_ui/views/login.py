"""Login wizard: API credentials, phone, code, two-step password, all in one dialog."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Awaitable, Callable

import flet as ft

from tgdl.telegram.session import LoginError, LoginStep

from ..widgets import muted

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger("tgdl.ui.login")


class LoginDialog:
    def __init__(self, app: "App", on_done: Callable[[bool], None] | None = None):
        self.app = app
        self.session = app.state.session
        self.on_done = on_done
        self._finished = False
        self._submit: Callable[[], Awaitable[LoginStep]] | None = None

        self.error = ft.Text("", color=ft.Colors.ERROR, size=13, visible=False)
        self.busy = ft.ProgressRing(width=18, height=18, stroke_width=2, visible=False)
        self.body = ft.Column(tight=True, spacing=12)
        self.primary = ft.Button("Continue", on_click=self._on_primary)
        self.cancel = ft.TextButton("Cancel", on_click=self._on_cancel)
        self.dialog = ft.AlertDialog(
            modal=True,
            scrollable=True,
            title=ft.Text("Log in to Telegram"),
            content=ft.Container(width=280, content=self.body),
            actions=[self.busy, self.cancel, self.primary],
            inset_padding=ft.Padding.symmetric(horizontal=12, vertical=24),
            on_dismiss=self._on_dismiss,
        )

    # ---- lifecycle -------------------------------------------------------
    def open(self) -> None:
        self.app.page.show_dialog(self.dialog)
        self.app.page.run_task(self._begin)

    async def _begin(self) -> None:
        self._set_busy(True)
        try:
            step = await self.session.start()
        except LoginError as exc:
            self._show_error(exc.message)
            step = self.session.step if exc.kind == "api_invalid" else LoginStep.NEED_API
        except Exception as exc:  # noqa: BLE001
            log.exception("login start failed")
            self._show_error(str(exc))
            step = LoginStep.NEED_API
        finally:
            self._set_busy(False)
        self.render(step)

    def _finish(self, ok: bool) -> None:
        if self._finished:
            return
        self._finished = True
        if self.on_done:
            self.on_done(ok)

    def _on_dismiss(self, e: ft.Event) -> None:
        self._finish(self.session.step is LoginStep.READY)

    def _on_cancel(self, e: ft.Event) -> None:
        self.session.cancel_login()
        self.app.page.pop_dialog()
        self._finish(False)

    # ---- helpers -----------------------------------------------------------
    def _set_busy(self, busy: bool) -> None:
        self.busy.visible = busy
        self.primary.disabled = busy
        self._safe_update()

    def _show_error(self, text: str) -> None:
        self.error.value = text
        self.error.visible = bool(text)
        self._safe_update()

    def _safe_update(self) -> None:
        try:
            self.dialog.update()
        except Exception:  # noqa: BLE001 - dialog may already be closed
            pass

    async def _on_primary(self, e: ft.Event) -> None:
        if self._submit is None:
            return
        self._show_error("")
        self._set_busy(True)
        step: LoginStep | None
        try:
            step = await self._submit()
        except LoginError as exc:
            self._show_error(exc.message)
            step = None if exc.kind in ("network", "flood") else self.session.step
        except Exception as exc:  # noqa: BLE001
            log.exception("login step failed")
            self._show_error(str(exc))
            step = None
        finally:
            self._set_busy(False)
        if step is not None:
            self.render(step)

    # ---- one screen per step -------------------------------------------
    def render(self, step: LoginStep) -> None:
        self.body.controls = []
        self.primary.visible = True
        if step is LoginStep.NEED_API:
            self._render_api()
        elif step is LoginStep.NEED_PHONE:
            self._render_phone()
        elif step is LoginStep.NEED_CODE:
            self._render_code()
        elif step is LoginStep.NEED_PASSWORD:
            self.app.page.run_task(self._render_password)
            return
        else:
            self._render_done()
        self.body.controls.append(self.error)
        self._safe_update()

    def _render_api(self) -> None:
        cfg = self.app.state.store.load()
        api_id = ft.TextField(label="API ID", value=str(cfg.api_id) if cfg.api_id else "", keyboard_type=ft.KeyboardType.NUMBER)
        api_hash = ft.TextField(label="API hash", value=cfg.api_hash, password=True, can_reveal_password=True)
        self.body.controls = [
            muted("Telegram needs your own API ID and hash. They are free: sign in at my.telegram.org, open "
                  "API development tools and create an app. They stay on this phone.", size=13),
            ft.TextButton("Open my.telegram.org", icon=ft.Icons.OPEN_IN_NEW, url="https://my.telegram.org"),
            api_id,
            api_hash,
        ]
        self.primary.content = "Continue"

        async def submit() -> LoginStep:
            return await self.session.set_api_credentials(api_id.value or "", api_hash.value or "")

        self._submit = submit

    def _render_phone(self) -> None:
        phone = ft.TextField(label="Phone number", hint_text="+60123456789", keyboard_type=ft.KeyboardType.PHONE, autofocus=True)
        self.body.controls = [muted("Use the international format with your country code.", size=13), phone]
        self.primary.content = "Send code"

        async def submit() -> LoginStep:
            return await self.session.send_code(phone.value or "")

        self._submit = submit

    def _render_code(self) -> None:
        code = ft.TextField(label="Login code", keyboard_type=ft.KeyboardType.NUMBER, autofocus=True)
        resend = ft.TextButton("Send the code again", on_click=self._on_resend)
        self.body.controls = [muted("Telegram sent a code to your Telegram app (or by SMS).", size=13), code, resend]
        self.primary.content = "Log in"

        async def submit() -> LoginStep:
            return await self.session.submit_code(code.value or "")

        self._submit = submit

    async def _render_password(self) -> None:
        hint = await self.session.password_hint()
        password = ft.TextField(label="Two-step verification password", password=True, can_reveal_password=True, autofocus=True)
        self.body.controls = [
            muted("This account has two-step verification turned on." + (f" Hint: {hint}" if hint else ""), size=13),
            password,
            self.error,
        ]
        self.primary.content = "Log in"

        async def submit() -> LoginStep:
            return await self.session.submit_password(password.value or "")

        self._submit = submit
        self._safe_update()

    def _render_done(self) -> None:
        self.body.controls = [ft.Text("You are logged in.", size=15)]
        self.primary.content = "Done"
        self.cancel.visible = False

        async def submit() -> LoginStep:
            self.app.page.pop_dialog()
            self._finish(True)
            await self.app.on_login_changed()
            return LoginStep.READY

        self._submit = submit
        self.app.page.run_task(self.app.on_login_changed)

    async def _on_resend(self, e: ft.Event) -> None:
        self._show_error("")
        self._set_busy(True)
        try:
            await self.session.resend_code()
            self._show_error("A new code was requested.")
        except LoginError as exc:
            self._show_error(exc.message)
        finally:
            self._set_busy(False)
