# =====================================================================
# Telegram Private Video Downloader  (command line)
# =====================================================================
# Logs into Telegram with your API ID & Hash and downloads videos and
# files from channels, groups and post links. Other sites (YouTube,
# TikTok, ...) work through yt-dlp. You can add your own link formats
# in menu [5]. Settings come from the local '.env' file.
#
# The download engine lives in apk/src/tgdl and is shared with the
# Android app (see apk/README.md).
# =====================================================================

import asyncio
import getpass
import io
import logging
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "apk", "src"))

if sys.stdout and getattr(sys.stdout, "encoding", None) and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

if sys.stderr and getattr(sys.stderr, "encoding", None) and sys.stderr.encoding.lower() != "utf-8":
    try:
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

from rich.console import Console
from rich.markup import escape
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from tgdl import __version__ as CORE_VERSION
from tgdl import crypto_backend, diagnostics
from tgdl.config import GB, EnvConfigStore, parse_channel
from tgdl.engine.manager import DownloadManager
from tgdl.engine.models import ItemState
from tgdl.engine.ytdlp_dl import detect_ffmpeg
from tgdl.links.router import route
from tgdl.links.rules import LinkRule, RuleError, RuleSet
from tgdl.paths import resolve_paths
from tgdl.telegram.dialogs import ChatCategory, list_dialogs
from tgdl.telegram.resolve import MediaFilter, resolve_targets, scan_channel
from tgdl.telegram.session import LoginError, LoginStep, TelegramSession

# Library messages would garble the progress bars; this UI prints what matters itself.
logging.getLogger("tgdl").addHandler(logging.NullHandler())
logging.getLogger("tgdl").propagate = False

console = Console()
PATHS = resolve_paths("cli", ROOT)
STORE = EnvConfigStore(PATHS.config_file)

APP_VERSION = f"v{CORE_VERSION}"

BANNER = f"""[bold green]
  ██████╗  █████╗ ██████╗ ██╗   ██╗███████╗     ██╗
  ██╔══██╗██╔══██╗██╔══██╗██║   ██║██╔════╝     ██║
  ██████╔╝███████║██████╔╝██║   ██║█████╗       ██║
  ██╔═══╝ ██╔══██║██╔══██╗╚██╗ ██╔╝██╔══╝  ██   ██║
  ██║     ██║  ██║██║  ██║ ╚████╔╝ ███████╗╚█████╔╝
  ╚═╝     ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚══════╝ ╚════╝
        DEVELOPED BY: PARVEJ | VERSION: {APP_VERSION}[/bold green]
"""

YES = ("y", "yes")


# ---------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------

def load_rules():
    rules = RuleSet.load(PATHS.rules_file)
    if rules.load_error:
        print(f"[WARNING] {rules.load_error}")
    broken = [r for r in rules.rules if r.error]
    for r in broken:
        print(f"[WARNING] Link rule '{r.id}' is disabled: {r.error}")
    return rules


def yes(answer):
    return answer.strip().lower() in YES


def print_note(note):
    if note.level == "ok":
        print(f"✓ {note.text}")
    elif note.level == "warn":
        print(f"[WARNING] {note.text}")
    else:
        print(f"[ERROR] {note.text}")


def make_progress():
    return Progress(
        SpinnerColumn(),
        TextColumn("[cyan][Queue {task.fields[index]}/{task.fields[total_count]}][/cyan]"),
        TextColumn("[bold white]{task.fields[filename]}[/bold white]"),
        BarColumn(bar_width=25),
        "[progress.percentage]{task.percentage:>3.0f}%",
        "•",
        DownloadColumn(),
        "•",
        TransferSpeedColumn(),
        "•",
        TimeRemainingColumn(),
        console=console,
        transient=True,
    )


class RichProgressSink:
    """Shows one progress bar per file that is actually downloading."""

    def __init__(self, bar):
        self.bar = bar
        self.tasks = {}

    def added(self, item):
        pass

    def started(self, item):
        self.tasks[item.id] = self.bar.add_task(
            "download",
            filename=escape(item.display_name),
            index=item.index,
            total_count=item.batch_total,
            total=item.total or 1,
        )

    def progress(self, item):
        task_id = self.tasks.get(item.id)
        if task_id is not None:
            self.bar.update(task_id, completed=item.done, total=item.total or 1, filename=escape(item.display_name))

    def finished(self, item):
        task_id = self.tasks.pop(item.id, None)
        if task_id is not None:
            self.bar.remove_task(task_id)
        ident = getattr(item.message, "id", None) or item.url
        name = item.display_name
        if item.state is ItemState.DONE:
            line = f"✓ [Finished] File ID: {ident} ({name})"
        elif item.state is ItemState.SKIPPED:
            line = f"↷ [Skipped] File ID: {ident} ({name}) already downloaded"
        elif item.state is ItemState.CANCELLED:
            line = f"✗ [Cancelled] File ID: {ident} ({name})"
        else:
            line = f"✗ [Failed] File ID: {ident} ({name}). Error: {item.error}"
        self.bar.console.print(line, markup=False, highlight=False)

    def log(self, text):
        self.bar.console.print(text, markup=False, highlight=False)


async def run_downloads(session, cfg, enqueue):
    """Create a queue, let enqueue(manager) fill it, show progress until everything is done."""
    manager = DownloadManager(session, cfg, PATHS)
    with make_progress() as progress:
        manager.sink = RichProgressSink(progress)
        items = enqueue(manager)
        try:
            await manager.wait_idle()
        except (KeyboardInterrupt, asyncio.CancelledError):
            manager.cancel_all()
            await manager.wait_idle()
            raise
    return items


def count_results(items):
    ok = sum(1 for i in items if i.state in (ItemState.DONE, ItemState.SKIPPED))
    skipped = sum(1 for i in items if i.state is ItemState.SKIPPED)
    return ok, skipped, len(items) - ok


# ---------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------

def prompt_credentials(cfg):
    curr_api_id = str(cfg.api_id) if cfg.api_id else ""
    curr_api_hash = cfg.api_hash

    print("\n[INFO] Enter your Telegram API Credentials (get from https://my.telegram.org):")

    while True:
        prompt_id = f"Enter App API_ID (Press Enter to keep '{curr_api_id}'): " if curr_api_id else "Enter App API_ID: "
        in_id = input(prompt_id).strip()
        if not in_id and curr_api_id:
            in_id = curr_api_id
        if in_id.isdigit():
            break
        print("[ERROR] API_ID must be a numeric integer.")

    while True:
        prompt_hash = f"Enter App API_HASH (Press Enter to keep '{curr_api_hash}'): " if curr_api_hash else "Enter App API_HASH: "
        in_hash = input(prompt_hash).strip()
        if not in_hash and curr_api_hash:
            in_hash = curr_api_hash
        if in_hash:
            break
        print("[ERROR] API_HASH cannot be empty.")
    return in_id, in_hash


async def cli_login(session, force_credentials=False):
    """Walk through API credentials, phone, code and 2FA password. Returns True when logged in."""
    try:
        step = await session.start()
    except LoginError as exc:
        print(f"\n[ERROR] {exc.message}")
        if exc.kind != "api_invalid":
            return False
        step = LoginStep.NEED_API

    if force_credentials or step is LoginStep.NEED_API:
        api_id, api_hash = prompt_credentials(STORE.load())
        try:
            step = await session.set_api_credentials(api_id, api_hash)
        except LoginError as exc:
            print(f"\n[ERROR] {exc.message}")
            return False

    while step is not LoginStep.READY:
        try:
            if step is LoginStep.NEED_API:
                api_id, api_hash = prompt_credentials(STORE.load())
                step = await session.set_api_credentials(api_id, api_hash)
            elif step is LoginStep.NEED_PHONE:
                print("\nConnecting to Telegram authentication...")
                phone = input("Enter your phone number with country code (e.g. +60123456789, 0=Cancel): ").strip()
                if phone in ("", "0"):
                    return False
                step = await session.send_code(phone)
                print("A login code was sent to your Telegram app (or by SMS).")
            elif step is LoginStep.NEED_CODE:
                code = input("Enter the login code (r=resend, 0=Cancel): ").strip()
                if code == "0":
                    session.cancel_login()
                    return False
                if code.lower() == "r":
                    step = await session.resend_code()
                    print("A new code was requested.")
                    continue
                step = await session.submit_code(code)
            elif step is LoginStep.NEED_PASSWORD:
                hint = await session.password_hint()
                if hint:
                    print(f"Password hint: {hint}")
                password = getpass.getpass("Enter your two-step verification password (hidden, Enter=Cancel): ")
                if not password:
                    session.cancel_login()
                    return False
                step = await session.submit_password(password)
        except LoginError as exc:
            print(f"[ERROR] {exc.message}")
            if exc.kind in ("network", "flood"):
                return False
            step = session.step
    return True


async def ensure_login(session):
    """Used before the download modes. Offers to log in when needed."""
    try:
        if await session.is_authorized():
            return True
    except LoginError:
        pass
    print("\n[INFO] Authentication required to proceed.")
    if not yes(input("Would you like to login to Telegram now? (Y/N): ")):
        return False
    try:
        ok = await cli_login(session)
    except Exception as exc:
        print(f"\n[ERROR] Authentication failed. Detail: {exc}")
        input("\nPress Enter to return to main menu...")
        return False
    if not ok:
        input("\nPress Enter to return to main menu...")
    return ok


# ---------------------------------------------------------------------
# Menu: My Account
# ---------------------------------------------------------------------

async def menu_account(session):
    while True:
        console.clear()
        console.print(BANNER)
        console.print("[bold cyan]==================================================[/bold cyan]")
        console.print("[bold white] My Account Settings[/bold white]")
        console.print("[bold cyan]==================================================[/bold cyan]")
        console.print("  [1] Telegram Login")
        console.print("  [2] Logout Telegram")
        console.print("  [0] Back to Main Menu")
        console.print("=" * 50)

        choice = input("Enter choice (0-2): ").strip()

        if choice == "0":
            return

        if choice == "1":
            console.print("\n[bold cyan]--- Telegram Login ---[/bold cyan]")
            is_auth = await session.is_authorized()

            if is_auth:
                me = await session.me()
                if me:
                    print(f"\n✓ Currently Logged In as: {me.name} ({'@' + me.username if me.username else 'No Username'} | +{me.phone or 'N/A'})")
                else:
                    print("\n✓ Currently Logged In to Telegram.")
                if not yes(input("\nDo you want to re-login / update API credentials? (Y/N): ")):
                    continue
                ok = await cli_login(session, force_credentials=True)
            else:
                ok = await cli_login(session)

            me = await session.me() if ok else None
            if me:
                print(f"\n✓ Login successful! Account: {me.name} ({'@' + me.username if me.username else 'No Username'} | ID: {me.id})")
                print("You now have full permission to use all downloader features.")
            else:
                print("\n[ERROR] Login failed or interrupted.")
            input("\nPress Enter to return to My Account menu...")

        elif choice == "2":
            console.print("\n[bold cyan]--- Logout Telegram ---[/bold cyan]")
            if not await session.is_authorized():
                print("\n[INFO] You are not currently logged into any Telegram account.")
                input("\nPress Enter to return to My Account menu...")
                continue

            me = await session.me()
            acc_name = me.name if me and me.name else "your account"
            if not yes(input(f"\nAre you sure you want to logout from '{acc_name}'? (Y/N): ")):
                print("[INFO] Logout cancelled. Returning to My Account menu...")
                continue
            try:
                await session.logout()
                print("\n✓ Successfully logged out from Telegram account.")
                print("Your session has been cleared.")
            except Exception as exc:
                print(f"\n[ERROR] Logout failed. Detail: {exc}")
            input("\nPress Enter to return to My Account menu...")
        else:
            print("[ERROR] Invalid choice. Please enter 0, 1, or 2.")
            input("\nPress Enter to return to My Account menu...")


# ---------------------------------------------------------------------
# Menu: batch download of a whole channel
# ---------------------------------------------------------------------

CATEGORY_MENU = {
    "1": ChatCategory.PRIVATE_CHANNEL,
    "2": ChatCategory.PRIVATE_GROUP,
    "3": ChatCategory.PUBLIC_CHANNEL,
    "4": ChatCategory.PUBLIC_GROUP,
}


async def scan_and_download(session, cfg, entity, title):
    """Scan one chat for videos, confirm, download. Returns True when the task finished."""
    client = session.client
    print(f"Output Directory  : {cfg.downloads_path(PATHS)}/")
    print(f"Max Download Limit: {cfg.max_total_size_bytes / GB:.2f} GB")

    print("\nScanning messages and detecting video files...")
    print("-" * 50)

    with console.status("Scanning..."):
        scan = await scan_channel(client, entity, MediaFilter.VIDEO, cfg.max_total_size_bytes)

    total_files = len(scan.messages)
    print(f"Found {total_files} video(s) detected in '{title}'.")
    print(f"Total size budget: {scan.total_size / GB:.2f} GB")

    if scan.limit_reached:
        print("Note: Stop limit reached during scan. Some newer files were excluded to stay under limits.")

    if not scan.messages:
        print("No videos found to download.")
        return False

    if not yes(input("\nContinue download? (Y/N): ")):
        print("[INFO] Download cancelled. Returning to channel list...")
        return False

    print("\nStarting Downloads...")
    print("-" * 50)

    items = await run_downloads(session, cfg, lambda m: m.enqueue_messages(scan.messages, entity, title))
    ok, skipped, failed = count_results(items)

    print("\n" + "=" * 50)
    print("Download Summary:")
    print(f"  Target Channel          : {title}")
    print(f"  Total Videos Checked    : {total_files}")
    print(f"  Successfully Downloaded : {ok}")
    if skipped:
        print(f"  (already on disk)       : {skipped}")
    print(f"  Failed Downloads        : {failed}")
    print(f"  Total Size Budgeted     : {scan.total_size / GB:.2f} GB")
    print("=" * 50 + "\n")

    input("[Done] Task completed! Press Enter to return to main menu...")
    return True


async def menu_batch(session):
    if not await ensure_login(session):
        return
    client = session.client

    while True:
        cfg = STORE.load()
        console.clear()
        console.print(BANNER)
        console.print("[bold cyan]Select Target Category:[/bold cyan]")
        console.print("  [1] Private Channel")
        console.print("  [2] Private Group")
        console.print("  [3] Public Channel")
        console.print("  [4] Public Group")
        console.print("  [5] Manual ID / Username")
        console.print("  [0] Back to Main Menu")
        console.print("=" * 50)

        ctype = input("Enter choice (0-5): ").strip()

        if ctype == "0":
            return

        if ctype not in ["1", "2", "3", "4", "5"]:
            print("[ERROR] Invalid choice.")
            input("\nPress Enter to return to category menu...")
            continue

        if ctype == "5":
            default_cid = cfg.default_channel
            prompt_msg = f"Enter ID / Username (Default: {default_cid}, 0=Back): " if default_cid else "Enter ID / Username (0=Back): "
            manual_id = input(prompt_msg).strip()
            if manual_id == "0":
                continue
            if not manual_id:
                manual_id = default_cid

            if not manual_id:
                print("[ERROR] No Channel ID provided.")
                input("\nPress Enter to return to category menu...")
                continue

            try:
                target_id = parse_channel(manual_id)
                target_entity = await client.get_entity(target_id)
                target_title = getattr(target_entity, "title", str(manual_id))
                print(f"\n✓ Success! Selected [Manual Input]: '{target_title}' (ID: {target_id})")
            except Exception as exc:
                print(f"[ERROR] Failed to locate channel '{manual_id}'. Detail: {exc}")
                input("\nPress Enter to return to category menu...")
                continue

            if await scan_and_download(session, cfg, target_entity, target_title):
                return
            continue

        category = CATEGORY_MENU[ctype]
        print(f"\nFetching your {category.label}s from Telegram...")
        try:
            channels_list = await list_dialogs(client, category)
        except Exception as exc:
            print(f"[ERROR] Failed to fetch dialogs. Detail: {exc}")
            input("\nPress Enter to return to category menu...")
            continue

        if not channels_list:
            print(f"[WARNING] No {category.label}s found in your Telegram account.")
            input("\nPress Enter to return to category menu...")
            continue

        while True:
            print(f"\nAvailable {category.label}s:")
            print("=" * 65)
            print("  [ 0] Back to Category Menu")
            for idx, dialog in enumerate(channels_list, start=1):
                display_name = dialog.name.replace("\n", " ").strip()[:40]
                print(f"  [{idx:2d}] {display_name:<40} (ID: {dialog.id})")
            print("=" * 65)

            sel = input(f"\nSelect Number (1-{len(channels_list)}, 0=Back): ").strip()
            if sel == "0":
                break
            if not sel.isdigit() or not (1 <= int(sel) <= len(channels_list)):
                print("[ERROR] Invalid selection.")
                continue

            chosen = channels_list[int(sel) - 1]
            print(f"\n✓ Success! Selected [{category.label}]: '{chosen.name}' (ID: {chosen.id})")
            if await scan_and_download(session, cfg, chosen.entity, chosen.name):
                return


# ---------------------------------------------------------------------
# Menu: download by link (3 = videos and other sites, 4 = files)
# ---------------------------------------------------------------------

async def menu_links(session, video_mode):
    cfg = STORE.load()
    if not await ensure_login(session):
        return
    client = session.client

    user_input = input("\nPaste Link(s) (0=Back): ").strip()
    if not user_input or user_input == "0":
        return

    rules = load_rules()
    routed = route(
        user_input,
        rules,
        default_channel=cfg.default_channel,
        ytdlp_enabled=video_mode and cfg.ytdlp.enabled,
        range_cap=cfg.range_cap,
    )
    for warning in routed.warnings:
        print(f"[WARNING] {warning}")

    if routed.empty:
        print("[WARNING] Invalid link or could not parse message ID.")
        input("\nPress Enter to return to main menu...")
        return

    for ch in routed.channels:
        print(f"[INFO] {ch.source} is a whole channel. Use option 2 > Manual ID / Username ({ch.peer}) to download everything in it.")

    media = MediaFilter.VIDEO if video_mode else MediaFilter.FILE
    resolved = await resolve_targets(client, routed.telegram, media, on_note=print_note)
    for ext in routed.external:
        print(f"✓ Will download with yt-dlp: {ext.url}")
    if routed.external and not detect_ffmpeg():
        print("[INFO] ffmpeg was not found: only single-file formats can be downloaded from other sites (no audio/video merging).")

    total_files = len(resolved.found) + len(routed.external)
    noun = "video(s)" if video_mode else "file(s)/document(s)"
    if not total_files:
        print("\nNo valid video messages were found from the provided link(s)." if video_mode
              else "\nNo valid files/documents were found from the provided link(s).")
        input("\nPress Enter to return to main menu...")
        return

    print(f"\nFound {total_files} {noun} ready to download.")
    if not yes(input("Continue download? (Y/N): ")):
        print("[INFO] Download cancelled. Returning to main menu...")
        return

    print("\nStarting Downloads...")
    print("-" * 50)

    def enqueue(manager):
        return manager.enqueue_telegram(resolved.found) + manager.enqueue_external(routed.external)

    items = await run_downloads(session, cfg, enqueue)
    ok, skipped, failed = count_results(items)

    print("\n" + "=" * 50)
    print("Download Summary:")
    print(f"  Total Requested {'Videos ' if video_mode else 'Files  '}: {total_files}")
    print(f"  Successfully Downloaded : {ok}")
    if skipped:
        print(f"  (already on disk)       : {skipped}")
    print(f"  Failed Downloads        : {failed}")
    print("=" * 50 + "\n")

    input("[Done] Task completed! Press Enter to return to main menu...")


# ---------------------------------------------------------------------
# Menu: link rules
# ---------------------------------------------------------------------

RULE_HELP = """
A link rule teaches the downloader a new kind of link.
Pattern placeholders (template style):
  {channel}  channel username or id      {msg}   message id, ranges like 12-20 work
  {username} public username             {cid}   private channel number (t.me/c/NUMBER)
  {topic}    forum topic (ignored)       {*}     any text in one path segment      {**}  anything
Example: mysite.com/{channel}/{msg}   matches   https://mysite.com/durov/12
"""

TARGET_CHOICES = {
    "1": ("telegram", "Download the Telegram message(s) the link points to"),
    "2": ("channel", "Treat the link as a whole channel (batch download)"),
    "3": ("rewrite", "Rewrite it into another link (for example a normal t.me link)"),
    "4": ("ytdlp", "Download the page with yt-dlp"),
}


def print_rules(rules):
    if not rules.rules:
        print("\nNo custom link rules yet. Built-in Telegram links and other websites already work without rules.")
        return
    print("\n  #  ON  PRI  ID                 TARGET    PATTERN")
    print("  " + "-" * 70)
    for i, r in enumerate(rules.rules, start=1):
        flag = "yes" if r.enabled and not r.error else (" ! " if r.error else "no ")
        print(f"  {i:<2} {flag}  {r.priority:<4} {r.id[:18]:<18} {r.target:<9} {r.pattern}")
        if r.error:
            print(f"       problem: {r.error}")


def pick_rule(rules, prompt):
    raw = input(prompt).strip()
    if raw.isdigit() and 1 <= int(raw) <= len(rules.rules):
        return rules.rules[int(raw) - 1]
    return rules.get(raw)


def rule_wizard(rules):
    print(RULE_HELP)
    rid = input("Rule id (letters, digits, - or _; 0=Cancel): ").strip()
    if not rid or rid == "0":
        return
    name = input("Name (optional): ").strip()
    style = input("Pattern style: [1] template (easy)  [2] regex (advanced)  [1]: ").strip() or "1"
    rtype = "regex" if style == "2" else "template"
    pattern = input("Pattern: ").strip()
    if not pattern:
        return
    print("What should matching links do?")
    for key, (_, text) in TARGET_CHOICES.items():
        print(f"  [{key}] {text}")
    target = TARGET_CHOICES.get(input("Choice [1]: ").strip() or "1", TARGET_CHOICES["1"])[0]

    channel_override = None
    rewrite_to = None
    if target in ("telegram", "channel"):
        raw = input("Fixed channel id or username to always use (Enter = read it from the link): ").strip()
        channel_override = parse_channel(raw) if raw else None
    if target == "rewrite":
        rewrite_to = input("Rewrite to (use {msg}, {channel}, {url} ...): ").strip()

    prio_raw = input("Priority, higher runs first [100]: ").strip() or "100"
    priority = int(prio_raw) if prio_raw.lstrip("-").isdigit() else 100

    tests = []
    example = input("Example link to test the rule (Enter = skip): ").strip()
    if example:
        tests.append({"input": example})

    rule = LinkRule(id=rid, name=name, pattern=pattern, type=rtype, target=target,
                    channel_override=channel_override, rewrite_to=rewrite_to, priority=priority, tests=tests)
    try:
        warnings = rules.add(rule)
    except RuleError as exc:
        print(f"\n[ERROR] Rule not saved: {exc}")
        return
    rules.save(PATHS.rules_file)
    for w in warnings:
        print(f"[WARNING] {w}")
    print(f"\n✓ Rule '{rid}' saved.")
    if example:
        print("Result for your example:")
        for line in route(example, rules, default_channel=STORE.load().default_channel).describe():
            print("  " + line)


def menu_rules():
    while True:
        console.clear()
        console.print(BANNER)
        console.print("[bold cyan]Link Rules - add your own supported links[/bold cyan]")
        rules = load_rules()
        print_rules(rules)
        console.print("\n  [1] Add a rule")
        console.print("  [2] Test a link")
        console.print("  [3] Turn a rule on/off")
        console.print("  [4] Delete a rule")
        console.print("  [5] Export rules to a file")
        console.print("  [6] Import rules from a file")
        console.print("  [0] Back to Main Menu")
        console.print("=" * 50)

        choice = input("Enter choice (0-6): ").strip()
        if choice == "0":
            return
        if choice == "1":
            rule_wizard(rules)
        elif choice == "2":
            text = input("Paste a link to test: ").strip()
            if text:
                cfg = STORE.load()
                result = route(text, rules, default_channel=cfg.default_channel,
                               ytdlp_enabled=cfg.ytdlp.enabled, range_cap=cfg.range_cap)
                print()
                for line in result.describe() or ["nothing recognised"]:
                    print("  " + line)
        elif choice == "3":
            rule = pick_rule(rules, "Rule number or id: ")
            if rule is None:
                print("[ERROR] No such rule.")
            else:
                rules.set_enabled(rule.id, not rule.enabled)
                rules.save(PATHS.rules_file)
                print(f"Rule '{rule.id}' is now {'ON' if rule.enabled else 'OFF'}.")
        elif choice == "4":
            rule = pick_rule(rules, "Rule number or id to delete: ")
            if rule is None:
                print("[ERROR] No such rule.")
            elif yes(input(f"Delete rule '{rule.id}'? (Y/N): ")):
                rules.remove(rule.id)
                rules.save(PATHS.rules_file)
                print("Deleted.")
        elif choice == "5":
            target = input("Save to file [link_rules_export.json]: ").strip() or "link_rules_export.json"
            try:
                with open(target, "w", encoding="utf-8") as fh:
                    fh.write(rules.export_text())
                print(f"✓ Exported {len(rules.rules)} rule(s) to {os.path.abspath(target)}")
            except OSError as exc:
                print(f"[ERROR] {exc}")
        elif choice == "6":
            source = input("Rules file to import: ").strip()
            try:
                with open(source, "r", encoding="utf-8") as fh:
                    added, problems = rules.import_text(fh.read(), replace=yes(input("Replace rules with the same id? (Y/N): ")))
                rules.save(PATHS.rules_file)
                print(f"✓ Imported {added} rule(s).")
                for p in problems:
                    print(f"[WARNING] {p}")
            except OSError as exc:
                print(f"[ERROR] {exc}")
        else:
            print("[ERROR] Invalid choice.")
        input("\nPress Enter to continue...")


# ---------------------------------------------------------------------
# Menu: settings and diagnostics
# ---------------------------------------------------------------------

def print_diagnostics(run_benchmark=False):
    cfg = STORE.load()
    rows = diagnostics.collect(PATHS, cfg, run_benchmark=run_benchmark)
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        print(f"  {label:<{width}} : {value}")


def menu_settings():
    while True:
        cfg = STORE.load()
        console.clear()
        console.print(BANNER)
        console.print("[bold cyan]Settings & Diagnostics[/bold cyan]  (saved to .env)")
        print(f"  [1] Download folder            : {cfg.downloads_path(PATHS)}")
        print(f"  [2] Max total size per batch   : {cfg.max_total_size_bytes / GB:.1f} GB")
        print(f"  [3] Files downloaded at once   : {cfg.max_concurrent_files}")
        print(f"  [4] Connections per file       : {cfg.parallel_connections}")
        print(f"  [5] Default channel (CHANNEL_ID): {cfg.default_channel if cfg.default_channel is not None else '-'}")
        print(f"  [6] Other sites via yt-dlp     : {'on' if cfg.ytdlp.enabled else 'off'}")
        print(f"  [7] yt-dlp max video height    : {cfg.ytdlp.max_height or 'no limit'}")
        print(f"  [8] yt-dlp cookies file        : {cfg.ytdlp.cookies_file or '-'}")
        print("  [9] Diagnostics (speed test)")
        print("  [0] Back to Main Menu")
        print("=" * 50)

        choice = input("Enter choice (0-9): ").strip()
        if choice == "0":
            return
        updates = {}
        try:
            if choice == "1":
                raw = input("New download folder (Enter = default): ").strip()
                updates["DOWNLOADS_DIR"] = raw
            elif choice == "2":
                updates["MAX_TOTAL_SIZE"] = str(int(float(input("Max GB per batch: ").strip()) * GB))
            elif choice == "3":
                updates["MAX_CONCURRENT_FILES"] = str(max(1, min(8, int(input("Files at once (1-8): ").strip()))))
            elif choice == "4":
                updates["PARALLEL_CONNECTIONS"] = str(max(1, min(16, int(input("Connections per file (1-16): ").strip()))))
            elif choice == "5":
                updates["CHANNEL_ID"] = input("Default channel id or username (Enter = none): ").strip()
            elif choice == "6":
                updates["YTDLP_ENABLED"] = "0" if cfg.ytdlp.enabled else "1"
            elif choice == "7":
                updates["YTDLP_MAX_HEIGHT"] = str(max(0, int(input("Max height in pixels (0 = no limit): ").strip())))
            elif choice == "8":
                updates["YTDLP_COOKIES"] = input("Path to cookies.txt (Enter = none): ").strip()
            elif choice == "9":
                print()
                with console.status("Testing AES speed..."):
                    print_diagnostics(run_benchmark=True)
                input("\nPress Enter to continue...")
                continue
            else:
                print("[ERROR] Invalid choice.")
                input("\nPress Enter to continue...")
                continue
        except ValueError:
            print("[ERROR] That is not a valid number.")
            input("\nPress Enter to continue...")
            continue
        STORE.update_values(updates)


# ---------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------

async def main():
    session = TelegramSession(PATHS, STORE)
    try:
        while True:
            cfg = STORE.load()
            try:
                os.makedirs(cfg.downloads_path(PATHS), exist_ok=True)
            except OSError:
                pass

            console.clear()
            console.print(BANNER)
            console.print("[bold green]==================================================[/bold green]")
            console.print(f"[bold white] Telegram Private Downloader ({APP_VERSION})[/bold white]")
            console.print("[bold green]==================================================[/bold green]")
            console.print("Select Download Mode:")
            console.print("  [1] My Account")
            console.print("  [2] Download ALL videos from channel (Batch Mode)")
            console.print("  [3] Download SPECIFIC video(s) by Link (Telegram or other sites)")
            console.print("  [4] Download SPECIFIC file(s)/document(s) by Link")
            console.print("  [5] Link Rules (add your own supported links)")
            console.print("  [6] Settings & Diagnostics")
            console.print("  [7] Exit")
            console.print("=" * 50)

            mode = input("Enter choice (1-7): ").strip()

            if mode == "7":
                console.print("\n[bold yellow]Exiting downloader. Goodbye![/bold yellow]\n")
                break
            elif mode == "1":
                await menu_account(session)
            elif mode == "2":
                await menu_batch(session)
            elif mode == "3":
                await menu_links(session, video_mode=True)
            elif mode == "4":
                await menu_links(session, video_mode=False)
            elif mode == "5":
                menu_rules()
            elif mode == "6":
                menu_settings()
            else:
                console.print("[bold red][ERROR] Invalid choice. Please enter a number from 1 to 7.[/bold red]")
                input("\nPress Enter to return to main menu...")
    finally:
        await session.close()


# ---------------------------------------------------------------------
# Command line switches
# ---------------------------------------------------------------------

USAGE = f"""Telegram Downloader {APP_VERSION}

  python downloader.py                 interactive menu
  python downloader.py --parse TEXT    show what links in TEXT mean (no login needed)
  python downloader.py --diag          show versions, AES speed backend, folders
  python downloader.py --version
"""


def cli_parse(text):
    cfg = STORE.load()
    result = route(text, load_rules(), default_channel=cfg.default_channel,
                   ytdlp_enabled=cfg.ytdlp.enabled, range_cap=cfg.range_cap)
    for line in result.describe() or ["nothing recognised"]:
        print(line)
    return 0 if not result.empty else 1


def handle_switches(argv):
    """Returns an exit code when a switch was handled, None to start the menu."""
    if not argv:
        return None
    head = argv[0]
    if head in ("-h", "--help"):
        print(USAGE)
        return 0
    if head == "--version":
        print(APP_VERSION)
        return 0
    if head == "--diag":
        print_diagnostics(run_benchmark=True)
        return 0
    if head == "--parse":
        return cli_parse(" ".join(argv[1:]))
    print(f"Unknown option: {head}\n")
    print(USAGE)
    return 2


if __name__ == "__main__":
    code = handle_switches(sys.argv[1:])
    if code is not None:
        sys.exit(code)
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\nExiting downloader. Goodbye!")
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower():
            print("\n" + "=" * 65)
            print("[ERROR] Telegram session file ('session.session') is locked!")
            print("Another instance of the script or terminal is currently running.")
            print("Please close other open terminals/downloader windows and try again.")
            print("=" * 65 + "\n")
            input("Press Enter to exit...")
        else:
            raise e
