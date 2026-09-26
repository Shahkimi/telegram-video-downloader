# =====================================================================
# Telegram Private Video Downloader
# =====================================================================
# This script logs into Telegram using your API ID & Hash and
# downloads video files from a designated channel or specific post links.
# It automatically reads configurations from a local '.env' file.
# =====================================================================

import os
import sys
import io
import re
import asyncio
import sqlite3

if sys.stdout and getattr(sys.stdout, 'encoding', None) and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass

if sys.stderr and getattr(sys.stderr, 'encoding', None) and sys.stderr.encoding.lower() != 'utf-8':
    try:
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.types import Channel, Chat
from rich.console import Console

from rich.progress import (
    Progress,
    TextColumn,
    BarColumn,
    DownloadColumn,
    TransferSpeedColumn,
    TimeRemainingColumn,
    SpinnerColumn
)

# Load configuration from .env file
load_dotenv(override=True)

console = Console()

# ---------------------------------------------------------------------
# Configuration Reading and Validation
# ---------------------------------------------------------------------

api_id_raw = os.getenv("API_ID")
api_hash = os.getenv("API_HASH")
channel_id_raw = os.getenv("CHANNEL_ID")

default_pc_downloads = os.path.join(os.path.expanduser("~"), "Downloads", "Telegram Downloads")
downloads_dir = os.getenv("DOWNLOADS_DIR", default_pc_downloads)
if downloads_dir == "downloads":
    downloads_dir = default_pc_downloads
max_total_size_raw = os.getenv("MAX_TOTAL_SIZE", str(20 * 1024 * 1024 * 1024))



# Parsed default CHANNEL_ID (if provided)
channel_id = None
if channel_id_raw:
    try:
        if channel_id_raw.startswith("-") or channel_id_raw.isdigit():
            channel_id = int(channel_id_raw)
        else:
            channel_id = channel_id_raw
    except ValueError:
        channel_id = channel_id_raw

# Validating Max Size Limit
try:
    MAX_TOTAL_SIZE = int(max_total_size_raw)
except ValueError:
    print(f"\n[WARNING] Invalid MAX_TOTAL_SIZE value. Using default of 20 GB.")
    MAX_TOTAL_SIZE = 20 * 1024 * 1024 * 1024

# ---------------------------------------------------------------------
# Telegram Client Lazy Initialization
# ---------------------------------------------------------------------

client = None

def get_client():
    """
    Returns an initialized TelegramClient instance if API credentials exist in .env, else None.
    """
    global client
    load_dotenv(override=True)
    api_id_raw = os.getenv("API_ID")
    api_hash_raw = os.getenv("API_HASH")

    api_id = 0
    if api_id_raw:
        try:
            api_id = int(api_id_raw)
        except ValueError:
            api_id = 0
    api_hash = api_hash_raw.strip() if api_hash_raw else ""

    if not api_id or not api_hash:
        return None

    if client is None:
        client = TelegramClient("session", api_id, api_hash)
    elif getattr(client, 'api_id', None) != api_id or getattr(client, 'api_hash', None) != api_hash:
        client = TelegramClient("session", api_id, api_hash)

    return client


async def ensure_telegram_logged_in(force_credentials=False):
    """
    Ensures that Telegram client is initialized, credentials exist in .env, and user is authorized.
    If API credentials are missing or force_credentials is True, prompts user for API_ID & API_HASH.
    Then prompts for phone number & 2FA if not logged in.
    """
    global client
    c = get_client()

    if c is None or force_credentials:
        curr_api_id = os.getenv("API_ID", "")
        curr_api_hash = os.getenv("API_HASH", "")

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

        update_env_file(in_id, in_hash)
        c = get_client()

    if not c.is_connected():
        await c.connect()

    if not await c.is_user_authorized():
        print("\nConnecting to Telegram authentication...")
        await c.start()

    return c

# Limit concurrent downloads to 3 files to avoid FloodWaitError (Telegram rate-limits)
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(3)

# ---------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------

def update_env_file(new_api_id, new_api_hash):
    """
    Updates or appends API_ID and API_HASH in local .env file.
    """
    env_path = ".env"
    env_lines = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            env_lines = f.readlines()

    api_id_found = False
    api_hash_found = False

    new_lines = []
    for line in env_lines:
        if line.strip().startswith("API_ID="):
            new_lines.append(f"API_ID={new_api_id}\n")
            api_id_found = True
        elif line.strip().startswith("API_HASH="):
            new_lines.append(f"API_HASH={new_api_hash}\n")
            api_hash_found = True
        else:
            new_lines.append(line)

    if not api_id_found:
        new_lines.append(f"API_ID={new_api_id}\n")
    if not api_hash_found:
        new_lines.append(f"API_HASH={new_api_hash}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    os.environ["API_ID"] = str(new_api_id)
    os.environ["API_HASH"] = str(new_api_hash)

def clean_filename(name):
    """
    Remove invalid characters from filename and clean extra whitespaces.
    """
    cleaned = re.sub(r'[\\/*?:"<>|]', "", name)
    cleaned = re.sub(r'\s+', " ", cleaned).strip()
    return cleaned


def parse_telegram_link(link_input, default_channel_id=None):
    """
    Parses a Telegram message link or ID and returns a list of (entity_id_or_username, message_id).
    Supports:
      - https://t.me/c/1234567890/456
      - https://t.me/c/1234567890/456-460 (range)
      - https://t.me/channel_username/456
      - https://t.me/channel_username/456-460 (range)
      - 456 or 456-460 (message ID or range using default_channel_id)
    """
    link_input = link_input.strip()
    if not link_input:
        return []

    # Single or range numeric ID: 456 or 456-460
    match_num_range = re.match(r'^(\d+)-(\d+)$', link_input)
    if match_num_range and default_channel_id:
        start_id = int(match_num_range.group(1))
        end_id = int(match_num_range.group(2))
        if start_id > end_id:
            start_id, end_id = end_id, start_id
        return [(default_channel_id, msg_id) for msg_id in range(start_id, end_id + 1)]

    if link_input.isdigit() and default_channel_id:
        return [(default_channel_id, int(link_input))]

    # Private channel range link: t.me/c/1234567890/456-460
    match_private_range = re.search(r't\.me/c/(\d+)/(\d+)-(\d+)', link_input)
    if match_private_range:
        raw_cid = match_private_range.group(1)
        start_id = int(match_private_range.group(2))
        end_id = int(match_private_range.group(3))
        if start_id > end_id:
            start_id, end_id = end_id, start_id
        chan_id = int(f"-100{raw_cid}")
        return [(chan_id, msg_id) for msg_id in range(start_id, end_id + 1)]

    # Private channel single link: t.me/c/1234567890/456
    match_private = re.search(r't\.me/c/(\d+)/(\d+)', link_input)
    if match_private:
        raw_cid = match_private.group(1)
        msg_id = int(match_private.group(2))
        chan_id = int(f"-100{raw_cid}")
        return [(chan_id, msg_id)]

    # Public channel range link: t.me/username/456-460
    match_public_range = re.search(r't\.me/([a-zA-Z0-9_]+)/(\d+)-(\d+)', link_input)
    if match_public_range:
        chan_username = match_public_range.group(1)
        start_id = int(match_public_range.group(2))
        end_id = int(match_public_range.group(3))
        if start_id > end_id:
            start_id, end_id = end_id, start_id
        return [(chan_username, msg_id) for msg_id in range(start_id, end_id + 1)]

    # Public channel single link: t.me/username/456
    match_public = re.search(r't\.me/([a-zA-Z0-9_]+)/(\d+)', link_input)
    if match_public:
        chan_username = match_public.group(1)
        msg_id = int(match_public.group(2))
        return [(chan_username, msg_id)]

    return []

# ---------------------------------------------------------------------
# Single File Download Worker
# ---------------------------------------------------------------------

async def download_worker(message, index, total_count, progress):
    """
    Asynchronous task worker to download a single file with live progress display.
    """
    file_size = getattr(message.file, "size", 0) if message.file else 0

    # 1. Try to get title from message text/caption
    file_name = ""
    if message.text:
        first_line = message.text.split("\n")[0].strip()
        if first_line:
            file_name = clean_filename(first_line)[:50]

    # 2. Fall back to message.file.name
    if not file_name and message.file and getattr(message.file, "name", None):
        file_name = clean_filename(message.file.name)

    # 3. Fall back to date/time format
    if not file_name:
        msg_date = getattr(message, "date", None)
        date_str = msg_date.strftime("%Y%m%d_%H%M%S") if msg_date else f"msg_{message.id}"
        file_name = f"file_{date_str}"

    # Ensure correct extension (.mp4, .pdf, .png, .jpg, .docx, .zip, etc.)
    ext = getattr(message.file, "ext", "") if message.file else ""
    if ext:
        if not ext.startswith("."):
            ext = f".{ext}"
        if not file_name.lower().endswith(ext.lower()):
            file_name = f"{file_name}{ext}"
    else:
        if not os.path.splitext(file_name)[1]:
            file_name = f"{file_name}.bin"

    async with DOWNLOAD_SEMAPHORE:
        task_id = progress.add_task(
            "download",
            filename=file_name,
            index=index,
            total_count=total_count,
            total=file_size or 1
        )
        
        def progress_cb(received, total):
            if total:
                progress.update(task_id, completed=received, total=total)
        
        try:
            output_path = os.path.join(downloads_dir, file_name)
            await message.download_media(
                file=output_path,
                progress_callback=progress_cb
            )
            progress.remove_task(task_id)
            progress.console.print(f"✓ [Finished] File ID: {message.id} ({file_name})")
            return True
        except Exception as e:
            progress.remove_task(task_id)
            progress.console.print(f"✗ [Failed] File ID: {message.id} ({file_name}). Error: {e}")
            return False

BANNER = """[bold green]
  ██████╗  █████╗ ██████╗ ██╗   ██╗███████╗     ██╗
  ██╔══██╗██╔══██╗██╔══██╗██║   ██║██╔════╝     ██║
  ██████╔╝███████║██████╔╝██║   ██║█████╗       ██║
  ██╔═══╝ ██╔══██║██╔══██╗╚██╗ ██╔╝██╔══╝  ██   ██║
  ██║     ██║  ██║██║  ██║ ╚████╔╝ ███████╗╚█████╔╝
  ╚═╝     ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚══════╝ ╚════╝ 
               DEVELOPED BY: PARVEJ[/bold green]
"""

# ---------------------------------------------------------------------
# Downloader Main Function
# ---------------------------------------------------------------------

async def main():
    os.makedirs(downloads_dir, exist_ok=True)

    while True:
        console.clear()
        console.print(BANNER)
        console.print("[bold green]==================================================[/bold green]")
        console.print("[bold white] Telegram Private Downloader[/bold white]")
        console.print("[bold green]==================================================[/bold green]")
        console.print("Select Download Mode:")
        console.print("  [1] My Account")
        console.print("  [2] Download ALL videos from channel (Batch Mode)")
        console.print("  [3] Download SPECIFIC video(s) by Post/Message Link")
        console.print("  [4] Download SPECIFIC file(s)/document(s) by Link")
        console.print("  [5] Exit")
        console.print("=" * 50)

        mode = input("Enter choice (1-5): ").strip()

        if mode == "5":
            console.print("\n[bold yellow]Exiting downloader. Goodbye![/bold yellow]\n")
            break

        if mode not in ["1", "2", "3", "4", "5"]:
            console.print("[bold red][ERROR] Invalid choice. Please enter 1, 2, 3, 4, or 5.[/bold red]")
            input("\nPress Enter to return to main menu...")
            continue

        # -----------------------------------------------------------------
        # MODE 1: My Account Settings (Login & Logout)
        # -----------------------------------------------------------------
        if mode == "1":
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

                acc_choice = input("Enter choice (0-2): ").strip()

                if acc_choice == "0":
                    break

                elif acc_choice == "1":
                    console.print("\n[bold cyan]--- Telegram Login ---[/bold cyan]")
                    c = get_client()
                    is_auth = False
                    if c:
                        try:
                            if not c.is_connected():
                                await c.connect()
                            is_auth = await c.is_user_authorized()
                        except Exception:
                            is_auth = False

                    if is_auth:
                        try:
                            me = await c.get_me()
                            name = f"{me.first_name or ''} {me.last_name or ''}".strip()
                            phone = getattr(me, 'phone', 'N/A')
                            username = f"@{me.username}" if me.username else "No Username"
                            print(f"\n✓ Currently Logged In as: {name} ({username} | +{phone})")
                        except Exception:
                            print("\n✓ Currently Logged In to Telegram.")
                        
                        relog = input("\nDo you want to re-login / update API credentials? (Y/N): ").strip().lower()
                        if relog not in ['y', 'yes']:
                            continue
                        c = await ensure_telegram_logged_in(force_credentials=True)
                    else:
                        c = await ensure_telegram_logged_in(force_credentials=False)

                    try:
                        me = await c.get_me()
                        name = f"{me.first_name or ''} {me.last_name or ''}".strip()
                        username = f"@{me.username}" if me.username else "No Username"
                        print(f"\n✓ Login successful! Account: {name} ({username} | ID: {me.id})")
                        print("You now have full permission to use all downloader features.")
                    except Exception as e:
                        print(f"\n[ERROR] Login failed or interrupted. Detail: {e}")

                    input("\nPress Enter to return to My Account menu...")

                elif acc_choice == "2":
                    console.print("\n[bold cyan]--- Logout Telegram ---[/bold cyan]")
                    c = get_client()
                    is_auth = False
                    if c:
                        try:
                            if not c.is_connected():
                                await c.connect()
                            is_auth = await c.is_user_authorized()
                        except Exception:
                            is_auth = False

                    if not is_auth:
                        print("\n[INFO] You are not currently logged into any Telegram account.")
                        input("\nPress Enter to return to My Account menu...")
                        continue

                    acc_name = "your account"
                    try:
                        me = await c.get_me()
                        acc_name = f"{me.first_name or ''} {me.last_name or ''}".strip()
                    except Exception:
                        pass

                    confirm = input(f"\nAre you sure you want to logout from '{acc_name}'? (Y/N): ").strip().lower()
                    if confirm not in ['y', 'yes']:
                        print("[INFO] Logout cancelled. Returning to My Account menu...")
                        continue

                    try:
                        await c.log_out()
                        print("\n✓ Successfully logged out from Telegram account.")
                        print("Your session has been cleared.")
                    except Exception as e:
                        print(f"\n[ERROR] Logout failed. Detail: {e}")

                    input("\nPress Enter to return to My Account menu...")
                else:
                    print("[ERROR] Invalid choice. Please enter 0, 1, or 2.")
                    input("\nPress Enter to return to My Account menu...")

        # -----------------------------------------------------------------
        # Authorization Check for Download Modes (2, 3, 4)
        # -----------------------------------------------------------------
        if mode in ["2", "3", "4"]:
            c = get_client()
            is_auth = False
            if c:
                try:
                    if not c.is_connected():
                        await c.connect()
                    is_auth = await c.is_user_authorized()
                except Exception:
                    is_auth = False

            if not is_auth:
                print("\n[INFO] Authentication required to proceed.")
                confirm = input("Would you like to login to Telegram now? (Y/N): ").strip().lower()
                if confirm not in ['y', 'yes']:
                    continue
                try:
                    c = await ensure_telegram_logged_in()
                except Exception as e:
                    print(f"\n[ERROR] Authentication failed. Detail: {e}")
                    input("\nPress Enter to return to main menu...")
                    continue

        # -----------------------------------------------------------------
        # MODE 2: Download ALL videos from channel (Batch Mode)
        # -----------------------------------------------------------------
        if mode == "2":

            while True:
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
                    break

                if ctype not in ["1", "2", "3", "4", "5"]:
                    print("[ERROR] Invalid choice.")
                    input("\nPress Enter to return to category menu...")
                    continue

                target_entity = None
                target_title = ""
                target_id = None
                category_label = ""

                if ctype == "5":
                    default_cid = channel_id
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
                        if str(manual_id).startswith("-") or str(manual_id).isdigit():
                            target_id = int(manual_id)
                        else:
                            target_id = manual_id
                        target_entity = await client.get_entity(target_id)
                        target_title = getattr(target_entity, 'title', str(manual_id))
                        category_label = "Manual Input"
                        print(f"\n✓ Success! Selected [{category_label}]: '{target_title}' (ID: {target_id})")
                    except Exception as e:
                        print(f"[ERROR] Failed to locate channel '{manual_id}'. Detail: {e}")
                        input("\nPress Enter to return to category menu...")
                        continue

                elif ctype in ["1", "2", "3", "4"]:
                    type_names = {
                        "1": "Private Channel",
                        "2": "Private Group",
                        "3": "Public Channel",
                        "4": "Public Group"
                    }
                    category_label = type_names[ctype]
                    
                    print(f"\nFetching your {category_label}s from Telegram...")
                    
                    channels_list = []
                    try:
                        async for dialog in client.iter_dialogs():
                            entity = dialog.entity
                            name = dialog.name or "Unknown Name"
                            has_username = bool(getattr(entity, 'username', None))
                            
                            if isinstance(entity, Channel):
                                is_broadcast = entity.broadcast
                                if ctype == "1" and is_broadcast and not has_username:
                                    channels_list.append((name, dialog.id, entity))
                                elif ctype == "2" and not is_broadcast and not has_username:
                                    channels_list.append((name, dialog.id, entity))
                                elif ctype == "3" and is_broadcast and has_username:
                                    channels_list.append((name, dialog.id, entity))
                                elif ctype == "4" and not is_broadcast and has_username:
                                    channels_list.append((name, dialog.id, entity))
                            elif isinstance(entity, Chat):
                                # Basic Chat group is always private group
                                if ctype == "2":
                                    channels_list.append((name, dialog.id, entity))
                    except Exception as e:
                        print(f"[ERROR] Failed to fetch dialogs. Detail: {e}")
                        input("\nPress Enter to return to category menu...")
                        continue

                    if not channels_list:
                        print(f"[WARNING] No {category_label}s found in your Telegram account.")
                        input("\nPress Enter to return to category menu...")
                        continue

                    # Sort alphabetically (A to Z) case-insensitively
                    channels_list.sort(key=lambda x: x[0].strip().lower())

                    # Loop for channel selection within this category
                    download_completed = False
                    while True:
                        print(f"\nAvailable {category_label}s:")
                        print("=" * 65)
                        print(f"  [ 0] Back to Category Menu")
                        for idx, (name, cid, ent) in enumerate(channels_list, start=1):
                            display_name = name.replace("\n", " ").strip()[:40]
                            print(f"  [{idx:2d}] {display_name:<40} (ID: {cid})")
                        print("=" * 65)

                        sel = input(f"\nSelect Number (1-{len(channels_list)}, 0=Back): ").strip()
                        if sel == "0":
                            break
                        if not sel.isdigit() or not (1 <= int(sel) <= len(channels_list)):
                            print("[ERROR] Invalid selection.")
                            continue

                        selected_item = channels_list[int(sel) - 1]
                        target_title = selected_item[0]
                        target_id = selected_item[1]
                        target_entity = selected_item[2]

                        print(f"\n✓ Success! Selected [{category_label}]: '{target_title}' (ID: {target_id})")
                        print(f"Output Directory  : {downloads_dir}/")
                        print(f"Max Download Limit: {MAX_TOTAL_SIZE / (1024**3):.2f} GB")

                        print("\nScanning messages and detecting video files...")
                        print("-" * 50)

                        download_queue = []
                        total_scanned_size = 0
                        limit_reached = False

                        async for message in client.iter_messages(target_entity, reverse=True):
                            if not message.video and not (message.document and getattr(message.document, 'mime_type', '').startswith('video/')):
                                continue

                            file_size = getattr(message.file, "size", 0) if message.file else 0

                            if total_scanned_size + file_size > MAX_TOTAL_SIZE:
                                limit_reached = True
                                break

                            download_queue.append(message)
                            total_scanned_size += file_size

                        total_files = len(download_queue)
                        print(f"Found {total_files} video(s) detected in '{target_title}'.")
                        print(f"Total size budget: {total_scanned_size / (1024**3):.2f} GB")
                        
                        if limit_reached:
                            print("Note: Stop limit reached during scan. Some newer files were excluded to stay under limits.")

                        if not download_queue:
                            print("No videos found to download.")
                            continue

                        confirm = input("\nContinue download? (Y/N): ").strip().lower()
                        if confirm not in ['y', 'yes']:
                            print("[INFO] Download cancelled. Returning to channel list...")
                            continue

                        print("\nStarting Downloads...")
                        print("-" * 50)

                        with Progress(
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
                            transient=True
                        ) as progress:
                            tasks = [
                                download_worker(msg, idx + 1, total_files, progress)
                                for idx, msg in enumerate(download_queue)
                            ]
                            results = await asyncio.gather(*tasks)

                        success_count = sum(1 for r in results if r)

                        print("\n" + "=" * 50)
                        print("Download Summary:")
                        print(f"  Target Channel          : {target_title}")
                        print(f"  Total Videos Checked    : {total_files}")
                        print(f"  Successfully Downloaded : {success_count}")
                        print(f"  Failed Downloads        : {total_files - success_count}")
                        print(f"  Total Size Budgeted     : {total_scanned_size / (1024**3):.2f} GB")
                        print("=" * 50 + "\n")
                        
                        input("[Done] Task completed! Press Enter to return to main menu...")
                        download_completed = True
                        break

                    if download_completed:
                        break

        # -----------------------------------------------------------------
        # MODE 3: Download Specific Video(s) by Link
        # -----------------------------------------------------------------
        elif mode == "3":
            user_input = input("\nPaste Link(s) (0=Back): ").strip()
            if not user_input or user_input == "0":
                continue

            raw_links = re.split(r'[\s,]+', user_input)
            parsed_targets = []
            for l in raw_links:
                parsed = parse_telegram_link(l, channel_id)
                parsed_targets.extend(parsed)

            if not parsed_targets:
                print("[WARNING] Invalid link or could not parse message ID.")
                input("\nPress Enter to return to main menu...")
                continue

            download_queue = []

            for target_entity, msg_id in parsed_targets:
                try:
                    entity = await client.get_entity(target_entity)
                    message = await client.get_messages(entity, ids=msg_id)
                    
                    if not message:
                        print(f"[WARNING] Message ID {msg_id} not found in target channel.")
                        continue

                    if not message.video and not (message.document and getattr(message.document, 'mime_type', '').startswith('video/')):
                        print(f"[WARNING] Message ID {msg_id} does not contain a video file.")
                        continue

                    download_queue.append(message)
                    print(f"✓ Found video in Message ID {msg_id}")
                except Exception as e:
                    print(f"[ERROR] Failed to fetch message ID {msg_id}. Detail: {e}")

            total_files = len(download_queue)
            if not download_queue:
                print("\nNo valid video messages were found from the provided link(s).")
                input("\nPress Enter to return to main menu...")
                continue

            print(f"\nFound {total_files} video(s) ready to download.")
            confirm = input("Continue download? (Y/N): ").strip().lower()
            if confirm not in ['y', 'yes']:
                print("[INFO] Download cancelled. Returning to main menu...")
                continue

            print("\nStarting Downloads...")
            print("-" * 50)

            with Progress(
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
                transient=True
            ) as progress:
                tasks = [
                    download_worker(msg, idx + 1, total_files, progress)
                    for idx, msg in enumerate(download_queue)
                ]
                results = await asyncio.gather(*tasks)

            success_count = sum(1 for r in results if r)

            print("\n" + "=" * 50)
            print("Download Summary:")
            print(f"  Total Requested Videos  : {total_files}")
            print(f"  Successfully Downloaded : {success_count}")
            print(f"  Failed Downloads        : {total_files - success_count}")
            print("=" * 50 + "\n")
            
            input("[Done] Task completed! Press Enter to return to main menu...")

        # -----------------------------------------------------------------
        # MODE 4: Download Specific File(s)/Document(s) by Link (PDF, Images, Docs, etc.)
        # -----------------------------------------------------------------
        elif mode == "4":
            user_input = input("\nPaste Link(s) (0=Back): ").strip()
            if not user_input or user_input == "0":
                continue

            raw_links = re.split(r'[\s,]+', user_input)
            parsed_targets = []
            for l in raw_links:
                parsed = parse_telegram_link(l, channel_id)
                parsed_targets.extend(parsed)

            if not parsed_targets:
                print("[WARNING] Invalid link or could not parse message ID.")
                input("\nPress Enter to return to main menu...")
                continue

            download_queue = []

            for target_entity, msg_id in parsed_targets:
                try:
                    entity = await client.get_entity(target_entity)
                    message = await client.get_messages(entity, ids=msg_id)
                    
                    if not message:
                        print(f"[WARNING] Message ID {msg_id} not found in target channel.")
                        continue

                    if not message.file:
                        print(f"[WARNING] Message ID {msg_id} does not contain any file/document.")
                        continue

                    if message.video or (message.document and getattr(message.document, 'mime_type', '').startswith('video/')):
                        print(f"[WARNING] Message ID {msg_id} is a video file. (Use Option 3 for videos)")
                        continue

                    download_queue.append(message)
                    ext = getattr(message.file, "ext", "") or ""
                    print(f"✓ Found file ({ext.upper().strip('.') or 'DOCUMENT'}) in Message ID {msg_id}")
                except Exception as e:
                    print(f"[ERROR] Failed to fetch message ID {msg_id}. Detail: {e}")

            total_files = len(download_queue)
            if not download_queue:
                print("\nNo valid files/documents were found from the provided link(s).")
                input("\nPress Enter to return to main menu...")
                continue

            print(f"\nFound {total_files} file(s)/document(s) ready to download.")
            confirm = input("Continue download? (Y/N): ").strip().lower()
            if confirm not in ['y', 'yes']:
                print("[INFO] Download cancelled. Returning to main menu...")
                continue

            print("\nStarting Downloads...")
            print("-" * 50)

            with Progress(
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
                transient=True
            ) as progress:
                tasks = [
                    download_worker(msg, idx + 1, total_files, progress)
                    for idx, msg in enumerate(download_queue)
                ]
                results = await asyncio.gather(*tasks)

            success_count = sum(1 for r in results if r)

            print("\n" + "=" * 50)
            print("Download Summary:")
            print(f"  Total Requested Files   : {total_files}")
            print(f"  Successfully Downloaded : {success_count}")
            print(f"  Failed Downloads        : {total_files - success_count}")
            print("=" * 50 + "\n")
            
            input("[Done] Task completed! Press Enter to return to main menu...")

# ---------------------------------------------------------------------
# Start Script
# ---------------------------------------------------------------------

if __name__ == "__main__":
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



