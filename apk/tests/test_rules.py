import json

import pytest

from tgdl.links.rules import LinkRule, RuleError, RuleSet, compile_template


def rule(**kw):
    base = dict(id="r1", pattern="mysite.com/{channel}/{msg}", target="telegram")
    base.update(kw)
    return LinkRule(**base)


def test_template_basic_match():
    r = rule()
    r.validate()
    m = r.match("https://mysite.com/durov/12")
    assert (m.kind, m.peer, m.msg_start, m.msg_end) == ("message", "durov", 12, 12)


def test_template_scheme_and_www_optional_case_insensitive():
    r = rule()
    for text in ("mysite.com/durov/12", "http://www.MySite.com/durov/12", "https://mysite.com/durov/12/?x=1#y"):
        assert r.match(text) is not None, text


def test_template_range_and_numeric_channel():
    r = rule()
    m = r.match("https://mysite.com/1234567890/10-12")
    assert (m.peer, m.msg_start, m.msg_end) == (-1001234567890, 10, 12)
    m = r.match("https://mysite.com/-1001234567890/10")
    assert m.peer == -1001234567890


def test_template_does_not_match_other_hosts_or_paths():
    r = rule()
    assert r.match("https://other.com/durov/12") is None
    assert r.match("https://mysite.com/durov/12/extra/deeper") is None
    assert r.match("https://notmysite.com/durov/12") is None


def test_cid_placeholder():
    r = rule(pattern="x.example/p/{cid}_{msg}")
    m = r.match("https://x.example/p/555_9")
    assert (m.peer, m.msg_start) == (-100555, 9)


def test_channel_override():
    r = rule(pattern="mirror.example/post/{msg}", channel_override="-1009999")
    m = r.match("https://mirror.example/post/77")
    assert (m.peer, m.msg_start) == (-1009999, 77)


def test_wildcards():
    r = rule(pattern="go.example/{*}/{channel}/{msg}")
    assert r.match("https://go.example/anything/chan_a/5").peer == "chan_a"
    r2 = rule(id="r2", pattern="videos.example/{**}", target="ytdlp")
    m = r2.match("videos.example/a/b/c?q=1")
    assert m.kind == "external" and m.url == "https://videos.example/a/b/c?q=1"


def test_rewrite_rule():
    r = rule(pattern="go.example/{*}/{msg}", target="rewrite", rewrite_to="https://t.me/mychan/{msg}")
    m = r.match("https://go.example/zzz/42")
    assert m.kind == "rewrite" and m.url == "https://t.me/mychan/42"


def test_regex_rule():
    r = rule(type="regex", pattern=r"^https?://x\.example/p/(?P<cid>\d+)_(?P<msg>\d+)$")
    m = r.match("https://x.example/p/12_34")
    assert (m.peer, m.msg_start) == (-10012, 34)


@pytest.mark.parametrize(
    "kw, needle",
    [
        (dict(pattern="a.com/{nope}/{msg}"), "unknown placeholder"),
        (dict(pattern="a.com/{msg}/{msg}"), "twice"),
        (dict(pattern="a.com/{channel"), "braces"),
        (dict(pattern=""), "empty"),
        (dict(pattern="a" * 301), "longer"),
        (dict(pattern="a.com/{msg}"), "channel"),                # telegram target without a channel
        (dict(pattern="a.com/{channel}"), "message id"),         # telegram target without msg
        (dict(type="regex", pattern="(unclosed"), "does not compile"),
        (dict(type="regex", pattern=r"^a\.com/(?P<msg>\d+)$"), "channel"),
        (dict(target="rewrite", pattern="a.com/{channel}/{msg}"), "rewrite_to"),
        (dict(target="rewrite", pattern="a.com/{channel}/{msg}", rewrite_to="https://t.me/{ghost}/{msg}"), "ghost"),
        (dict(id="bad id!"), "id must"),
        (dict(id=""), "id must"),
        (dict(target="nope"), "target"),
        (dict(type="nope"), "type"),
        (dict(priority=5000), "priority"),
    ],
)
def test_validation_errors(kw, needle):
    with pytest.raises(RuleError) as exc:
        rule(**kw).validate()
    assert needle in str(exc.value)


def test_shadow_warning():
    warnings = rule(pattern="t.me/{channel}/{msg}").validate()
    assert any("take priority" in w for w in warnings)
    assert rule().validate() == []


def test_embedded_tests_pass_and_fail():
    ok = rule(tests=[{"input": "https://mysite.com/durov/12-13", "expect": [["durov", 12], ["durov", 13]]}])
    ok.validate()
    assert ok.run_tests() == []

    bad = rule(tests=[{"input": "https://mysite.com/durov/12", "expect": [["durov", 99]]}])
    bad.validate()
    assert bad.run_tests()

    nomatch = rule(tests=[{"input": "https://elsewhere.com/x/1"}])
    nomatch.validate()
    assert "no match" in nomatch.run_tests()[0]


def test_ruleset_add_rejects_failing_tests_and_duplicates():
    rs = RuleSet()
    with pytest.raises(RuleError):
        rs.add(rule(tests=[{"input": "https://elsewhere.com/x/1"}]))
    rs.add(rule())
    with pytest.raises(RuleError):
        rs.add(rule())
    rs.add(rule(pattern="other.com/{channel}/{msg}"), replace=True)
    assert len(rs.rules) == 1 and "other.com" in rs.rules[0].pattern


def test_priority_order_and_disable():
    rs = RuleSet()
    rs.add(rule(id="low", pattern="a.com/{channel}/{msg}", priority=10))
    rs.add(rule(id="high", pattern="a.com/{channel}/{msg}", priority=500, channel_override=None))
    assert rs.match("https://a.com/x/1")[0].id == "high"
    rs.set_enabled("high", False)
    assert rs.match("https://a.com/x/1")[0].id == "low"
    assert rs.remove("low") and not rs.remove("low")


def test_save_load_roundtrip(tmp_path):
    rs = RuleSet()
    rs.add(rule(tests=[{"input": "https://mysite.com/durov/12", "expect": [["durov", 12]]}]))
    path = tmp_path / "rules.json"
    rs.save(path)
    loaded = RuleSet.load(path)
    assert [r.to_dict() for r in loaded.rules] == [r.to_dict() for r in rs.rules]
    assert loaded.match("https://mysite.com/durov/3") is not None


def test_load_missing_corrupt_and_invalid_rules(tmp_path):
    assert RuleSet.load(tmp_path / "none.json").rules == []

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    rs = RuleSet.load(bad)
    assert rs.rules == [] and rs.load_error
    assert (tmp_path / "bad.json.bad").exists()

    mixed = tmp_path / "mixed.json"
    mixed.write_text(json.dumps({"version": 1, "rules": [
        {"id": "good", "pattern": "a.com/{channel}/{msg}", "target": "telegram"},
        {"id": "broken", "pattern": "a.com/{ghost}", "target": "telegram"},
        {"id": "good", "pattern": "b.com/{channel}/{msg}", "target": "telegram"},
        "junk",
    ]}), encoding="utf-8")
    rs = RuleSet.load(mixed)
    assert [r.id for r in rs.rules] == ["good", "broken", "good"]
    assert rs.get("broken").error
    assert rs.rules[2].error and "duplicate" in rs.rules[2].error
    assert [r.id for r in rs.active()] == ["good"]


def test_import_export():
    rs = RuleSet()
    rs.add(rule())
    other = RuleSet()
    added, problems = other.import_text(rs.export_text())
    assert (added, problems) == (1, [])
    added, problems = other.import_text(rs.export_text())
    assert added == 0 and problems
    assert other.import_text("nope")[0] == 0


def test_compile_template_is_anchored():
    rx = compile_template("a.com/{msg}")
    assert rx.match("a.com/5")
    assert not rx.match("xa.com/5")
