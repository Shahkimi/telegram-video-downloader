"""
Built-in Telegram link formats.

Supported:
  https://t.me/c/1234567890/456              private channel / supergroup post
  https://t.me/c/1234567890/456-460          range
  https://t.me/c/1234567890/12/456           forum topic 12, message 456
  https://t.me/username/456[-460]            public post (also /s/username/456, telegram.me, telegram.dog)
  https://t.me/username/12/456               forum topic in a public group
  https://web.telegram.org/{a,k,z}/#-1001234567890/456[-460]
  tg://privatepost?channel=1234567890&post=456
  tg://resolve?domain=username&post=456
  https://t.me/username                      channel only, offered as a batch scan

Query strings such as ?single, ?comment=7 or ?thread=3 are ignored.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from .model import Match

DOMAIN = r"(?<![\w.-])(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)"
TAIL = r"(?:[/?#]\S*)?$"
USER = r"(?P<user>[A-Za-z0-9_]{1,32})"

# first path segments on t.me that are never a channel username
RESERVED = frozenset({
    "c", "s", "joinchat", "addstickers", "addemoji", "addtheme", "setlanguage", "share", "proxy",
    "socks", "login", "confirmphone", "bg", "invoice", "boost", "addlist", "iv", "giftcode", "m",
    "nft", "contact", "call", "web", "k", "a", "z", "telegrampassport", "msg", "dl",
})

_NUM = r"(?P<a>\d+)(?:-(?P<a_end>\d+))?(?:/(?P<b>\d+)(?:-(?P<b_end>\d+))?)?"

_PRIVATE = re.compile(DOMAIN + r"/c/(?P<cid>\d+)/" + _NUM + TAIL, re.I)
_PUBLIC_S = re.compile(DOMAIN + r"/s/" + USER + "/" + _NUM + TAIL, re.I)
_PUBLIC = re.compile(DOMAIN + "/" + USER + "/" + _NUM + TAIL, re.I)
_CHANNEL_ONLY = re.compile(DOMAIN + "/" + USER + r"/?(?:[?#]\S*)?$", re.I)
_WEB = re.compile(
    r"(?<![\w.-])(?:https?://)?web\.telegram\.org/[a-z]/?#(?P<chat>-?\d+)[/_](?P<a>\d+)(?:-(?P<a_end>\d+))?(?:[/?#]\S*)?$",
    re.I,
)
_TG_SCHEME = re.compile(r"^tg://(?P<host>privatepost|resolve)\?(?P<query>\S+)$", re.I)


def _range(a: str, a_end: str | None, b: str | None, b_end: str | None) -> tuple[int, int]:
    """With a topic segment present the message is the second number."""
    if b is not None:
        start, end = int(b), int(b_end) if b_end else int(b)
    else:
        start, end = int(a), int(a_end) if a_end else int(a)
    if start > end:
        start, end = end, start
    return start, end


def _private_peer(cid: str) -> int:
    return int(f"-100{cid}")


def _web_peer(raw: str) -> int:
    digits = raw.lstrip("-")
    if digits.startswith("100") and len(digits) > 3:
        digits = digits[3:]
    return int(f"-100{digits}")


def _tg_scheme(text: str) -> Match | None:
    m = _TG_SCHEME.match(text.strip())
    if not m:
        return None
    query = parse_qs(urlsplit(text.strip()).query)
    post = (query.get("post") or [""])[0]
    if not post.isdigit():
        return None
    if m.group("host").lower() == "privatepost":
        channel = (query.get("channel") or [""])[0]
        if not channel.isdigit():
            return None
        peer: int | str = _private_peer(channel)
        rule = "builtin:tg-privatepost"
    else:
        domain = (query.get("domain") or [""])[0]
        if not re.fullmatch(r"[A-Za-z0-9_]{1,32}", domain):
            return None
        peer = domain
        rule = "builtin:tg-resolve"
    return Match(kind="message", rule_id=rule, peer=peer, msg_start=int(post), msg_end=int(post))


def match_builtin(text: str) -> Match | None:
    """Match one candidate string against the built-in Telegram formats."""
    text = text.strip()
    if not text:
        return None

    hit = _tg_scheme(text)
    if hit:
        return hit

    m = _WEB.search(text)
    if m:
        start, end = _range(m.group("a"), m.group("a_end"), None, None)
        return Match("message", "builtin:web-telegram", _web_peer(m.group("chat")), start, end)

    m = _PRIVATE.search(text)
    if m:
        start, end = _range(m.group("a"), m.group("a_end"), m.group("b"), m.group("b_end"))
        return Match("message", "builtin:private", _private_peer(m.group("cid")), start, end)

    m = _PUBLIC_S.search(text)
    if m and m.group("user").lower() not in RESERVED:
        start, end = _range(m.group("a"), m.group("a_end"), m.group("b"), m.group("b_end"))
        return Match("message", "builtin:public-preview", m.group("user"), start, end)

    m = _PUBLIC.search(text)
    if m and m.group("user").lower() not in RESERVED:
        start, end = _range(m.group("a"), m.group("a_end"), m.group("b"), m.group("b_end"))
        return Match("message", "builtin:public", m.group("user"), start, end)

    m = _CHANNEL_ONLY.search(text)
    if m and m.group("user").lower() not in RESERVED and not m.group("user").startswith("+"):
        return Match("channel", "builtin:channel", m.group("user"))

    return None


TELEGRAM_HOSTS = ("t.me", "telegram.me", "telegram.dog", "web.telegram.org", "telegram.org")


def is_telegram_url(text: str) -> bool:
    host = re.sub(r"^(?:[a-z][a-z0-9+.-]*://)?(?:www\.)?", "", text.strip(), flags=re.I).split("/", 1)[0].lower()
    return host in TELEGRAM_HOSTS or text.strip().lower().startswith("tg://")


SAMPLE_LINKS = (
    "https://t.me/durov/1",
    "https://t.me/c/1234567890/1",
    "https://web.telegram.org/k/#-1001234567890/1",
)
