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
    """positionText "R" is not a rank -- must not be misread as int(R)."""
    (retired,) = [r for r in parse_race_results(RAW_RESULTS) if r.driver_code == "ALB"]

    assert retired.position is None
    assert not retired.finished
    assert retired.grid == 10


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
