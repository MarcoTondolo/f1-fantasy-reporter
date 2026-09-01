"""The daily digest email body: context assembly and caption formatting,
against a synthetic DailyDigest. Mirrors test_report_upgrades.py's style."""

from __future__ import annotations

from datetime import datetime, timezone

from f1_fantasy.news.digest import DailyDigest, DigestMention
from f1_fantasy.report.digest import build_digest_report, caption


def _mention(category: str, title: str, source: str = "https://example/feed") -> DigestMention:
    return DigestMention(category=category, source=source, title=title)


def test_build_digest_report_titles_include_the_round_when_present():
    digest = DailyDigest(season=2026, round_number=13, generated_at=datetime.now(timezone.utc))

    context = build_digest_report(digest)

    assert context["title"] == "F1 Fantasy daily digest -- 2026 round 13"


def test_build_digest_report_omits_the_round_when_none():
    digest = DailyDigest(season=2026, round_number=None, generated_at=datetime.now(timezone.utc))

    context = build_digest_report(digest)

    assert context["title"] == "F1 Fantasy daily digest -- 2026"


def test_build_digest_report_caps_mentions_per_category():
    many = [_mention("lineup", f"story {i}") for i in range(20)]
    digest = DailyDigest(season=2026, round_number=13, generated_at=datetime.now(timezone.utc), lineup_mentions=many)

    context = build_digest_report(digest)

    assert len(context["lineup_mentions"]) == 8


def test_caption_lists_confirmed_changes_before_rumoured_lineup_news():
    digest = DailyDigest(
        season=2026, round_number=13, generated_at=datetime.now(timezone.utc),
        confirmed_lineup_changes=["HAD not entered this round (was Red Bull)"],
        lineup_mentions=[_mention("lineup", "Tsunoda linked to a Monza seat swap")],
    )

    text = caption(build_digest_report(digest))

    assert text.index("CONFIRMED LINEUP CHANGES") < text.index("LINEUP NEWS")
    assert "HAD not entered this round" in text
    assert "Tsunoda linked to a Monza seat swap" in text


def test_caption_shows_the_watch_note_when_no_confirmed_changes():
    digest = DailyDigest(
        season=2026, round_number=13, generated_at=datetime.now(timezone.utc),
        lineup_watch_note="no session data yet for round 13",
    )

    text = caption(build_digest_report(digest))

    assert "no session data yet for round 13" in text
    assert "CONFIRMED LINEUP CHANGES" not in text


def test_caption_reports_a_quiet_day_plainly_rather_than_looking_broken():
    digest = DailyDigest(season=2026, round_number=13, generated_at=datetime.now(timezone.utc))

    text = caption(build_digest_report(digest))

    assert "quiet news day, not a fetch failure" in text


def test_caption_includes_source_for_each_mention():
    digest = DailyDigest(
        season=2026, round_number=13, generated_at=datetime.now(timezone.utc),
        upgrade_mentions=[_mention("upgrade", "Ferrari brings new floor", source="https://www.autosport.com/rss/f1/news/")],
    )

    text = caption(build_digest_report(digest))

    assert "Ferrari brings new floor" in text
    assert "https://www.autosport.com/rss/f1/news/" in text
