"""
Decides what a pasted piece of text means.

Order, per link:
  1. enabled user rules, highest priority first
  2. built-in Telegram formats
  3. bare message id or id range (needs a default channel)
  4. any other http(s) link goes to yt-dlp (when enabled)
Anything else is reported as unmatched.
"""
from __future__ import annotations

import re

from .builtin import is_telegram_url, match_builtin
from .model import ChannelTarget, ExternalTarget, Match, Peer, RouteResult, TelegramTarget
from .rules import RuleSet

MAX_REWRITE_DEPTH = 3
DEFAULT_RANGE_CAP = 1000

_NUMERIC = re.compile(r"^(\d+)(?:-(\d+))?$")
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.I)
_DOMAINISH = re.compile(r"^(?:www\.)?[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.[a-z]{2,}(?::\d+)?(?:[/?#]\S*)?$", re.I)
_STRIP_EDGES = "<>\"'()[]{}"


def _clean_token(token: str) -> str:
    """Drop wrapping brackets/quotes and trailing sentence punctuation, repeatedly."""
    while True:
        cleaned = token.strip(_STRIP_EDGES).rstrip(".;:!?")
        if cleaned == token:
            return cleaned
        token = cleaned


def extract_candidates(text: str) -> list[str]:
    """Split pasted text into things that look like links or message ids."""
    out: list[str] = []
    for token in re.split(r"[\s,]+", text.strip()):
        token = _clean_token(token)
        if not token:
            continue
        if _SCHEME.match(token) or _NUMERIC.match(token) or _DOMAINISH.match(token):
            if token not in out:
                out.append(token)
    return out


def _expand(match: Match, range_cap: int, source: str, result: RouteResult) -> tuple[int, ...]:
    start = match.msg_start or 0
    end = match.msg_end if match.msg_end is not None else start
    if end - start + 1 > range_cap:
        result.warnings.append(f"{source}: range of {end - start + 1} messages cut to the first {range_cap}")
        end = start + range_cap - 1
    return tuple(range(start, end + 1))


def _route_one(
    candidate: str,
    rules: RuleSet | None,
    default_channel: Peer | None,
    ytdlp_enabled: bool,
    range_cap: int,
    result: RouteResult,
    depth: int = 0,
) -> None:
    if depth > MAX_REWRITE_DEPTH:
        result.warnings.append(f"{candidate}: rewrite rules loop, giving up")
        result.unmatched.append(candidate)
        return

    match: Match | None = None
    if rules is not None:
        hit = rules.match(candidate)
        if hit:
            match = hit[1]
    if match is None:
        match = match_builtin(candidate)

    if match is None:
        num = _NUMERIC.match(candidate)
        if num:
            if default_channel is None:
                result.warnings.append(f"{candidate}: a bare message id needs a default channel (CHANNEL_ID)")
                result.unmatched.append(candidate)
                return
            start = int(num.group(1))
            end = int(num.group(2)) if num.group(2) else start
            if start > end:
                start, end = end, start
            match = Match("message", "builtin:default-channel", default_channel, start, end)

    if match is None:
        if _SCHEME.match(candidate) and not candidate.lower().startswith("tg://") and not is_telegram_url(candidate):
            if ytdlp_enabled and re.match(r"^https?://", candidate, re.I):
                result.external.append(ExternalTarget(candidate, candidate, "builtin:ytdlp"))
                return
            if not ytdlp_enabled:
                result.warnings.append(f"{candidate}: not a Telegram link and yt-dlp is turned off")
        elif is_telegram_url(candidate):
            result.warnings.append(f"{candidate}: Telegram link type is not supported (invite links, stickers, etc.)")
        result.unmatched.append(candidate)
        return

    result.warnings.extend(f"{candidate}: {w}" for w in match.warnings)

    if match.kind == "message" and match.peer is not None:
        result.telegram.append(TelegramTarget(match.peer, _expand(match, range_cap, candidate, result), candidate, match.rule_id))
    elif match.kind == "channel" and match.peer is not None:
        result.channels.append(ChannelTarget(match.peer, candidate, match.rule_id))
    elif match.kind == "rewrite" and match.url:
        _route_one(match.url, rules, default_channel, ytdlp_enabled, range_cap, result, depth + 1)
    elif match.kind == "external" and match.url:
        if ytdlp_enabled:
            result.external.append(ExternalTarget(match.url, candidate, match.rule_id))
        else:
            result.warnings.append(f"{candidate}: matched a yt-dlp rule but yt-dlp is turned off")
            result.unmatched.append(candidate)
    else:
        result.unmatched.append(candidate)


def route(
    text: str,
    rules: RuleSet | None = None,
    *,
    default_channel: Peer | None = None,
    ytdlp_enabled: bool = True,
    range_cap: int = DEFAULT_RANGE_CAP,
) -> RouteResult:
    result = RouteResult()
    candidates = extract_candidates(text)
    if not candidates and text.strip():
        result.unmatched.append(text.strip()[:200])
    for candidate in candidates:
        _route_one(candidate, rules, default_channel, ytdlp_enabled, max(1, range_cap), result)
    return result


def parse_telegram_link(link_input: str, default_channel_id: Peer | None = None) -> list[tuple[Peer, int]]:
    """Compatibility wrapper with the old downloader.py signature: [(entity, message_id), ...]."""
    result = route(link_input, None, default_channel=default_channel_id, ytdlp_enabled=False, range_cap=10 ** 9)
    return result.telegram_pairs()
