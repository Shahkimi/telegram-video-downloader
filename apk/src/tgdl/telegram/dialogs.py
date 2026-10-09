"""List the user's chats and sort them into the four categories the menu offers."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ChatCategory(str, Enum):
    PRIVATE_CHANNEL = "private_channel"
    PRIVATE_GROUP = "private_group"
    PUBLIC_CHANNEL = "public_channel"
    PUBLIC_GROUP = "public_group"

    @property
    def label(self) -> str:
        return {
            ChatCategory.PRIVATE_CHANNEL: "Private Channel",
            ChatCategory.PRIVATE_GROUP: "Private Group",
            ChatCategory.PUBLIC_CHANNEL: "Public Channel",
            ChatCategory.PUBLIC_GROUP: "Public Group",
        }[self]


@dataclass
class DialogInfo:
    name: str
    id: int
    entity: Any
    category: ChatCategory


def classify(entity: Any) -> ChatCategory | None:
    from telethon.tl.types import Channel, Chat

    if isinstance(entity, Channel):
        has_username = bool(getattr(entity, "username", None))
        if entity.broadcast:
            return ChatCategory.PUBLIC_CHANNEL if has_username else ChatCategory.PRIVATE_CHANNEL
        return ChatCategory.PUBLIC_GROUP if has_username else ChatCategory.PRIVATE_GROUP
    if isinstance(entity, Chat):
        return ChatCategory.PRIVATE_GROUP  # a basic group is always private
    return None


def peer_id(entity: Any) -> int | None:
    """Telegram's marked id for a chat (-100... for channels), or None when it cannot be worked out."""
    if entity is None:
        return None
    try:
        from telethon import utils

        return utils.get_peer_id(entity)
    except Exception:  # noqa: BLE001 - not an entity Telethon understands
        return None


async def list_dialogs(client: Any, category: ChatCategory | None = None) -> list[DialogInfo]:
    """All matching chats, sorted A-Z ignoring case."""
    found: list[DialogInfo] = []
    async for dialog in client.iter_dialogs():
        cat = classify(dialog.entity)
        if cat is None or (category is not None and cat is not category):
            continue
        found.append(DialogInfo(dialog.name or "Unknown Name", dialog.id, dialog.entity, cat))
    found.sort(key=lambda d: d.name.strip().lower())
    return found
