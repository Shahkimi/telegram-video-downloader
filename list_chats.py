# =====================================================================
# Telegram Chat/Channel ID Lister
# =====================================================================
# Lists every channel, group and supergroup you are in, with its ID.
# Copy the ID you want and use it as CHANNEL_ID in '.env' (or in the
# downloader's Settings menu).
#
# It uses the same login as downloader.py (session.session next to this
# file), so log in once with 'python downloader.py' first.
# =====================================================================

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "apk" / "src"))

from tgdl.config import EnvConfigStore  # noqa: E402
from tgdl.paths import resolve_paths  # noqa: E402
from tgdl.telegram.dialogs import list_dialogs  # noqa: E402
from tgdl.telegram.session import LoginError, TelegramSession  # noqa: E402


def chat_type(entity) -> str:
    from telethon.tl.types import Channel

    if isinstance(entity, Channel):
        return "Channel" if entity.broadcast else "MegaGroup"
    return "Group"


async def main() -> int:
    paths = resolve_paths("cli", ROOT)
    session = TelegramSession(paths, EnvConfigStore(paths.config_file))

    print("\nConnecting to Telegram and fetching chats...")
    try:
        client = await session.ensure_connected()
    except LoginError as exc:
        print(f"\n[ERROR] {exc.message}")
        print("Run 'python downloader.py' and log in from the My Account menu first.")
        return 1

    print("=" * 70)
    print(f"{'Chat/Channel Name':<35} | {'ID':<15} | {'Type':<12}")
    print("=" * 70)
    try:
        for chat in await list_dialogs(client):
            display_name = chat.name.replace("\n", " ").strip()
            print(f"{display_name[:35]:<35} | {chat.id:<15} | {chat_type(chat.entity):<12}")
    except Exception as exc:  # noqa: BLE001
        print(f"\n[ERROR] Failed to fetch dialogs. Detail: {exc}")
        return 1
    finally:
        await session.close()

    print("=" * 70)
    print("\nInstructions:")
    print("1. Find your target channel/group in the list above.")
    print("2. Copy the corresponding ID (it will look like a long negative number, e.g. -100xxxxxxxxxx).")
    print("3. Open your '.env' file, set it as: CHANNEL_ID=copied_id_here")
    print("4. Save the '.env' file and run 'python downloader.py' to start downloading.\n")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
