"""Link rules screen: see, add, edit, test, import and export your own link formats."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import flet as ft

from tgdl.links.router import route
from tgdl.links.rules import LinkRule, RuleError, RuleSet

from ..widgets import muted

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger("tgdl.ui.rules")

TARGETS = [
    ("telegram", "Download Telegram messages"),
    ("channel", "Open as a whole channel"),
    ("rewrite", "Rewrite into another link"),
    ("ytdlp", "Download page with yt-dlp"),
]

HELP = (
    "A rule teaches the app a new kind of link. Write the pattern like the link, with {placeholders} where parts change:\n"
    "{channel} {username} {cid} {msg} {topic} {*} {**}\n"
    "Example: mysite.com/{channel}/{msg} matches https://mysite.com/durov/12"
)


class RulesView:
    def __init__(self, app: "App"):
        self.app = app
        self.list = ft.Column(spacing=0)
        self.test_field = ft.TextField(label="Test a link", hint_text="Paste a link to see how the app reads it", dense=True, on_submit=self._on_test)
        self.test_out = ft.Column(spacing=4)
        self.root = ft.Column(
            expand=True,
            scroll=ft.ScrollMode.AUTO,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            spacing=10,
            controls=[
                muted(HELP, size=12),
                ft.Row(
                    wrap=True,
                    spacing=8,
                    controls=[
                        ft.Button("Add rule", icon=ft.Icons.ADD, on_click=lambda e: self.edit(None)),
                        ft.OutlinedButton("Export", icon=ft.Icons.UPLOAD_FILE, on_click=self._on_export),
                        ft.OutlinedButton("Import", icon=ft.Icons.FILE_DOWNLOAD, on_click=self._on_import),
                    ],
                ),
                self.list,
                ft.Divider(),
                self.test_field,
                ft.Button("Test", on_click=self._on_test),
                self.test_out,
            ],
        )
        self.refresh()

    # ---- list -------------------------------------------------------------------------------
    @property
    def rules(self) -> RuleSet:
        return self.app.state.rules

    def _update(self) -> None:
        try:
            self.root.update()
        except Exception:  # noqa: BLE001
            pass

    def refresh(self) -> None:
        rows: list[ft.Control] = []
        if self.rules.load_error:
            rows.append(muted(self.rules.load_error, size=12, color=ft.Colors.ERROR))
        if not self.rules.rules:
            rows.append(muted("No rules yet. Normal Telegram links and other websites already work without any."))
        for rule in self.rules.rules:
            sub = f"{rule.pattern}  ->  {rule.target}"
            if rule.error:
                sub = f"Problem: {rule.error}"
            rows.append(ft.ListTile(
                leading=ft.Switch(value=rule.enabled and not rule.error, disabled=bool(rule.error),
                                  on_change=lambda e, rid=rule.id: self._toggle(rid, bool(e.control.value))),
                title=ft.Text(rule.name or rule.id, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                subtitle=ft.Text(sub, size=12, max_lines=3, overflow=ft.TextOverflow.ELLIPSIS,
                                 color=ft.Colors.ERROR if rule.error else None),
                trailing=ft.PopupMenuButton(items=[
                    ft.PopupMenuItem(content="Edit", icon=ft.Icons.EDIT, on_click=lambda e, r=rule: self.edit(r)),
                    ft.PopupMenuItem(content="Delete", icon=ft.Icons.DELETE, on_click=lambda e, r=rule: self._confirm_delete(r)),
                ]),
            ))
        self.list.controls = rows

    def _toggle(self, rule_id: str, enabled: bool) -> None:
        self.rules.set_enabled(rule_id, enabled)
        self.app.state.save_rules()

    def _confirm_delete(self, rule: LinkRule) -> None:
        page = self.app.page

        def delete(e: ft.Event) -> None:
            page.pop_dialog()
            self.rules.remove(rule.id)
            self.app.state.save_rules()
            self.refresh()
            self._update()

        page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text("Delete rule?"), content=ft.Text(rule.name or rule.id),
            actions=[ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()), ft.Button("Delete", on_click=delete)],
        ))

    # ---- editor -----------------------------------------------------------------------------------
    def edit(self, existing: LinkRule | None) -> None:
        page = self.app.page
        rule = existing or LinkRule(id="", pattern="")
        rid = ft.TextField(label="Id (short, no spaces)", value=rule.id, dense=True, disabled=existing is not None)
        name = ft.TextField(label="Name (optional)", value=rule.name, dense=True)
        style = ft.Dropdown(label="Pattern style", value=rule.type, dense=True, width=290, options=[
            ft.DropdownOption(key="template", text="Template (easy)"), ft.DropdownOption(key="regex", text="Regex (advanced)")])
        pattern = ft.TextField(label="Pattern", value=rule.pattern, hint_text="mysite.com/{channel}/{msg}", dense=True, multiline=True, min_lines=1, max_lines=3)
        target = ft.Dropdown(label="What should it do?", value=rule.target, dense=True, width=290, options=[ft.DropdownOption(key=k, text=t) for k, t in TARGETS])
        channel = ft.TextField(label="Channel to use (optional)", value="" if rule.channel_override is None else str(rule.channel_override), dense=True)
        rewrite = ft.TextField(label="Rewrite to", value=rule.rewrite_to or "", hint_text="https://t.me/mychannel/{msg}", dense=True)
        priority = ft.TextField(label="Priority (higher goes first)", value=str(rule.priority), keyboard_type=ft.KeyboardType.NUMBER, dense=True)
        example = ft.TextField(label="Example link to test (optional)", value=(rule.tests[0].get("input", "") if rule.tests else ""), dense=True)
        message = ft.Text("", color=ft.Colors.ERROR, size=13, visible=False)

        dialog = ft.AlertDialog(modal=True, scrollable=True, title=ft.Text("Edit rule" if existing else "New rule"))

        def sync_fields(e: ft.Event | None = None) -> None:
            """Only show the fields that matter for what the rule does."""
            channel.visible = (target.value or "telegram") in ("telegram", "channel")
            rewrite.visible = target.value == "rewrite"
            if e is not None:
                dialog.update()

        target.on_select = sync_fields
        sync_fields()

        def save(e: ft.Event) -> None:
            prio_text = (priority.value or "100").strip()
            candidate = LinkRule(
                id=(rid.value or "").strip(),
                name=(name.value or "").strip(),
                pattern=(pattern.value or "").strip(),
                type=style.value or "template",
                target=target.value or "telegram",
                channel_override=(channel.value or "").strip() or None,
                rewrite_to=(rewrite.value or "").strip() or None,
                priority=int(prio_text) if prio_text.lstrip("-").isdigit() else 100,
                enabled=existing.enabled if existing else True,
                tests=[{"input": example.value.strip()}] if (example.value or "").strip() else [],
            )
            try:
                warnings = self.rules.add(candidate, replace=existing is not None)
            except RuleError as exc:
                message.value, message.visible = str(exc), True
                dialog.update()
                return
            self.app.state.save_rules()
            page.pop_dialog()
            self.refresh()
            self._update()
            self.app.toast("Rule saved" + (f". Note: {warnings[0]}" if warnings else ""))

        dialog.content = ft.Container(width=290, content=ft.Column(
            [rid, name, style, pattern, target, channel, rewrite, priority, example, message],
            tight=True, spacing=10, horizontal_alignment=ft.CrossAxisAlignment.STRETCH))
        dialog.actions = [ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()), ft.Button("Save", on_click=save)]
        dialog.inset_padding = ft.Padding.symmetric(horizontal=12, vertical=24)
        page.show_dialog(dialog)

    # ---- test box -----------------------------------------------------------------------------------
    def _on_test(self, e: ft.Event) -> None:
        text = (self.test_field.value or "").strip()
        cfg = self.app.state.cfg
        if not text:
            return
        result = route(text, self.rules, default_channel=cfg.default_channel, ytdlp_enabled=cfg.ytdlp.enabled, range_cap=cfg.range_cap)
        lines = result.describe() or ["nothing recognised"]
        self.test_out.controls = [ft.Text(line, size=13, selectable=True) for line in lines]
        self._update()

    # ---- import / export ---------------------------------------------------------------------------
    async def _on_export(self, e: ft.Event) -> None:
        text = self.rules.export_text()
        try:
            await self.app.share.share_text(text, title="Link rules")
        except Exception:  # noqa: BLE001 - not every platform has a share sheet
            await self.app.clipboard.set(text)
            self.app.toast("Rules copied to the clipboard")

    async def _on_import(self, e: ft.Event) -> None:
        try:
            picked = await self.app.file_picker.pick_files(dialog_title="Choose a rules file", allowed_extensions=["json"],
                                                           file_type=ft.FilePickerFileType.CUSTOM)
        except Exception as exc:  # noqa: BLE001
            self.app.toast(f"Cannot open the file picker: {exc}", error=True)
            return
        if not picked or not picked[0].path:
            return
        try:
            with open(picked[0].path, "r", encoding="utf-8") as fh:
                added, problems = self.rules.import_text(fh.read(), replace=True)
        except OSError as exc:
            self.app.toast(f"Cannot read the file: {exc}", error=True)
            return
        self.app.state.save_rules()
        self.refresh()
        self._update()
        self.app.toast(f"Imported {added} rule(s)" + (f", {len(problems)} skipped" if problems else ""), error=bool(problems and not added))
