"""Frozen copy of the original parse_telegram_link from downloader.py (v2.6.0), used as a regression oracle."""
import re


def parse_telegram_link(link_input, default_channel_id=None):
    """
    Parses a Telegram message link or ID and returns a list of (entity_id_or_username, message_id).
    Supports:
      - https://t.me/c/1234567890/456
      - https://t.me/c/1234567890/456-460 (range)
      - https://t.me/channel_username/456
      - https://t.me/channel_username/456-460 (range)
      - https://web.telegram.org/k/#-1001234567890/456
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

    # web.telegram.org private channel range link
    match_web_range = re.search(r'web\.telegram\.org/[a-z]/#-?100(\d+)/(\d+)-(\d+)', link_input) or re.search(r'web\.telegram\.org/[a-z]/#-?(\d+)/(\d+)-(\d+)', link_input)
    if match_web_range:
        raw_cid = match_web_range.group(1)
        start_id = int(match_web_range.group(2))
        end_id = int(match_web_range.group(3))
        if start_id > end_id:
            start_id, end_id = end_id, start_id
        chan_id = int(f"-100{raw_cid}")
        return [(chan_id, msg_id) for msg_id in range(start_id, end_id + 1)]

    # web.telegram.org private channel single link
    match_web = re.search(r'web\.telegram\.org/[a-z]/#-?100(\d+)/(\d+)', link_input) or re.search(r'web\.telegram\.org/[a-z]/#-?(\d+)/(\d+)', link_input)
    if match_web:
        raw_cid = match_web.group(1)
        msg_id = int(match_web.group(2))
        chan_id = int(f"-100{raw_cid}")
        return [(chan_id, msg_id)]

    # Private channel range link: t.me/c/1234567890/456-460 or telegram.me/c/1234567890/456-460
    match_private_range = re.search(r'(?:t\.me|telegram\.me)/c/(\d+)/(\d+)-(\d+)', link_input)
    if match_private_range:
        raw_cid = match_private_range.group(1)
        start_id = int(match_private_range.group(2))
        end_id = int(match_private_range.group(3))
        if start_id > end_id:
            start_id, end_id = end_id, start_id
        chan_id = int(f"-100{raw_cid}")
        return [(chan_id, msg_id) for msg_id in range(start_id, end_id + 1)]

    # Private channel single link: t.me/c/1234567890/456 or telegram.me/c/1234567890/456
    match_private = re.search(r'(?:t\.me|telegram\.me)/c/(\d+)/(\d+)', link_input)
    if match_private:
        raw_cid = match_private.group(1)
        msg_id = int(match_private.group(2))
        chan_id = int(f"-100{raw_cid}")
        return [(chan_id, msg_id)]

    # Public channel range link: t.me/username/456-460 or telegram.me/username/456-460
    match_public_range = re.search(r'(?:t\.me|telegram\.me)/([a-zA-Z0-9_]+)/(\d+)-(\d+)', link_input)
    if match_public_range:
        chan_username = match_public_range.group(1)
        start_id = int(match_public_range.group(2))
        end_id = int(match_public_range.group(3))
        if start_id > end_id:
            start_id, end_id = end_id, start_id
        return [(chan_username, msg_id) for msg_id in range(start_id, end_id + 1)]

    # Public channel single link: t.me/username/456 or telegram.me/username/456
    match_public = re.search(r'(?:t\.me|telegram\.me)/([a-zA-Z0-9_]+)/(\d+)', link_input)
    if match_public:
        chan_username = match_public.group(1)
        msg_id = int(match_public.group(2))
        return [(chan_username, msg_id)]

    return []
