"""The upgrade tracker card's context assembly, against synthetic mentions/effects."""

from __future__ import annotations

from datetime import datetime, timezone

from f1_fantasy.news.upgrades import UpgradeMention
from f1_fantasy.predict.upgrades import UpgradeEffect
from f1_fantasy.report.upgrades import build_upgrades, caption


def _effect(constructor, relative_delta, n_before=3, n_after=2) -> UpgradeEffect:
    return UpgradeEffect(
        constructor=constructor, upgrade_round=9,
        before_rounds=(6, 7, 8), after_rounds=(9, 10),
        constructor_before_mean=0.5, constructor_after_mean=0.3,
        field_before_mean=0.6, field_after_mean=0.5,
        constructor_delta=-0.2, field_delta=-0.1, relative_delta=relative_delta,
        n_before=n_before, n_after=n_after,
    )


def test_build_upgrades_sorts_effects_by_absolute_relative_delta_descending():
    effects = [_effect("A", relative_delta=-0.05), _effect("B", relative_delta=0.4), _effect("C", relative_delta=-0.1)]

    context = build_upgrades([], effects, season=2026)

    assert [row["constructor"] for row in context["measured_effects"]] == ["B", "C", "A"]


def test_build_upgrades_labels_a_negative_relative_delta_as_improved():
    effects = [_effect("A", relative_delta=-0.3)]

    context = build_upgrades([], effects, season=2026)

    assert context["measured_effects"][0]["verdict"] == "improved vs field"


def test_build_upgrades_labels_a_positive_relative_delta_as_behind_the_field():
    effects = [_effect("A", relative_delta=0.2)]

    context = build_upgrades([], effects, season=2026)

    assert context["measured_effects"][0]["verdict"] == "behind the field's own gain"


def test_build_upgrades_sorts_mentions_by_publish_date_most_recent_first():
    older = UpgradeMention(source="a", title="older", published_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
    newer = UpgradeMention(source="b", title="newer", published_at=datetime(2026, 8, 10, tzinfo=timezone.utc))

    context = build_upgrades([older, newer], [], season=2026)

    assert [row["title"] for row in context["recent_mentions"]] == ["newer", "older"]


def test_build_upgrades_handles_a_mention_with_no_publish_date():
    undated = UpgradeMention(source="a", title="undated", published_at=None)

    context = build_upgrades([undated], [], season=2026)

    assert context["recent_mentions"][0]["title"] == "undated"
    assert context["recent_mentions"][0]["published_at"] == ""


def test_build_upgrades_counts_unattributed_mentions_separately():
    attributed = UpgradeMention(source="a", title="a", constructors=("McLaren",))
    unattributed = UpgradeMention(source="b", title="b", constructors=())

    context = build_upgrades([attributed, unattributed], [], season=2026)

    assert context["unattributed_count"] == 1


def test_build_upgrades_source_name_strips_the_www_prefix():
    mention = UpgradeMention(source="https://www.autosport.com/rss/f1/news/", title="t")

    context = build_upgrades([mention], [], season=2026)

    assert context["recent_mentions"][0]["source"] == "autosport.com"


def test_build_upgrades_caveat_notes_when_nothing_has_been_measured_yet():
    context = build_upgrades([], [], season=2026)

    assert "No upgrade effects measured yet" in context["caveat"]


def test_caption_includes_constructor_and_round_for_a_measured_effect():
    effects = [_effect("Ferrari", relative_delta=-0.15)]

    text = caption(build_upgrades([], effects, season=2026))

    assert "Ferrari R9" in text
