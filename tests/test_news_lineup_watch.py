"""Lineup-change detection, against synthetic driver->constructor mappings.

detect_lineup_changes is deliberately source-agnostic (works the same for
two qualifying sessions, two practice sessions, or a hand-entered mapping),
so most of these tests exercise it directly without touching FastF1 or
Jolpica. constructor_of_from_practice's own regression test below is the
one exception -- it needs a fake FastF1 session to reproduce a real
failure mode.
"""

from __future__ import annotations

import pytest

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


def _fake_laps(rows: list[tuple[str, str, int]]):
    """Builds a real fastf1.core.Laps (not a plain DataFrame) so
    constructor_of_from_practice's use of .pick_drivers() works unmodified.
    Each row is (driver_code, team, n_laps); n_laps duplicate rows are
    emitted per driver to drive the lap-count-based cap under test.
    """
    import pandas as pd
    from fastf1.core import Laps

    records = [
        {"Driver": driver, "Team": team, "DriverNumber": str(i)}
        for i, (driver, team, n_laps) in enumerate(rows)
        for _ in range(n_laps)
    ]
    return Laps(pd.DataFrame(records))


def test_constructor_of_from_practice_caps_a_third_driver_by_lap_count(monkeypatch):
    """Regression test for the spurious new_entrant noise confirmed live
    at Monza R13 FP1: HER/Cadillac, IWA/Red Bull, BRO/Williams and
    ARO/Alpine were all young-driver-rule stand-ins for a single session,
    each logging far fewer laps than the regular driver they replaced for
    that session. constructor_of_from_practice should keep only the two
    highest-lap-count drivers per constructor, dropping the stand-in."""
    import fastf1

    from f1_fantasy.news import lineup_watch as lineup_watch_module

    laps = _fake_laps(
        [
            ("VER", "Red Bull", 20),
            ("HAD", "Red Bull", 18),
            ("IWA", "Red Bull", 3),  # young-driver-rule stand-in, one session only
            ("LEC", "Ferrari", 19),
            ("HAM", "Ferrari", 21),
        ]
    )

    class FakeSession:
        def load(self, **kwargs):
            return None

        @property
        def laps(self):
            return laps

    monkeypatch.setattr(lineup_watch_module, "_ensure_cache", lambda: None)
    monkeypatch.setattr(fastf1, "get_session", lambda season, rnd, session: FakeSession())

    mapping = lineup_watch_module.constructor_of_from_practice(2026, 13, "FP1")

    assert mapping == {"VER": "Red Bull", "HAD": "Red Bull", "LEC": "Ferrari", "HAM": "Ferrari"}
    assert "IWA" not in mapping


def test_constructor_of_from_practice_capped_mapping_does_not_produce_spurious_new_entrant(monkeypatch):
    """End-to-end version of the regression above: diffing the capped
    practice mapping against the prior round's real two-driver-per-team
    qualifying roster must not flag the low-lap-count third driver
    (IWA) as a new_entrant, while still surfacing genuine changes."""
    import fastf1

    from f1_fantasy.news import lineup_watch as lineup_watch_module

    laps = _fake_laps(
        [
            ("VER", "Red Bull", 20),
            ("HAD", "Red Bull", 18),
            ("IWA", "Red Bull", 3),
        ]
    )

    class FakeSession:
        def load(self, **kwargs):
            return None

        @property
        def laps(self):
            return laps

    monkeypatch.setattr(lineup_watch_module, "_ensure_cache", lambda: None)
    monkeypatch.setattr(fastf1, "get_session", lambda season, rnd, session: FakeSession())

    before = {"VER": "Red Bull", "HAD": "Red Bull"}
    after = lineup_watch_module.constructor_of_from_practice(2026, 13, "FP1")

    changes = detect_lineup_changes(13, before, after)

    assert changes == []


def test_detect_lineup_changes_still_flags_real_swaps_against_a_capped_practice_mapping(monkeypatch):
    """The feature this whole module exists for must survive the cap:
    genuine team_change and absence pairs (e.g. LAW/TSU/LIN moving
    between Red Bull and Racing Bulls, HAD sitting out) still come
    through once a constructor's practice mapping is capped to two."""
    import fastf1

    from f1_fantasy.news import lineup_watch as lineup_watch_module

    laps = _fake_laps(
        [
            ("VER", "Red Bull", 20),
            ("LAW", "Red Bull", 19),  # moved from Racing Bulls
            ("TSU", "Racing Bulls", 18),  # moved from Red Bull
            ("LIN", "Racing Bulls", 17),
            ("IWA", "Red Bull", 3),  # young-driver-rule stand-in, ignored
            # HAD is simply absent from this round's practice entry list
        ]
    )

    class FakeSession:
        def load(self, **kwargs):
            return None

        @property
        def laps(self):
            return laps

    monkeypatch.setattr(lineup_watch_module, "_ensure_cache", lambda: None)
    monkeypatch.setattr(fastf1, "get_session", lambda season, rnd, session: FakeSession())

    before = {
        "VER": "Red Bull",
        "HAD": "Red Bull",
        "TSU": "Red Bull",
        "LIN": "Racing Bulls",
    }
    after = lineup_watch_module.constructor_of_from_practice(2026, 13, "FP1")

    changes = detect_lineup_changes(13, before, after)

    by_driver = {c.driver: c for c in changes}
    assert by_driver["HAD"].change_type == "absence"
    assert by_driver["TSU"].change_type == "team_change"
    assert by_driver["TSU"].new_constructor == "Racing Bulls"
    assert by_driver["LAW"].change_type == "new_entrant"
    assert by_driver["LAW"].new_constructor == "Red Bull"
    assert "IWA" not in by_driver


def test_constructor_of_from_practice_converts_a_post_load_failure_to_session_unavailable(monkeypatch):
    """Regression test for a real failure hit live in GitHub Actions
    (round 13 FP1, requested before FastF1's live-timing mirror had the
    data): ``session.load()`` returned without raising -- FastF1 swallows
    its own per-category SessionNotAvailableError internally and just
    logs a warning -- but the subsequent ``.laps`` property access raised
    fastf1.exceptions.DataNotLoadedError, which used to propagate
    uncaught out of constructor_of_from_practice and crash the caller
    instead of degrading to SessionUnavailable like every other
    "session isn't ready yet" case."""
    import fastf1
    from fastf1.exceptions import DataNotLoadedError

    from f1_fantasy.news import lineup_watch as lineup_watch_module

    class FakeSession:
        def load(self, **kwargs):
            return None  # "succeeds" without actually populating _laps

        @property
        def laps(self):
            raise DataNotLoadedError("laps data has not been loaded yet")

    monkeypatch.setattr(lineup_watch_module, "_ensure_cache", lambda: None)
    monkeypatch.setattr(fastf1, "get_session", lambda season, rnd, session: FakeSession())

    with pytest.raises(lineup_watch_module.SessionUnavailable):
        lineup_watch_module.constructor_of_from_practice(2026, 13, "FP1")
