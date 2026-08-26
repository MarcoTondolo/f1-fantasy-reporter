"""Grid-penalty detection: the deterministic half of the news bulletin.

Fixtures are trimmed real shapes from Jolpica's qualifying.json/results.json
(confirmed live 2026-08-25 against several 2026 rounds) rather than invented
field names, same policy as the fantasy API fixtures.
"""

from __future__ import annotations

from f1_fantasy.results import (
    detect_grid_penalties,
    parse_qualifying,
    parse_race_results,
)

RAW_QUALIFYING = {
    "MRData": {
        "RaceTable": {
            "Races": [
                {
                    "QualifyingResults": [
                        {
                            "position": "1",
                            "Driver": {"code": "NOR", "givenName": "Lando", "familyName": "Norris"},
                            "Constructor": {"name": "McLaren"},
                        },
                        {
                            "position": "2",
                            "Driver": {"code": "VER", "givenName": "Max", "familyName": "Verstappen"},
                            "Constructor": {"name": "Red Bull"},
                        },
                        {
                            "position": "3",
                            "Driver": {"code": "HAD", "givenName": "Isack", "familyName": "Hadjar"},
                            "Constructor": {"name": "Racing Bulls"},
                        },
                    ]
                }
            ]
        }
    }
}

RAW_RESULTS = {
    "MRData": {
        "RaceTable": {
            "Races": [
                {
                    "Results": [
                        {
                            "position": "1",
                            "grid": "1",
                            "status": "Finished",
                            "points": "25",
                            "Driver": {"code": "NOR", "givenName": "Lando", "familyName": "Norris"},
                            "Constructor": {"name": "McLaren"},
                        },
                        {
                            "position": "2",
                            "grid": "2",
                            "status": "Finished",
                            "points": "18",
                            "Driver": {"code": "VER", "givenName": "Max", "familyName": "Verstappen"},
                            "Constructor": {"name": "Red Bull"},
                        },
                        # Modelled on a real case (round 10, 2026): a big ICE
                        # penalty drops a driver many places, not noise.
                        {
                            "position": "13",
                            "grid": "13",
                            "status": "Finished",
                            "points": "0",
                            "Driver": {"code": "HAD", "givenName": "Isack", "familyName": "Hadjar"},
                            "Constructor": {"name": "Racing Bulls"},
                        },
                        {
                            "position": "R",
                            "grid": "10",
                            "status": "Retired",
                            "points": "0",
                            "Driver": {"code": "ALB", "givenName": "Alex", "familyName": "Albon"},
                            "Constructor": {"name": "Williams"},
                        },
                    ]
                }
            ]
        }
    }
}


def test_qualifying_parses_position_and_driver_code():
    results = parse_qualifying(RAW_QUALIFYING)

    assert [r.position for r in results] == [1, 2, 3]
    assert results[0].driver_code == "NOR"
    assert results[0].driver_name == "Lando Norris"


def test_race_results_parse_grid_and_classified_position():
    results = parse_race_results(RAW_RESULTS)

    assert results[0].grid == 1
    assert results[0].position == 1


def test_a_retirement_has_no_classified_position_but_keeps_its_grid_slot():
    """positionText "R" is not a rank -- must not be misread as int(R).

    Defensive only: this feed does not actually emit a non-numeric position
    (see RAW_RESULTS_REAL_SHAPE below), but Ergast historically did.
    """
    (retired,) = [r for r in parse_race_results(RAW_RESULTS) if r.driver_code == "ALB"]

    assert retired.position is None
    assert not retired.finished
    assert retired.grid == 10


# The shape the live feed actually returns, captured from 2026 round 12
# (Dutch GP). Every row carries a numeric position -- including the six
# retirements, which are classified 17th-22nd. A "finished" check keyed on
# `position is not None` therefore reports True for all of them, which is the
# bug this fixture exists to prevent recurring. Note "Lapped": a driver a lap
# down IS classified, and must not be counted as a retirement.
RAW_RESULTS_REAL_SHAPE = {
    "MRData": {
        "RaceTable": {
            "Races": [
                {
                    "Results": [
                        {
                            "position": "1", "grid": "1", "status": "Finished", "points": "25",
                            "Driver": {"code": "NOR", "givenName": "Lando", "familyName": "Norris"},
                            "Constructor": {"name": "McLaren"},
                        },
                        {
                            "position": "11", "grid": "12", "status": "Lapped", "points": "0",
                            "Driver": {"code": "TSU", "givenName": "Yuki", "familyName": "Tsunoda"},
                            "Constructor": {"name": "Racing Bulls"},
                        },
                        {
                            "position": "17", "grid": "9", "status": "Retired", "points": "0",
                            "Driver": {"code": "ALB", "givenName": "Alex", "familyName": "Albon"},
                            "Constructor": {"name": "Williams"},
                        },
                        {
                            "position": "22", "grid": "7", "status": "Retired", "points": "0",
                            "Driver": {"code": "VER", "givenName": "Max", "familyName": "Verstappen"},
                            "Constructor": {"name": "Red Bull"},
                        },
                        {
                            "position": "20", "grid": "18", "status": "Did not start", "points": "0",
                            "Driver": {"code": "STR", "givenName": "Lance", "familyName": "Stroll"},
                            "Constructor": {"name": "Aston Martin"},
                        },
                    ]
                }
            ]
        }
    }
}


def test_a_retirement_with_a_numeric_position_is_still_not_a_finish():
    """The regression that matters: this feed classifies retirements with a
    real position, so `position is not None` cannot detect a DNF."""
    by_code = {r.driver_code: r for r in parse_race_results(RAW_RESULTS_REAL_SHAPE)}

    assert by_code["VER"].position == 22  # a genuine classification position
    assert by_code["VER"].finished is False
    assert by_code["ALB"].position == 17
    assert by_code["ALB"].finished is False


def test_a_lapped_driver_is_a_classified_finisher():
    """"Lapped" is this feed's equivalent of Ergast's "+1 Lap" -- classified,
    not a retirement. Counting it as a DNF would overstate the DNF rate."""
    by_code = {r.driver_code: r for r in parse_race_results(RAW_RESULTS_REAL_SHAPE)}

    assert by_code["TSU"].status == "Lapped"
    assert by_code["TSU"].finished is True


def test_did_not_start_is_not_a_finish():
    by_code = {r.driver_code: r for r in parse_race_results(RAW_RESULTS_REAL_SHAPE)}

    assert by_code["STR"].finished is False


def test_only_the_winner_and_the_lapped_runner_count_as_classified():
    results = parse_race_results(RAW_RESULTS_REAL_SHAPE)

    assert sorted(r.driver_code for r in results if r.finished) == ["NOR", "TSU"]


def test_a_big_grid_drop_is_flagged_as_a_penalty():
    quali = parse_qualifying(RAW_QUALIFYING)
    race = parse_race_results(RAW_RESULTS)

    penalties = detect_grid_penalties(quali, race)

    assert len(penalties) == 1
    assert penalties[0].driver_code == "HAD"
    assert penalties[0].quali_position == 3
    assert penalties[0].grid_position == 13
    assert penalties[0].places_lost == 10


def test_an_ordinary_one_place_reshuffle_is_not_a_penalty():
    """NOR and VER kept their exact quali order -- no false positives."""
    quali = parse_qualifying(RAW_QUALIFYING)
    race = parse_race_results(RAW_RESULTS)

    penalties = detect_grid_penalties(quali, race)

    assert all(p.driver_code not in {"NOR", "VER"} for p in penalties)


def test_threshold_is_configurable():
    quali = parse_qualifying(RAW_QUALIFYING)
    race = parse_race_results(RAW_RESULTS)

    # Nothing in the fixture drops 20+ places.
    assert detect_grid_penalties(quali, race, threshold=20) == []
