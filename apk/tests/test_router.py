from tgdl.links.router import extract_candidates, parse_telegram_link, route
from tgdl.links.rules import LinkRule, RuleSet


def make_rules(*rules):
    rs = RuleSet()
    for r in rules:
        rs.add(r)
    return rs


def test_extract_candidates():
    text = "see https://t.me/a_chan/1, and (https://youtu.be/abc). also 55-60 plus junk words"
    assert extract_candidates(text) == ["https://t.me/a_chan/1", "https://youtu.be/abc", "55-60"]
    assert extract_candidates("   ") == []
    assert extract_candidates("t.me/c/1/2 t.me/c/1/2") == ["t.me/c/1/2"]


def test_route_builtin_telegram():
    r = route("https://t.me/c/1234567890/456-458")
    assert r.telegram_pairs() == [(-1001234567890, 456), (-1001234567890, 457), (-1001234567890, 458)]
    assert r.telegram[0].rule_id == "builtin:private"
    assert not r.external and not r.unmatched


def test_route_channel_link():
    r = route("https://t.me/some_channel")
    assert [c.peer for c in r.channels] == ["some_channel"]
    assert r.telegram == []


def test_route_bare_ids_need_default_channel():
    r = route("123")
    assert r.unmatched == ["123"] and r.warnings
    r = route("10-12", default_channel=-1005)
    assert r.telegram_pairs() == [(-1005, 10), (-1005, 11), (-1005, 12)]


def test_route_other_sites_go_to_ytdlp():
    r = route("https://www.youtube.com/watch?v=abc https://vm.tiktok.com/ZM123/")
    assert [e.url for e in r.external] == ["https://www.youtube.com/watch?v=abc", "https://vm.tiktok.com/ZM123/"]
    assert r.external[0].rule_id == "builtin:ytdlp"


def test_route_ytdlp_disabled():
    r = route("https://www.youtube.com/watch?v=abc", ytdlp_enabled=False)
    assert r.external == [] and r.unmatched and any("yt-dlp" in w for w in r.warnings)


def test_unsupported_telegram_links_never_reach_ytdlp():
    r = route("https://t.me/+AbCdEfGh12345")
    assert r.external == [] and r.unmatched
    r = route("https://t.me/joinchat/AAAAAEabc")
    assert r.external == []


def test_range_cap():
    r = route("https://t.me/chan_x/1-5000", range_cap=100)
    assert len(r.telegram[0].msg_ids) == 100
    assert any("cut to the first 100" in w for w in r.warnings)


def test_user_rule_beats_builtin_and_ytdlp():
    rs = make_rules(
        LinkRule(id="mirror", pattern="mysite.com/{channel}/{msg}", target="telegram"),
        LinkRule(id="vids", pattern="videos.example/{**}", target="ytdlp"),
    )
    r = route("https://mysite.com/durov/9 https://videos.example/v/1 https://t.me/other_chan/3", rs)
    assert r.telegram_pairs() == [("durov", 9), ("other_chan", 3)]
    assert r.telegram[0].rule_id == "user:mirror"
    assert [e.url for e in r.external] == ["https://videos.example/v/1"]
    assert r.external[0].rule_id == "user:vids"


def test_rewrite_goes_back_through_router():
    rs = make_rules(LinkRule(id="short", pattern="go.example/{*}/{msg}", target="rewrite", rewrite_to="https://t.me/mychan/{msg}"))
    r = route("https://go.example/zzz/42", rs)
    assert r.telegram_pairs() == [("mychan", 42)]


def test_rewrite_loop_is_stopped():
    rs = make_rules(LinkRule(id="loop", pattern="loop.example/{msg}", target="rewrite", rewrite_to="https://loop.example/{msg}"))
    r = route("https://loop.example/1", rs)
    assert r.telegram == [] and r.unmatched and any("loop" in w for w in r.warnings)


def test_ytdlp_rule_with_ytdlp_off():
    rs = make_rules(LinkRule(id="vids", pattern="videos.example/{**}", target="ytdlp"))
    r = route("https://videos.example/a", rs, ytdlp_enabled=False)
    assert r.external == [] and r.unmatched


def test_scheme_less_user_rule_url_is_normalised():
    rs = make_rules(LinkRule(id="vids", pattern="videos.example/{**}", target="ytdlp"))
    r = route("videos.example/a", rs)
    assert r.external[0].url == "https://videos.example/a"


def test_compat_wrapper_ignores_user_rules_and_ytdlp():
    assert parse_telegram_link("https://youtu.be/abc") == []
    assert parse_telegram_link("https://t.me/chan_x/4") == [("chan_x", 4)]


def test_describe_lines():
    lines = route("https://t.me/chan_x/4-5 https://youtu.be/abc nonsense.").describe()
    assert any(l.startswith("telegram") for l in lines) and any(l.startswith("yt-dlp") for l in lines)
