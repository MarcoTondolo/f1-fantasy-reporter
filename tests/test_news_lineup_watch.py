"""Lineup-change detection, against synthetic driver->constructor mappings.

detect_lineup_changes is deliberately source-agnostic (works the same for
two qualifying sessions, two practice sessions, or a hand-entered mapping),
so these tests exercise it directly without touching FastF1 or Jolpica.
"""

from __future__ import annotations

from f1_fantasy.news.lineup_watch import LineupChange, detect_lineup_changes


def test_detect_lineup_changes_flags_a_team_change():
    before = {"HAD": "Red Bull", "VER": "Red Bull"}
    after = {"HAD": "RB F1 Team", "VER": "Red Bull"}

    changes = detect_lineup_changes(13, before, after)

    assert changes == [LineupChange("HAD", 13, "Red Bull", "RB F1 Team", "team_change")]


def test_detect_lineup_changes_flags_an_absence():
    before = {"HAD": "Red Bull", "VER": "Red Bull"}
    after = {"VER": "Red Bull"}

    changes = detect_lineup_changes(13, before, after)

    assert changes == [LineupChange("HAD", 13, "Red Bull", None, "absence")]


def test_detect_lineup_changes_flags_a_new_entrant():
    before = {"VER": "Red Bull"}
    after = {"VER": "Red Bull", "TSU": "RB F1 Team"}

    changes = detect_lineup_changes(13, before, after)

    assert changes == [LineupChange("TSU", 13, None, "RB F1 Team", "new_entrant")]


def test_detect_lineup_changes_reports_nothing_when_mappings_match():
    mapping = {"VER": "Red Bull", "HAD": "Red Bull"}

    assert detect_lineup_changes(13, mapping, dict(mapping)) == []


def test_detect_lineup_changes_handles_multiple_simultaneous_changes():
    before = {"HAD": "Red Bull", "LAW": "RB F1 Team", "VER": "Red Bull"}
    after = {"LAW": "Red Bull", "TSU": "RB F1 Team", "VER": "Red Bull"}

    changes = detect_lineup_changes(13, before, after)

    by_driver = {c.driver: c for c in changes}
    assert by_driver["HAD"].change_type == "absence"
    assert by_driver["LAW"].change_type == "team_change"
    assert by_driver["LAW"].new_constructor == "Red Bull"
    assert by_driver["TSU"].change_type == "new_entrant"


def test_headline_describes_a_team_change():
    change = LineupChange("LAW", 13, "RB F1 Team", "Red Bull", "team_change")

    assert change.headline == "LAW moved from RB F1 Team to Red Bull"


def test_headline_describes_an_absence():
    change = LineupChange("HAD", 13, "Red Bull", None, "absence")

    assert "not entered" in change.headline
    assert "Red Bull" in change.headline


def test_headline_describes_a_new_entrant():
    change = LineupChange("TSU", 13, None, "RB F1 Team", "new_entrant")

    assert "newly entered" in change.headline
    assert "RB F1 Team" in change.headline


def test_watch_round_diffs_two_real_looking_rounds(monkeypatch):
    from f1_fantasy.news import lineup_watch as lineup_watch_module
    from f1_fantasy.results import QualifyingResult

    def fake_fetch_qualifying(season, round_number):
        if round_number == 12:
            return [
                QualifyingResult(driver_code="VER", driver_name="V", constructor="Red Bull", position=1),
                QualifyingResult(driver_code="LAW", driver_name="L", constructor="Red Bull", position=2),
            ]
        return [
            QualifyingResult(driver_code="VER", driver_name="V", constructor="Red Bull", position=1),
            QualifyingResult(driver_code="HAD", driver_name="H", constructor="Red Bull", position=3),
        ]

    monkeypatch.setattr(lineup_watch_module, "fetch_qualifying", fake_fetch_qualifying)

    changes = lineup_watch_module.watch_round(2026, 12, 13)

    by_driver = {c.driver: c for c in changes}
    assert by_driver["LAW"].change_type == "absence"
    assert by_driver["HAD"].change_type == "new_entrant"
