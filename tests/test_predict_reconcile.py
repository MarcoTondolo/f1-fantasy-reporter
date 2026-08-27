"""Gate 1: reconstructed points must match what the game actually awarded.

Runs offline against captured payloads. The live equivalent -- all 252
driver-rounds of 2026 -- is run by ``f1-fantasy reconcile-scoring``; this
pins the specific cases that make the gate meaningful, so a regression in the
scoring table fails here rather than only against the network.
"""

from __future__ import annotations

import json

import pytest

from f1_fantasy.predict.reconcile import (
    DriverReconciliation,
    driver_of_the_day_candidates,
    fetch_constructor_feed_rows,
    fetch_driver_feed,
    summarise,
)
from f1_fantasy.predict.scoring import qualifying_points, race_points
from f1_fantasy.results import parse_qualifying, parse_race_results


def _row(driver, actual_race, expected_race, actual_q=0.0, expected_q=0.0):
    return DriverReconciliation(
        round_number=1, driver=driver,
        actual_qualifying=actual_q, expected_qualifying=expected_q,
        actual_race=actual_race, expected_race_without_overtakes=expected_race,
    )


def test_a_residual_that_is_a_non_negative_integer_reads_as_overtakes():
    assert _row("VER", 12.0, 5.0).residual_looks_like_overtakes is True
    assert _row("VER", 5.0, 5.0).residual_looks_like_overtakes is True


def test_a_negative_residual_signals_a_wrong_rule_not_an_overtake_count():
    """The failure mode this exists to catch: before the classification rule
    was corrected, Albon's round 7 came out at -16, which is not an overtake
    count -- it was an unrecognised retirement."""
    assert _row("ALB", -16.0, 0.0).residual_looks_like_overtakes is False


def test_a_fractional_residual_also_signals_a_wrong_rule():
    assert _row("NOR", 7.5, 5.0).residual_looks_like_overtakes is False


def test_driver_of_the_day_is_the_leftover_ten():
    """Exactly one driver per round exceeds their overtake points by 10."""
    rows = [_row("PIA", 16.0, 0.0), _row("NOR", 6.0, 0.0), _row("VER", 3.0, 0.0)]
    overtakes = {"PIA": 6.0, "NOR": 6.0, "VER": 3.0}

    assert driver_of_the_day_candidates(rows, overtakes) == ["PIA"]


def test_summarise_counts_matches_and_implausible_residuals():
    rows = [_row("A", 10.0, 5.0, 10.0, 10.0), _row("B", -3.0, 0.0, 9.0, 9.0)]

    summary = summarise(rows)

    assert summary["drivers"] == 2
    assert summary["qualifying_exact"] == 2
    assert summary["residual_plausible_as_overtakes"] == 1
    assert summary["residual_implausible"] == 1


# -- end-to-end on a captured round ---------------------------------------

# Trimmed from the real 2026 round 11 (Hungarian GP) payloads. Chosen because
# it exercises the rules together: a pole-to-win, a lapped-but-classified
# runner, a genuine retirement, and the fastest-lap holder.
RAW_QUALI_R11 = {
    "MRData": {"RaceTable": {"Races": [{"QualifyingResults": [
        {"position": "1", "Q1": "1:14.1", "Q2": "1:13.5", "Q3": "1:13.0",
         "Driver": {"code": "NOR", "givenName": "Lando", "familyName": "Norris"},
         "Constructor": {"name": "McLaren"}},
        {"position": "5", "Q1": "1:14.6", "Q2": "1:14.0", "Q3": "1:13.8",
         "Driver": {"code": "HAM", "givenName": "Lewis", "familyName": "Hamilton"},
         "Constructor": {"name": "Ferrari"}},
        {"position": "18", "Q1": "1:15.9", "Q2": "", "Q3": "",
         "Driver": {"code": "SAI", "givenName": "Carlos", "familyName": "Sainz"},
         "Constructor": {"name": "Williams"}},
    ]}]}}
}

RAW_RESULTS_R11 = {
    "MRData": {"RaceTable": {"Races": [{"Results": [
        {"position": "1", "grid": "1", "status": "Finished", "points": "25", "laps": "70",
         "Driver": {"code": "NOR", "givenName": "Lando", "familyName": "Norris"},
         "Constructor": {"name": "McLaren"}},
        {"position": "5", "grid": "5", "status": "Finished", "points": "10", "laps": "70",
         "FastestLap": {"rank": "1"},
         "Driver": {"code": "HAM", "givenName": "Lewis", "familyName": "Hamilton"},
         "Constructor": {"name": "Ferrari"}},
        {"position": "18", "grid": "18", "status": "Lapped", "points": "0", "laps": "68",
         "Driver": {"code": "SAI", "givenName": "Carlos", "familyName": "Sainz"},
         "Constructor": {"name": "Williams"}},
    ]}]}}
}


def test_captured_round_reconstructs_its_known_component_values():
    quali = {q.driver_code: q for q in parse_qualifying(RAW_QUALI_R11)}
    results = {r.driver_code: r for r in parse_race_results(RAW_RESULTS_R11)}
    winner_laps = max(r.laps for r in results.values())

    # Norris: pole, win, no places gained.
    assert qualifying_points(quali["NOR"].position) == 10
    nor = race_points(grid=results["NOR"].grid, position=results["NOR"].position,
                      status=results["NOR"].status, laps=results["NOR"].laps,
                      winner_laps=winner_laps)
    assert nor.position == 25
    assert nor.positions_gained == 0

    # Hamilton: P5 from P5, and holds the fastest lap.
    assert results["HAM"].fastest_lap is True
    ham = race_points(grid=5, position=5, status="Finished", fastest_lap=True,
                      laps=70, winner_laps=winner_laps)
    assert ham.position == 10
    assert ham.fastest_lap == 10

    # Sainz: two laps down but well past 90% -- classified, not a DNF.
    sai = race_points(grid=results["SAI"].grid, position=results["SAI"].position,
                      status=results["SAI"].status, laps=results["SAI"].laps,
                      winner_laps=winner_laps)
    assert sai.dnf == 0
    assert sai.total == 0


def test_a_driver_who_reached_q1_only_still_counts_as_having_set_a_time():
    quali = {q.driver_code: q for q in parse_qualifying(RAW_QUALI_R11)}

    assert quali["SAI"].set_a_time is True
    assert qualifying_points(18, set_a_time=True) == 0


def test_fetch_driver_feed_and_fetch_constructor_feed_rows_share_one_cached_payload(tmp_path):
    """Regression test for the _fetch_feed_payload extraction: both
    functions must still read the same cached file and split driver vs
    constructor rows exactly as before the refactor."""
    payload = {
        "Data": {
            "Value": [
                {"PositionName": "DRIVER", "DriverTLA": "VER", "Value": 28.0},
                {"PositionName": "CONSTRUCTOR", "FUllName": "Red Bull Racing", "Value": 30.0},
            ]
        }
    }
    cache_path = tmp_path / "ffeed_1.json"
    cache_path.write_text(json.dumps(payload), encoding="utf-8")

    drivers = fetch_driver_feed(1, cache_dir=tmp_path)
    constructors = fetch_constructor_feed_rows(1, cache_dir=tmp_path)

    assert set(drivers) == {"VER"}
    assert set(constructors) == {"Red Bull Racing"}
    assert constructors["Red Bull Racing"]["Value"] == 30.0


def test_reconstruct_points_matches_reconcile_rounds_split(monkeypatch):
    """Regression test for the reconstruct_points extraction: reconcile_round
    must still report the exact same expected_qualifying and
    expected_race_without_overtakes it did before the refactor, now that
    both are derived from one shared reconstruct_points call."""
    from f1_fantasy.predict import reconcile as reconcile_module

    monkeypatch.setattr(reconcile_module, "fetch_qualifying", lambda season, rnd: parse_qualifying(RAW_QUALI_R11))
    monkeypatch.setattr(reconcile_module, "fetch_race_results", lambda season, rnd: parse_race_results(RAW_RESULTS_R11))
    monkeypatch.setattr(
        reconcile_module,
        "fetch_driver_feed",
        lambda race_id, cache_dir=None: {
            "NOR": {"QualifyingPoints": "10", "RacePoints": "25"},
            "HAM": {"QualifyingPoints": "6", "RacePoints": "20"},
        },
    )

    breakdowns = reconcile_module.reconstruct_points(2026, 11)
    rows = reconcile_module.reconcile_round(2026, 11)
    rows_by_driver = {r.driver: r for r in rows}

    # Norris: pole (10 quali pts), win with no places gained (25 race pts, 0 gained).
    assert breakdowns["NOR"].qualifying == 10
    assert rows_by_driver["NOR"].expected_qualifying == 10
    assert rows_by_driver["NOR"].expected_race_without_overtakes == pytest.approx(
        breakdowns["NOR"].total - breakdowns["NOR"].qualifying
    )
