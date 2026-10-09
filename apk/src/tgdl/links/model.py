"""Plain data types shared by the built-in matcher, the custom rules and the router."""
from __future__ import annotations

from dataclasses import dataclass, field

Peer = int | str


@dataclass
class Match:
    """What one rule found in one candidate string."""

    kind: str                       # "message" | "channel" | "rewrite" | "external"
    rule_id: str
    peer: Peer | None = None
    msg_start: int | None = None
    msg_end: int | None = None
    url: str | None = None          # rewrite / external
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TelegramTarget:
    peer: Peer
    msg_ids: tuple[int, ...]
    source: str
    rule_id: str


@dataclass(frozen=True)
class ChannelTarget:
    """A channel link without a message id (t.me/name). Offered as a batch scan."""

    peer: Peer
    source: str
    rule_id: str


@dataclass(frozen=True)
class ExternalTarget:
    """A non-Telegram page handed to yt-dlp."""

    url: str
    source: str
    rule_id: str


@dataclass
class RouteResult:
    telegram: list[TelegramTarget] = field(default_factory=list)
    channels: list[ChannelTarget] = field(default_factory=list)
    external: list[ExternalTarget] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.telegram or self.channels or self.external)

    def telegram_pairs(self) -> list[tuple[Peer, int]]:
        return [(t.peer, mid) for t in self.telegram for mid in t.msg_ids]

    def describe(self) -> list[str]:
        """Short human lines, used by the CLI --parse command and the app preview."""
        lines: list[str] = []
        for t in self.telegram:
            ids = t.msg_ids
            span = f"{ids[0]}" if len(ids) == 1 else f"{ids[0]}-{ids[-1]} ({len(ids)} messages)"
            lines.append(f"telegram  {t.peer} / {span}   [{t.rule_id}]")
        for c in self.channels:
            lines.append(f"channel   {c.peer}   [{c.rule_id}]")
        for e in self.external:
            lines.append(f"yt-dlp    {e.url}   [{e.rule_id}]")
        for u in self.unmatched:
            lines.append(f"no match  {u}")
        for w in self.warnings:
            lines.append(f"warning   {w}")
        return lines
