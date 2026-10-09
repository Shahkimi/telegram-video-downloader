"""Settings > Backup: export the API ID/hash, settings, link rules and library to a file, and import such a file."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import TYPE_CHECKING

import flet as ft

from tgdl import __version__
from tgdl.backup import Backup, BackupError, apply_library, dumps, file_name, is_encrypted, loads, make_backup, merged_config
from tgdl.telegram.session import LoginError

from ..widgets import muted, safe_update

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger("tgdl.ui.backup")


class BackupFlow:
    def __init__(self, app: "App"):
        self.app = app

    # ======================================================================= export
    def open_export(self) -> None:
        page = self.app.page
        st = self.app.state
        creds = ft.Checkbox(label="API ID and API hash", value=st.cfg.has_credentials, disabled=not st.cfg.has_credentials)
        rules = ft.Checkbox(label=f"Link rules ({len(st.rules.rules)})", value=True)
        library = ft.Checkbox(label=f"Library ({len(st.library)} chats, with their folders)", value=True)
        password = ft.TextField(label="Password (optional)", password=True, can_reveal_password=True, dense=True)
        confirm = ft.TextField(label="Repeat password", password=True, can_reveal_password=True, dense=True)
        warning = ft.Text("", size=12, color=ft.Colors.ERROR)

        def refresh(e: ft.Event | None = None) -> None:
            if creds.value and not password.value:
                warning.value = "Without a password, anyone who gets this file can use your API ID and hash."
            else:
                warning.value = ""
            safe_update(warning)

        creds.on_change = password.on_change = refresh
        refresh()

        async def export(e: ft.Event) -> None:
            if (password.value or "") != (confirm.value or ""):
                warning.value = "The two passwords are different."
                safe_update(warning)
                return
            page.pop_dialog()
            await self.export(include_credentials=bool(creds.value), include_rules=bool(rules.value),
                              include_library=bool(library.value), password=password.value or None)

        page.show_dialog(ft.AlertDialog(
            title=ft.Text("Export settings"),
            content=ft.Column(tight=True, spacing=6, scroll=ft.ScrollMode.AUTO, controls=[
                muted("Always included: download, speed, website and Android settings.", size=12),
                creds, rules, library,
                password, confirm, warning,
                muted("Your Telegram login is never exported. After importing you log in once with your phone number.", size=11),
            ]),
            actions=[ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
                     ft.Button("Export", icon=ft.Icons.UPLOAD_FILE, on_click=export)],
        ))

    def build(self, *, include_credentials: bool, include_rules: bool, include_library: bool) -> Backup:
        st = self.app.state
        return make_backup(
            st.cfg,
            rules_text=st.rules.export_text() if include_rules else None,
            library=st.library if include_library else None,
            include_credentials=include_credentials,
            app_version=__version__,
        )

    async def export(self, *, include_credentials: bool = True, include_rules: bool = True, include_library: bool = True,
                     password: str | None = None) -> str | None:
        backup = self.build(include_credentials=include_credentials, include_rules=include_rules, include_library=include_library)
        data = await asyncio.to_thread(dumps, backup, password)   # the password key takes a moment on a phone
        name = file_name()
        saved = None
        try:
            saved = await self.app.file_picker.save_file(dialog_title="Save settings backup", file_name=name,
                                                         src_bytes=data)
            if saved and not self.app.state.paths.is_android and not os.path.exists(saved):
                with open(saved, "wb") as fh:  # desktop pickers only return the path
                    fh.write(data)
        except Exception as exc:  # noqa: BLE001 - no system save dialog: keep it next to the downloads instead
            log.warning("save dialog failed: %s", exc)
            saved = self._write_fallback(name, data)
        if not saved:
            self.app.toast("Export cancelled")
            return None
        text = f"Settings exported to {saved}" + (" (password protected)" if password else "")
        if os.path.exists(saved):
            self.app.toast(text, action="Share", on_action=lambda e: self.app.page.run_task(self.app.share_file, saved))
        else:
            self.app.toast(text)
        return saved

    def _write_fallback(self, name: str, data: bytes) -> str | None:
        folder = self.app.state.manager.downloads_dir
        try:
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, name)
            with open(path, "wb") as fh:
                fh.write(data)
            return path
        except OSError as exc:
            self.app.toast(f"Cannot save the backup: {exc}", error=True)
            return None

    # ======================================================================= import
    async def open_import(self) -> None:
        try:
            picked = await self.app.file_picker.pick_files(dialog_title="Choose a settings backup", with_data=True)
        except Exception as exc:  # noqa: BLE001
            self.app.toast(f"Cannot open the file picker: {exc}", error=True)
            return
        if not picked:
            return
        f = picked[0]
        try:
            raw = f.bytes if getattr(f, "bytes", None) else open(f.path, "rb").read()
        except (OSError, TypeError) as exc:
            self.app.toast(f"Cannot read that file: {exc}", error=True)
            return
        await self.read(raw)

    async def read(self, raw: bytes) -> None:
        try:
            encrypted = is_encrypted(raw)
        except BackupError as exc:
            self.app.toast(exc.message, error=True)
            return
        if encrypted:
            self._ask_password(raw)
            return
        self.confirm(loads(raw))

    def _ask_password(self, raw: bytes, error: str = "") -> None:
        page = self.app.page
        field = ft.TextField(label="Backup password", password=True, can_reveal_password=True, autofocus=True,
                             error=error or None)

        async def unlock(e: ft.Event) -> None:
            page.pop_dialog()
            try:
                backup = await asyncio.to_thread(loads, raw, field.value or "")
            except BackupError as exc:
                if exc.kind in ("bad_password", "password_needed"):
                    self._ask_password(raw, exc.message)
                else:
                    self.app.toast(exc.message, error=True)
                return
            self.confirm(backup)

        field.on_submit = unlock
        page.show_dialog(ft.AlertDialog(
            title=ft.Text("Password protected"),
            content=field,
            actions=[ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()), ft.Button("Unlock", on_click=unlock)],
        ))

    def confirm(self, backup: Backup) -> None:
        page = self.app.page
        st = self.app.state
        lines: list[ft.Control] = [ft.Text("This backup contains:", size=13)]
        lines += [ft.Text(f"  -  {line}", size=13) for line in backup.describe() or ["nothing"]]
        take_creds = ft.Checkbox(label="Use the API ID and hash from the backup", value=True, visible=backup.has_credentials)
        if backup.has_credentials and st.cfg.has_credentials and int(backup.config.get("api_id") or 0) != st.cfg.api_id:
            lines.append(muted("It has a different API ID than this phone uses now.", size=12))
        lines.append(take_creds)
        lines.append(muted("Settings and link rules with the same name are replaced; library chats are added. "
                           "Folders that do not exist on this phone are skipped.", size=11))

        async def run(e: ft.Event) -> None:
            page.pop_dialog()
            await self.apply(backup, take_credentials=bool(take_creds.value))

        page.show_dialog(ft.AlertDialog(
            title=ft.Text("Import settings?"),
            content=ft.Column(lines, tight=True, spacing=4),
            actions=[ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()), ft.Button("Import", on_click=run)],
        ))

    async def apply(self, backup: Backup, *, take_credentials: bool = True) -> list[str]:
        st = self.app.state
        done: list[str] = []
        old_id, old_hash = st.cfg.api_id, st.cfg.api_hash
        if backup.config:
            st.cfg = merged_config(st.cfg, backup, take_credentials=take_credentials)
            st.save_config()
            done.append("settings")
        if backup.rules is not None:
            added, problems = st.rules.import_text(json.dumps(backup.rules), replace=True)
            st.save_rules()
            done.append(f"{added} link rule(s)")
            for p in problems[:3]:
                log.warning("rule import: %s", p)
        if backup.library:
            added = apply_library(st.library, backup.library)
            st.save_library()
            done.append(f"{added} new library chat(s)")

        creds_changed = (st.cfg.api_id, st.cfg.api_hash) != (old_id, old_hash)
        if creds_changed and st.cfg.has_credentials:
            try:
                await st.session.set_api_credentials(st.cfg.api_id, st.cfg.api_hash)
            except LoginError as exc:
                log.warning("new credentials: %s", exc.message)
            done.append("API ID and hash")
            await self.app.on_login_changed()

        self.app.settings_reloaded()
        self.app.toast("Imported " + ", ".join(done) if done else "Nothing to import")
        if creds_changed and not await st.session.is_authorized():
            self.app.open_login()
        return done
