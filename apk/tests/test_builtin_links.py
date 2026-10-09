import pytest

from tests.legacy_parse import parse_telegram_link as legacy
from tgdl.links.router import parse_telegram_link as new

# Every format the original script understood must still give the same answer.
LEGACY_CORPUS = [
    "https://t.me/c/1234567890/456",
    "http://t.me/c/1234567890/456",
    "t.me/c/1234567890/456",
    "https://telegram.me/c/1234567890/456",
    "https://t.me/c/1234567890/456-460",
    "https://t.me/c/1234567890/460-456",
    "https://t.me/channel_name/789",
    "https://t.me/channel_name/789-792",
    "https://t.me/channel_name/792-789",
    "https://telegram.me/channel_name/5",
    "t.me/Channel_Name/5",
    "https://web.telegram.org/k/#-1001234567890/456",
    "https://web.telegram.org/a/#-1001234567890/456",
    "https://web.telegram.org/k/#-1001234567890/456-459",
    "https://web.telegram.org/z/#-12345/6",
    "https://t.me/c/1234567890/456?single",
    "https://t.me/channel_name/789?single",
]

LEGACY_WITH_DEFAULT = ["456", "456-460", "460-456", "7"]


@pytest.mark.parametrize("link", LEGACY_CORPUS)
def test_same_as_legacy(link):
    assert new(link) == legacy(link)


@pytest.mark.parametrize("link", LEGACY_WITH_DEFAULT)
def test_same_as_legacy_with_default_channel(link):
    assert new(link, -1001111) == legacy(link, -1001111)
    assert new(link, "mychan") == legacy(link, "mychan")


@pytest.mark.parametrize("link", ["456", "456-460", "garbage", "", "https://example.com/x/1"])
def test_nothing_without_default_channel(link):
    assert new(link) == legacy(link) == []


def test_new_forum_topic_private():
    # The old parser read the topic number as the message id (bug). Message is the last number.
    assert new("https://t.me/c/1234567890/12/456") == [(-1001234567890, 456)]
    assert legacy("https://t.me/c/1234567890/12/456") == [(-1001234567890, 12)]


def test_new_forum_topic_public_and_range():
    assert new("https://t.me/somegroup/3/100") == [("somegroup", 100)]
    assert new("https://t.me/somegroup/3/100-102") == [("somegroup", 100), ("somegroup", 101), ("somegroup", 102)]


def test_preview_and_domains():
    assert new("https://t.me/s/channel_name/10") == [("channel_name", 10)]
    assert new("https://telegram.dog/channel_name/10") == [("channel_name", 10)]


def test_query_strings_ignored():
    assert new("https://t.me/channel_name/10?comment=77") == [("channel_name", 10)]
    assert new("https://t.me/c/1234567890/10?thread=3") == [(-1001234567890, 10)]


def test_tg_scheme():
    assert new("tg://privatepost?channel=1234567890&post=45") == [(-1001234567890, 45)]
    assert new("tg://resolve?domain=durov&post=45") == [("durov", 45)]
    assert new("tg://resolve?domain=durov") == []


def test_multiple_links_in_one_paste():
    text = "https://t.me/a_channel/1, https://t.me/c/99/2 https://t.me/a_channel/5-6"
    assert new(text) == [("a_channel", 1), (-10099, 2), ("a_channel", 5), ("a_channel", 6)]


def test_reserved_paths_are_not_channels():
    assert new("https://t.me/joinchat/AAAA1111/5") == []
    assert new("https://t.me/c/123") == []


def test_text_around_link():
    assert new("look at this (https://t.me/chan_x/42).") == [("chan_x", 42)]
