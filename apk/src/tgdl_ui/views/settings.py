"""Settings (More > Settings): account, storage, speed, other sites, Android behaviour."""
from __future__ import annotations

import logging
import os
import shutil
from typing import TYPE_CHECKING, Callable

import flet as ft

from tgdl import __version__
from tgdl.config import GB, parse_channel
from tgdl.diagnostics import writable_dir

from ..widgets import muted, safe_update, section

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger("tgdl.ui.settings")

HEIGHT_OPTIONS = [("360", "360p"), ("480", "480p"), ("720", "720p"), ("1080", "1080p"), ("1440", "1440p"), ("2160", "4K"), ("0", "Best available")]


class SettingsView:
    def __init__(self, app: "App"):
        self.app = app
        cfg = app.state.cfg

        # account
        self.account_text = ft.Text("Checking...", size=14)
        self.account_btn = ft.Button("Log in", on_click=lambda e: self.app.open_login())
        self.logout_btn = ft.OutlinedButton("Log out", on_click=self._on_logout, visible=False)

        # downloads
        self.dir_field = ft.TextField(label="Download folder", value=cfg.downloads_dir or str(app.state.paths.default_downloads_dir),
                                      dense=True, expand=True, on_submit=self._save_dir, on_blur=self._save_dir)
        self.dir_row = ft.Row([self.dir_field, ft.IconButton(ft.Icons.FOLDER_OPEN, tooltip="Choose folder", on_click=self._pick_dir),
                               ft.IconButton(ft.Icons.RESTART_ALT, tooltip="Back to the default folder", on_click=self._reset_dir)],
                              vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=0)
        self.dir_status = muted("")
        self.access_text = muted("", size=12)
        self.access_btn = ft.OutlinedButton("Allow All files access", icon=ft.Icons.SD_STORAGE, on_click=self._on_access, visible=False)
        self.per_channel = ft.Switch(label="A folder for each channel", value=cfg.per_channel_folders, on_change=self._toggle("per_channel_folders"))
        self.nomedia = ft.Switch(label="Hide downloads from the gallery (.nomedia)", value=cfg.nomedia, on_change=self._on_nomedia)
        self.max_gb = ft.TextField(label="Max size per channel scan (GB)", value=f"{cfg.max_total_size_bytes / GB:g}",
                                   keyboard_type=ft.KeyboardType.NUMBER, dense=True, on_blur=self._save_max_gb, on_submit=self._save_max_gb)
        self.default_channel = ft.TextField(label="Default channel (optional)", value="" if cfg.default_channel is None else str(cfg.default_channel),
                                            hint_text="Lets you paste bare message numbers", dense=True,
                                            on_blur=self._save_default_channel, on_submit=self._save_default_channel)
        self.skip_existing = ft.Switch(label="Skip files that are already downloaded", value=cfg.skip_existing, on_change=self._toggle("skip_existing"))

        # speed
        self.concurrent = self._dropdown("Files at the same time", [str(i) for i in range(1, 6)], str(cfg.max_concurrent_files), self._save_concurrent)
        self.connections = self._dropdown("Connections per file", ["2", "4", "6", "8", "12", "16"], str(cfg.parallel_connections), self._save_connections)

        # other sites
        self.ytdlp_on = ft.Switch(label="Download from other websites (yt-dlp)", value=cfg.ytdlp.enabled, on_change=self._toggle_ytdlp)
        height_options = list(HEIGHT_OPTIONS)
        if str(cfg.ytdlp.max_height or 0) not in [k for k, _ in height_options]:
            height_options.insert(0, (str(cfg.ytdlp.max_height), f"{cfg.ytdlp.max_height}p"))
        self.height = ft.Dropdown(label="Largest video size", value=str(cfg.ytdlp.max_height or 0), dense=True,
                                  options=[ft.DropdownOption(key=k, text=t) for k, t in height_options], on_select=self._save_height)
        self.cookies = ft.TextField(label="Cookies file (optional)", value=cfg.ytdlp.cookies_file or "", dense=True, expand=True,
                                    hint_text="cookies.txt exported from your browser", on_blur=self._save_cookies, on_submit=self._save_cookies)
        self.cookies_row = ft.Row([self.cookies, ft.IconButton(ft.Icons.FOLDER_OPEN, tooltip="Choose file", on_click=self._pick_cookies)],
                                  vertical_alignment=ft.CrossAxisAlignment.CENTER)

        # android
        self.clipboard = ft.Switch(label="Offer to download links I copy", value=cfg.android.clipboard_autodetect, on_change=self._toggle_android("clipboard_autodetect"))
        self.autostart = ft.Switch(label="Start right away when I share a link to this app", value=cfg.android.autostart_shared, on_change=self._toggle_android("autostart_shared"))
        self.keep_alive = ft.Switch(label="Keep downloading with the screen off", value=cfg.android.keep_alive, on_change=self._toggle_android("keep_alive"))
        self.keep_screen = ft.Switch(label="Keep the screen on while downloading", value=cfg.android.keep_screen_on, on_change=self._toggle_android("keep_screen_on"))
        self.battery_btn = ft.OutlinedButton("Allow unrestricted battery use", icon=ft.Icons.BATTERY_SAVER, on_click=self._on_battery)

        self.android_box = ft.Column(
            spacing=4,
            visible=app.state.paths.is_android,
            controls=[section("Android"), self.clipboard, self.autostart, self.keep_alive, self.keep_screen, self.battery_btn, ft.Divider()],
        )

        self.root = ft.Column(
            expand=True,
            scroll=ft.ScrollMode.AUTO,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            spacing=10,
            controls=[
                section("Telegram account"),
                self.account_text,
                ft.Row([self.account_btn, self.logout_btn], spacing=8, wrap=True),
                ft.Divider(),
                section("Storage"),
                self.dir_row,
                self.dir_status,
                self.access_text,
                self.access_btn,
                self.per_channel,
                self.nomedia,
                muted("Each chat can also get its own folder or be hidden on its own: open it and use Folder or Hide.", size=12),
                ft.Divider(),
                section("Downloads"),
                self.max_gb,
                self.default_channel,
                self.skip_existing,
                ft.Divider(),
                section("Speed"),
                ft.Row([self.concurrent, self.connections], spacing=10),
                muted("More connections are faster until Telegram slows you down. Lower them if downloads fail.", size=12),
                ft.Divider(),
                section("Other websites"),
                self.ytdlp_on,
                self.height,
                self.cookies_row,
                muted("YouTube, TikTok, Instagram, X and many more. Without ffmpeg only ready-made single files can be saved, "
                      "so the best quality may not be available.", size=12),
                ft.Divider(),
                self.android_box,
                muted(f"TG Downloader {__version__}", size=12),
                ft.Container(height=16),
            ],
        )

    # ---- construction helpers -----------------------------------------------------------
    def _dropdown(self, label: str, values: list[str], current: str, handler: Callable) -> ft.Dropdown:
        if current not in values:
            values = sorted({*values, current}, key=int)
        return ft.Dropdown(label=label, value=current, dense=True, expand=True,
                           options=[ft.DropdownOption(key=v, text=v) for v in values], on_select=handler)

    def _update(self) -> None:
        safe_update(self.root)

    def on_close(self) -> None:
        pass

    # ---- showing ------------------------------------------------------------------------------
    def on_show(self) -> None:
        self.nomedia.value = self.app.state.cfg.nomedia
        self.app.page.run_task(self.refresh_account)
        self.app.page.run_task(self.refresh_access)
        self._show_dir_status()

    async def refresh_access(self) -> None:
        if not self.app.state.paths.is_android:
            return
        granted = await self.app.native.has_all_files_access()
        self.access_btn.visible = not granted
        self.access_text.value = ("All files access: allowed. Any folder can be used." if granted else
                                  "All files access is off: only the Download folder works. Allow it to pick another folder or an SD card.")
        self._update()

    async def refresh_account(self) -> None:
        session = self.app.state.session
        authorized, error = await session.check()
        if error is not None and error.kind == "network":
            self.account_text.value = "Cannot reach Telegram. Check your internet connection."
            self.account_btn.content = "Log in"
            self.logout_btn.visible = False
        elif authorized:
            me = await session.me()
            self.account_text.value = f"Logged in as {me.label}" if me else "Logged in"
            self.account_btn.content = "Switch account"
            self.logout_btn.visible = True
        else:
            self.account_text.value = "Not logged in"
            self.account_btn.content = "Log in"
            self.logout_btn.visible = False
        self._update()

    def _show_dir_status(self) -> None:
        manager = self.app.state.manager
        target = manager.downloads_dir
        status = writable_dir(target)
        text = f"Saving to: {target}" + ("" if status == "ok" else f"\n{status}")
        if manager.downloads_dir_override:
            text += "\nThe shared Downloads folder was not writable, so the app folder is used instead."
        self.dir_status.value = text
        self.dir_status.color = ft.Colors.ON_SURFACE_VARIANT if status == "ok" else ft.Colors.ERROR
        self._update()

    # ---- saving ---------------------------------------------------------------------------------
    def _save(self) -> None:
        self.app.state.save_config()

    def _toggle(self, attr: str):
        def handler(e: ft.Event) -> None:
            setattr(self.app.state.cfg, attr, bool(e.control.value))
            self._save()
        return handler

    def _toggle_android(self, attr: str):
        def handler(e: ft.Event) -> None:
            setattr(self.app.state.cfg.android, attr, bool(e.control.value))
            self._save()
        return handler

    def _toggle_ytdlp(self, e: ft.Event) -> None:
        self.app.state.cfg.ytdlp.enabled = bool(e.control.value)
        self._save()

    def _save_dir(self, e: ft.Event) -> None:
        cfg = self.app.state.cfg
        value = (self.dir_field.value or "").strip()
        default = str(self.app.state.paths.default_downloads_dir)
        new = None if (not value or value == default) else value
        if new == cfg.downloads_dir:
            return
        cfg.downloads_dir = new
        self.app.state.manager.downloads_dir_override = None
        self._save()
        self._show_dir_status()

    async def _pick_dir(self, e: ft.Event) -> None:
        picked = await self.app.pick_folder()
        if picked:
            self.dir_field.value = picked
            self._save_dir(e)
            self.app.toast(f"Downloads now go to {picked}")

    def _reset_dir(self, e: ft.Event) -> None:
        self.dir_field.value = str(self.app.state.paths.default_downloads_dir)
        self._save_dir(e)
        self._update()

    async def _on_access(self, e: ft.Event) -> None:
        await self.app.request_all_files_access()
        await self.refresh_access()

    async def _on_nomedia(self, e: ft.Event) -> None:
        await self.app.set_download_root_hidden(bool(self.nomedia.value))

    def _save_max_gb(self, e: ft.Event) -> None:
        try:
            gb = float(self.max_gb.value or "")
            if gb <= 0:
                raise ValueError
        except ValueError:
            self.max_gb.value = f"{self.app.state.cfg.max_total_size_bytes / GB:g}"
            self.app.toast("Enter a number above zero", error=True)
            self._update()
            return
        self.app.state.cfg.max_total_size_bytes = int(gb * GB)
        self._save()

    def _save_default_channel(self, e: ft.Event) -> None:
        self.app.state.cfg.default_channel = parse_channel((self.default_channel.value or "").strip().lstrip("@"))
        self._save()

    def _save_concurrent(self, e: ft.Event) -> None:
        self.app.state.cfg.max_concurrent_files = int(self.concurrent.value or 3)
        self._save()

    def _save_connections(self, e: ft.Event) -> None:
        self.app.state.cfg.parallel_connections = int(self.connections.value or 8)
        self._save()

    def _save_height(self, e: ft.Event) -> None:
        value = int(self.height.value or 0)
        self.app.state.cfg.ytdlp.max_height = value or None
        self._save()

    def _save_cookies(self, e: ft.Event) -> None:
        path = (self.cookies.value or "").strip()
        if path and not os.path.exists(path):
            self.app.toast("That cookies file does not exist", error=True)
            return
        self.app.state.cfg.ytdlp.cookies_file = path or None
        self._save()

    async def _pick_cookies(self, e: ft.Event) -> None:
        try:
            picked = await self.app.file_picker.pick_files(dialog_title="Choose cookies.txt")
        except Exception as exc:  # noqa: BLE001
            self.app.toast(f"Cannot open the file picker: {exc}", error=True)
            return
        if not picked or not picked[0].path:
            return
        # The picker hands out a temporary copy on Android, so keep our own.
        target = self.app.state.paths.data_dir / "cookies.txt"
        try:
            shutil.copyfile(picked[0].path, target)
        except OSError as exc:
            self.app.toast(f"Cannot copy the cookies file: {exc}", error=True)
            return
        self.cookies.value = str(target)
        self._update()
        self._save_cookies(e)
        self.app.toast("Cookies file saved")

    # ---- buttons -----------------------------------------------------------------------------------
    async def _on_logout(self, e: ft.Event) -> None:
        def confirm(ev: ft.Event) -> None:
            self.app.page.pop_dialog()
            self.app.page.run_task(self._do_logout)

        self.app.page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text("Log out?"),
            content=ft.Text("This removes the saved Telegram login from this phone. Your downloads stay."),
            actions=[ft.TextButton("Cancel", on_click=lambda ev: self.app.page.pop_dialog()), ft.Button("Log out", on_click=confirm)],
        ))

    async def _do_logout(self) -> None:
        await self.app.state.session.logout()
        self.app.state.dialogs = None
        self.app.toast("Logged out")
        await self.refresh_account()

    async def _on_battery(self, e: ft.Event) -> None:
        outcome = await self.app.native.request_ignore_battery_optimizations()
        if outcome == "granted":
            self.app.toast("Already allowed. Downloads can run in the background.")
        elif outcome == "failed":
            self.app.toast("Open Android Settings > Apps > TG Downloader > Battery and choose Unrestricted")
