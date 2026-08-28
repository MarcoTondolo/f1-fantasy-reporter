"""Upgrade-mention detection and attribution, offline.

Mirrors test_bulletins.py's split (keyword logic pinned without a live
feed) and test_news_incidents.py's fixture style for anything needing a
constructed RaceEvent or feed-shaped stub.
"""

from __future__ import annotations

from datetime import datetime, timezone

from f1_fantasy.calendar import RaceEvent
from f1_fantasy.news.upgrades import (
    UpgradeMention,
    _attribute_constructors,
    _matches,
    attribute_round,
    fetch_mentions,
    group_by_constructor_and_round,
)


def test_an_upgrade_keyword_in_the_title_matches():
    assert "upgrade" in _matches("McLaren brings upgrade to Monza")


def test_matching_is_case_insensitive():
    assert "sidepod" in _matches("New SIDEPOD spotted on the Ferrari")


def test_an_unrelated_headline_matches_nothing():
    assert _matches("Norris wins from pole at Zandvoort") == ()


def test_bare_development_and_specification_do_not_match_alone():
    """Deliberate precision-over-recall exclusion: bare "development" and
    "specification" match too much unrelated F1 news to be useful alone."""
    assert _matches("McLaren's junior driver development programme expands") == ()
    assert _matches("FIA confirms 2027 tyre specification") == ()


def test_compound_bring_and_introduce_phrases_do_match():
    assert "bring an upgrade" in _matches("Ferrari to bring an upgrade to Monza")
    assert "introduces a new" in _matches("Red Bull introduces a new floor")


def test_attribute_constructors_finds_red_bull_not_racing_bulls_from_red_bull_text():
    assert _attribute_constructors("Red Bull unveils new floor") == ("Red Bull",)


def test_attribute_constructors_finds_racing_bulls_not_red_bull_from_racing_bulls_text():
    assert _attribute_constructors("Racing Bulls bring aero upgrade") == ("Racing Bulls",)


def test_attribute_constructors_handles_multiple_teams_in_one_story():
    found = _attribute_constructors("Who's upgrading this weekend? McLaren, Ferrari, Mercedes and Alpine all bring new parts")
    assert set(found) == {"McLaren", "Ferrari", "Mercedes", "Alpine"}


def test_attribute_constructors_matches_sauber_as_audi_after_the_rebrand():
    assert _attribute_constructors("Sauber's new floor impresses in practice") == ("Audi",)


EARLIER = RaceEvent(season=2026, round=9, name="Earlier GP", starts_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
LATER = RaceEvent(season=2026, round=10, name="Later GP", starts_at=datetime(2026, 8, 8, tzinfo=timezone.utc))


def test_attribute_round_picks_the_nearest_event_by_date_not_floor_or_ceiling():
    # Published slightly closer to LATER than to EARLIER.
    mention = UpgradeMention(
        source="test", title="t", published_at=datetime(2026, 8, 5, tzinfo=timezone.utc)
    )

    assert attribute_round(mention, [EARLIER, LATER]) == 10


def test_attribute_round_picks_the_earlier_event_when_closer():
    mention = UpgradeMention(
        source="test", title="t", published_at=datetime(2026, 8, 2, tzinfo=timezone.utc)
    )

    assert attribute_round(mention, [EARLIER, LATER]) == 9


def test_attribute_round_is_none_with_no_publish_date():
    mention = UpgradeMention(source="test", title="t", published_at=None)

    assert attribute_round(mention, [EARLIER, LATER]) is None


def test_group_by_constructor_and_round_keeps_every_corroborating_mention():
    mentions = [
        UpgradeMention(source="a", title="1", constructors=("McLaren",), attributed_round=9),
        UpgradeMention(source="b", title="2", constructors=("McLaren",), attributed_round=9),
        UpgradeMention(source="c", title="3", constructors=("McLaren",), attributed_round=9),
    ]

    groups = group_by_constructor_and_round(mentions)

    assert len(groups[("McLaren", 9)]) == 3


def test_group_by_constructor_and_round_excludes_unattributed_mentions():
    mentions = [
        UpgradeMention(source="a", title="1", constructors=(), attributed_round=9),
        UpgradeMention(source="b", title="2", constructors=("McLaren",), attributed_round=None),
    ]

    groups = group_by_constructor_and_round(mentions)

    assert groups == {}


def test_fetch_mentions_skips_an_unreachable_source_without_failing_the_rest(monkeypatch):
    import urllib.error

    real_urlopen = __import__("urllib.request", fromlist=["urlopen"]).urlopen

    good_feed = (
        b"<?xml version='1.0'?><rss><channel>"
        b"<item><title>McLaren brings upgrade to Monza</title><description></description></item>"
        b"</channel></rss>"
    )

    class FakeResponse:
        def __init__(self, data):
            self._data = data

        def read(self):
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(request, timeout=20.0):
        if "bad" in request.full_url:
            raise urllib.error.URLError("blocked")
        return FakeResponse(good_feed)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    mentions = fetch_mentions(feed_urls=("https://bad.example/feed", "https://good.example/feed"))

    assert len(mentions) == 1
    assert mentions[0].title == "McLaren brings upgrade to Monza"
