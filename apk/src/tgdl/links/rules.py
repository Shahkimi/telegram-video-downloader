"""
User-defined link rules.

A rule says "links that look like PATTERN mean TARGET". Two pattern styles:

  template   mysite.com/{channel}/{msg}      readable placeholders, scheme and www optional
  regex      ^https?://x\\.example/p/(?P<cid>\\d+)_(?P<msg>\\d+)$     full control, use named groups

Template placeholders
  {channel}   username, -100id, or a plain number (read as a private channel id, like t.me/c/ID)
  {username}  public username
  {cid}       private channel number (becomes -100<cid>)
  {peer}      username or id taken literally
  {msg}       message id; ranges like 12-20 work
  {topic}     forum topic number (ignored)
  {*}         any text inside one path segment
  {**}        anything

Regex rules use the same group names: channel, username, cid, peer, msg, msg_end.

Targets
  telegram   download the message(s)                 needs msg + a channel (group or channel_override)
  channel    open the channel as a batch scan        needs a channel
  rewrite    turn the link into another link         needs rewrite_to, may use {groups} and {url}
  ytdlp      hand the link to yt-dlp
"""
from __future__ import annotations

import json
import os
import re
import string
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import atomic_write_text, parse_channel
from .builtin import SAMPLE_LINKS, match_builtin
from .model import Match, Peer

RULES_VERSION = 1
MAX_PATTERN_LEN = 300
TYPES = ("template", "regex")
TARGETS = ("telegram", "channel", "rewrite", "ytdlp")
ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")

_PLACEHOLDERS: dict[str, str] = {
    "channel": r"(?P<channel>-?\d+|[A-Za-z][A-Za-z0-9_]{0,31})",
    "username": r"(?P<username>[A-Za-z][A-Za-z0-9_]{0,31})",
    "cid": r"(?P<cid>\d+)",
    "peer": r"(?P<peer>-?\d+|[A-Za-z][A-Za-z0-9_]{0,31})",
    "msg": r"(?P<msg>\d+)(?:-(?P<msg_end>\d+))?",
    "topic": r"(?P<topic>\d+)",
    "*": r"[^/?#]*",
    "**": r".*",
}
_PEER_GROUPS = ("channel", "username", "cid", "peer")
_SCHEME_RE = re.compile(r"^([a-z][a-z0-9+.-]*)://", re.I)
_SUFFIX = r"/?(?:[?#].*)?"


class RuleError(ValueError):
    def __init__(self, message: str, field_name: str | None = None):
        super().__init__(message)
        self.field = field_name


def compile_template(pattern: str) -> re.Pattern[str]:
    """Turn a template into a compiled regex. Raises RuleError for bad templates."""
    text = pattern.strip()
    if not text:
        raise RuleError("pattern is empty", "pattern")

    scheme = _SCHEME_RE.match(text)
    if scheme and scheme.group(1).lower() in ("http", "https"):
        text = text[scheme.end():]
        prefix = r"https?://(?:www\.)?"
    elif scheme:
        text = text[scheme.end():]
        prefix = re.escape(scheme.group(0))
    else:
        prefix = r"(?:[a-z][a-z0-9+.-]*://)?(?:www\.)?"
    if text.lower().startswith("www."):
        text = text[4:]
    text = text.rstrip("/")

    parts: list[str] = []
    seen: set[str] = set()
    pos = 0
    for m in re.finditer(r"\{([^{}]*)\}", text):
        parts.append(re.escape(text[pos:m.start()]))
        name = m.group(1).strip()
        if name not in _PLACEHOLDERS:
            raise RuleError(f"unknown placeholder {{{name}}}", "pattern")
        if name not in ("*", "**"):
            if name in seen:
                raise RuleError(f"placeholder {{{name}}} used twice", "pattern")
            seen.add(name)
        parts.append(_PLACEHOLDERS[name])
        pos = m.end()
    tail = text[pos:]
    if "{" in tail or "}" in tail:
        raise RuleError("unbalanced braces in pattern", "pattern")
    parts.append(re.escape(tail))

    try:
        return re.compile("^" + prefix + "".join(parts) + _SUFFIX + "$", re.I | re.S)
    except re.error as exc:  # pragma: no cover - defensive, templates are generated
        raise RuleError(f"pattern does not compile: {exc}", "pattern") from exc


def _peer_from_groups(groups: dict[str, str | None], override: Peer | None) -> Peer | None:
    if override is not None:
        return override
    if groups.get("cid"):
        return int(f"-100{groups['cid']}")
    if groups.get("channel"):
        val = groups["channel"]
        if val.startswith("-"):
            return int(val)
        if val.isdigit():
            return int(f"-100{val}")
        return val
    if groups.get("username"):
        return groups["username"]
    if groups.get("peer"):
        val = groups["peer"]
        return int(val) if re.fullmatch(r"-?\d+", val) else val
    return None


def _rewrite_fields(template: str) -> list[str]:
    names = []
    for _, field_name, _, _ in string.Formatter().parse(template):
        if field_name:
            names.append(field_name.split(".")[0].split("[")[0])
    return names


@dataclass
class LinkRule:
    id: str
    pattern: str
    type: str = "template"
    target: str = "telegram"
    name: str = ""
    enabled: bool = True
    priority: int = 100
    channel_override: int | str | None = None
    rewrite_to: str | None = None
    tests: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    _rx: re.Pattern[str] | None = field(default=None, repr=False, compare=False)

    # ---- validation -------------------------------------------------
    def validate(self) -> list[str]:
        """Compile and check the rule. Returns warnings, raises RuleError on problems."""
        if not ID_RE.match(self.id or ""):
            raise RuleError("id must be 1-40 letters, digits, dot, dash or underscore", "id")
        if self.type not in TYPES:
            raise RuleError(f"type must be one of {', '.join(TYPES)}", "type")
        if self.target not in TARGETS:
            raise RuleError(f"target must be one of {', '.join(TARGETS)}", "target")
        if not isinstance(self.priority, int) or isinstance(self.priority, bool) or not -1000 <= self.priority <= 1000:
            raise RuleError("priority must be a whole number from -1000 to 1000", "priority")
        pattern = (self.pattern or "").strip()
        if not pattern:
            raise RuleError("pattern is empty", "pattern")
        if len(pattern) > MAX_PATTERN_LEN:
            raise RuleError(f"pattern is longer than {MAX_PATTERN_LEN} characters", "pattern")

        if self.type == "template":
            rx = compile_template(pattern)
        else:
            try:
                rx = re.compile(pattern, re.I)
            except re.error as exc:
                raise RuleError(f"regex does not compile: {exc}", "pattern") from exc

        groups = set(rx.groupindex)
        has_peer = bool(groups & set(_PEER_GROUPS)) or self.channel_override not in (None, "")
        if self.target == "telegram":
            if "msg" not in groups:
                raise RuleError("a telegram rule needs a message id: use {msg} or a (?P<msg>...) group", "pattern")
            if not has_peer:
                raise RuleError("a telegram rule needs a channel: use {channel}/{username}/{cid} or set channel_override", "pattern")
        elif self.target == "channel":
            if not has_peer:
                raise RuleError("a channel rule needs {channel}/{username}/{cid} or channel_override", "pattern")
        elif self.target == "rewrite":
            if not self.rewrite_to:
                raise RuleError("a rewrite rule needs rewrite_to", "rewrite_to")
            allowed = groups | {"url"}
            for name in _rewrite_fields(self.rewrite_to):
                if name not in allowed:
                    raise RuleError(f"rewrite_to uses {{{name}}} which the pattern does not capture", "rewrite_to")

        self._rx = rx
        warnings: list[str] = []
        if any(rx.match(sample) for sample in SAMPLE_LINKS):
            warnings.append("this rule also matches normal t.me links and will take priority over the built-in handling")
        return warnings

    def compiled(self) -> re.Pattern[str]:
        if self._rx is None:
            self.validate()
        assert self._rx is not None
        return self._rx

    # ---- matching ---------------------------------------------------
    def match(self, text: str) -> Match | None:
        if self.error:
            return None
        rx = self.compiled()
        found = rx.match(text.strip()) if self.type == "template" else rx.search(text.strip())
        if not found:
            return None
        groups = {k: v for k, v in found.groupdict().items()}
        rule_id = f"user:{self.id}"
        override = parse_channel(self.channel_override)

        if self.target == "telegram":
            peer = _peer_from_groups(groups, override)
            if peer is None or not groups.get("msg"):
                return None
            start = int(groups["msg"])
            end = int(groups.get("msg_end") or start)
            if start > end:
                start, end = end, start
            return Match("message", rule_id, peer, start, end)
        if self.target == "channel":
            peer = _peer_from_groups(groups, override)
            return Match("channel", rule_id, peer) if peer is not None else None
        if self.target == "rewrite":
            values: defaultdict[str, str] = defaultdict(str, {k: (v or "") for k, v in groups.items()})
            values["url"] = text.strip()
            try:
                url = (self.rewrite_to or "").format_map(values)
            except (KeyError, IndexError, ValueError):
                return None
            return Match("rewrite", rule_id, url=url)
        url = text.strip()
        if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
            url = "https://" + url
        return Match("external", rule_id, url=url)

    # ---- self tests -------------------------------------------------
    def run_tests(self) -> list[str]:
        """Run the embedded examples. Returns failure messages (empty list = all passed)."""
        failures: list[str] = []
        for i, case in enumerate(self.tests, start=1):
            text = str(case.get("input", ""))
            expect = case.get("expect")
            m = self.match(text)
            if m is None:
                failures.append(f"test {i}: no match for {text!r}")
                continue
            if expect is None:
                continue
            if m.kind == "message":
                got = [[m.peer, i2] for i2 in range(m.msg_start or 0, (m.msg_end or 0) + 1)]
                if got != [list(x) for x in expect]:
                    failures.append(f"test {i}: expected {expect}, got {got}")
            elif m.kind in ("rewrite", "external"):
                if m.url != expect:
                    failures.append(f"test {i}: expected {expect!r}, got {m.url!r}")
            elif m.kind == "channel":
                if m.peer != expect:
                    failures.append(f"test {i}: expected {expect!r}, got {m.peer!r}")
        return failures

    # ---- (de)serialisation -----------------------------------------
    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "priority": self.priority,
            "type": self.type,
            "pattern": self.pattern,
            "target": self.target,
        }
        if self.channel_override not in (None, ""):
            out["channel_override"] = self.channel_override
        if self.rewrite_to:
            out["rewrite_to"] = self.rewrite_to
        if self.tests:
            out["tests"] = self.tests
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LinkRule":
        prio = data.get("priority", 100)
        return cls(
            id=str(data.get("id", "")),
            pattern=str(data.get("pattern", "")),
            type=str(data.get("type", "template")),
            target=str(data.get("target", "telegram")),
            name=str(data.get("name", "")),
            enabled=bool(data.get("enabled", True)),
            priority=prio if isinstance(prio, int) and not isinstance(prio, bool) else 100,
            channel_override=data.get("channel_override"),
            rewrite_to=data.get("rewrite_to"),
            tests=[t for t in data.get("tests", []) if isinstance(t, dict)] if isinstance(data.get("tests"), list) else [],
        )


class RuleSet:
    """Ordered collection of rules plus load/save."""

    def __init__(self, rules: list[LinkRule] | None = None):
        self.rules: list[LinkRule] = list(rules or [])
        self.load_error: str | None = None

    # ---- access -----------------------------------------------------
    def get(self, rule_id: str) -> LinkRule | None:
        return next((r for r in self.rules if r.id == rule_id), None)

    def active(self) -> list[LinkRule]:
        usable = [r for r in self.rules if r.enabled and not r.error]
        return sorted(usable, key=lambda r: -r.priority)  # sorted() is stable: equal priority keeps file order

    def match(self, text: str) -> tuple[LinkRule, Match] | None:
        for rule in self.active():
            m = rule.match(text)
            if m is not None:
                return rule, m
        return None

    # ---- editing ----------------------------------------------------
    def add(self, rule: LinkRule, replace: bool = False) -> list[str]:
        """Validate, run the rule's own tests, then store it. Returns warnings."""
        existing = self.get(rule.id)
        if existing is not None and not replace:
            raise RuleError(f"a rule with id {rule.id!r} already exists", "id")
        warnings = rule.validate()
        rule.error = None
        failures = rule.run_tests()
        if failures:
            raise RuleError("; ".join(failures), "tests")
        if existing is not None:
            self.rules[self.rules.index(existing)] = rule
        else:
            self.rules.append(rule)
        return warnings

    def remove(self, rule_id: str) -> bool:
        rule = self.get(rule_id)
        if rule is None:
            return False
        self.rules.remove(rule)
        return True

    def set_enabled(self, rule_id: str, enabled: bool) -> bool:
        rule = self.get(rule_id)
        if rule is None:
            return False
        rule.enabled = enabled
        return True

    # ---- persistence ------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {"version": RULES_VERSION, "rules": [r.to_dict() for r in self.rules]}

    @classmethod
    def from_dict(cls, data: Any) -> "RuleSet":
        out = cls()
        items = data.get("rules") if isinstance(data, dict) else None
        if not isinstance(items, list):
            out.load_error = "rules file has no 'rules' list"
            return out
        seen: set[str] = set()
        for raw in items:
            if not isinstance(raw, dict):
                continue
            rule = LinkRule.from_dict(raw)
            try:
                if rule.id in seen:
                    raise RuleError(f"duplicate id {rule.id!r}", "id")
                rule.validate()
            except RuleError as exc:
                rule.error = str(exc)
            seen.add(rule.id)
            out.rules.append(rule)
        return out

    @classmethod
    def load(cls, path: Path | str) -> "RuleSet":
        path = Path(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as exc:
            try:
                os.replace(path, path.with_suffix(".json.bad"))
            except OSError:
                pass
            out = cls()
            out.load_error = f"could not read rules file ({exc}); a copy was kept as {path.name}.bad"
            return out
        return cls.from_dict(data)

    def save(self, path: Path | str) -> None:
        atomic_write_text(Path(path), json.dumps(self.to_dict(), indent=2, ensure_ascii=False))

    def export_text(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    def import_text(self, text: str, replace: bool = False) -> tuple[int, list[str]]:
        """Merge rules from exported JSON. Returns (added_count, problems)."""
        problems: list[str] = []
        try:
            incoming = RuleSet.from_dict(json.loads(text))
        except ValueError as exc:
            return 0, [f"not valid JSON: {exc}"]
        if incoming.load_error:
            return 0, [incoming.load_error]
        added = 0
        for rule in incoming.rules:
            if rule.error:
                problems.append(f"{rule.id}: {rule.error}")
                continue
            try:
                self.add(rule, replace=replace)
                added += 1
            except RuleError as exc:
                problems.append(f"{rule.id}: {exc}")
        return added, problems


def builtin_would_match(text: str) -> bool:
    return match_builtin(text) is not None
