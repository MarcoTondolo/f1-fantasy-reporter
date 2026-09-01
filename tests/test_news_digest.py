"""Digest classification and assembly, against synthetic feed entries.

Mirrors test_news_upgrades.py's style: no network, RawEntry fixtures built
by hand, fetch_raw_entries monkeypatched for the one assembly-level test.
"""

from __future__ import annotations

from datetime import datetime, timezone

from f1_fantasy.calendar import RaceEvent
from f1_fantasy.news.digest import (
    LINEUP_KEYWORDS,
    attribute_mentions,
    build_digest,
    classify_entries,
)
from f1_fantasy.news.lineup_watch import LineupChange
from f1_fantasy.news.upgrades import RawEntry


def _entry(title: str, summary: str = "") -> RawEntry:
    return RawEntry(source="https://example/feed", title=title, summary=summary)


def test_a_lineup_keyword_in_the_title_is_classified_as_lineup():
    mentions = classify_entries([_entry("Hadjar ruled out of Monza with wrist fracture")])

    assert [m.category for m in mentions] == ["lineup"]


def test_an_upgrade_keyword_is_classified_as_upgrade():
    mentions = classify_entries([_entry("McLaren brings upgrade to Monza")])

    assert [m.category for m in mentions] == ["upgrade"]


def test_a_penalty_keyword_is_classified_as_penalty():
    mentions = classify_entries([_entry("Verstappen handed grid penalty for gearbox change")])

    categories = {m.category for m in mentions}
    assert "penalty" in categories


def test_an_unrelated_headline_produces_no_mentions():
    assert classify_entries([_entry("Norris wins from pole at Zandvoort")]) == []


def test_an_entry_matching_two_categories_appears_once_per_category():
    # Both a lineup keyword ("injury") and a penalty keyword ("investigation").
    mentions = classify_entries([_entry("Driver injury under investigation after practice crash")])

    assert sorted(m.category for m in mentions) == ["lineup", "penalty"]


def test_lineup_mention_carries_constructor_attribution():
    mentions = classify_entries([_entry("Lawson promoted to Red Bull for Monza")])

    lineup = [m for m in mentions if m.category == "lineup"][0]
    assert "Red Bull" in lineup.constructors


EARLIER = RaceEvent(season=2026, round=12, name="Earlier GP", starts_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
LATER = RaceEvent(season=2026, round=13, name="Later GP", starts_at=datetime(2026, 8, 8, tzinfo=timezone.utc))


def test_attribute_mentions_reuses_upgrades_nearest_event_logic():
    from f1_fantasy.news.digest import DigestMention

    mention = DigestMention(
        category="lineup", source="test", title="t",
        published_at=datetime(2026, 8, 7, tzinfo=timezone.utc),
    )

    attributed = attribute_mentions([mention], [EARLIER, LATER])

    assert attributed[0].attributed_round == 13


def test_lineup_keywords_are_specific_compounds_not_bare_generic_words():
    assert "seat" not in LINEUP_KEYWORDS
    assert "return" not in LINEUP_KEYWORDS


def test_build_digest_splits_categories_and_carries_confirmed_changes(monkeypatch):
    entries = [
        _entry("Hadjar ruled out of Monza with wrist fracture"),
        _entry("McLaren brings upgrade to Monza"),
        _entry("Verstappen handed grid penalty for gearbox change"),
        _entry("Norris wins from pole at Zandvoort"),
    ]
    monkeypatch.setattr("f1_fantasy.news.digest.fetch_raw_entries", lambda urls: entries)

    change = LineupChange("HAD", 13, "Red Bull", None, "absence")
    digest = build_digest(2026, 13, [EARLIER, LATER], confirmed_changes=[change])

    assert len(digest.lineup_mentions) == 1
    assert len(digest.upgrade_mentions) == 1
    assert len(digest.penalty_mentions) == 1
    assert digest.confirmed_lineup_changes == [change.headline]
    assert digest.season == 2026
    assert digest.round_number == 13


def test_build_digest_carries_the_watch_note_when_no_confirmed_changes(monkeypatch):
    monkeypatch.setattr("f1_fantasy.news.digest.fetch_raw_entries", lambda urls: [])

    digest = build_digest(2026, 13, [EARLIER, LATER], lineup_watch_note="no session data yet for round 13")

    assert digest.confirmed_lineup_changes == []
    assert digest.lineup_watch_note == "no session data yet for round 13"
