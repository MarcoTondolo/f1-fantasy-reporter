"""Incident-adjusted reliability, against synthetic incidents and results.

Mirrors test_predict_reliability.py's fixture style for the history test,
and test_predict_race.py's monkeypatching style for the walk-forward smoke
test.
"""

from __future__ import annotations

from f1_fantasy.news.incidents import DriverIncidentSummary
from f1_fantasy.predict import adjusted as adjusted_module
from f1_fantasy.results import RaceResult

WINNER = RaceResult(driver_code="WINNER", driver_name="Winner", constructor="McLaren", grid=1, position=1, status="Finished", laps=58)


def test_adjusted_history_excludes_a_high_confidence_collision_dnf(monkeypatch):
    """Stroll retires in a collision at confidence 0.9 (above the 0.6
    default threshold) -- excluded entirely, neither a race nor a DNF."""
    stroll = RaceResult(driver_code="STR", driver_name="Stroll", constructor="Aston Martin", grid=10, position=17, status="Retired", laps=20)

    monkeypatch.setattr(adjusted_module, "fetch_race_results", lambda season, rnd: [WINNER, stroll])
    monkeypatch.setattr(
        adjusted_module,
        "round_incidents",
        lambda season, rnd: {
            "STR": DriverIncidentSummary(
                driver_code="STR", round_number=rnd, penalties=0, investigations=1, no_further_action=0,
                safety_car_laps=0, red_flag_laps=0, retirement_cause="collision", retirement_confidence=0.9,
            )
        },
    )

    history = adjusted_module.adjusted_constructor_history(2026, [1])

    assert "Aston Martin" not in history


def test_adjusted_history_still_counts_a_low_confidence_collision_dnf(monkeypatch):
    """Same collision call, but confidence 0.4 is below the threshold --
    counted exactly as reliability.constructor_history would."""
    stroll = RaceResult(driver_code="STR", driver_name="Stroll", constructor="Aston Martin", grid=10, position=17, status="Retired", laps=20)

    monkeypatch.setattr(adjusted_module, "fetch_race_results", lambda season, rnd: [WINNER, stroll])
    monkeypatch.setattr(
        adjusted_module,
        "round_incidents",
        lambda season, rnd: {
            "STR": DriverIncidentSummary(
                driver_code="STR", round_number=rnd, penalties=0, investigations=1, no_further_action=0,
                safety_car_laps=0, red_flag_laps=0, retirement_cause="collision", retirement_confidence=0.4,
            )
        },
    )

    history = adjusted_module.adjusted_constructor_history(2026, [1])

    assert history["Aston Martin"].races == 1
    assert history["Aston Martin"].dnfs == 1


def test_adjusted_history_still_counts_a_mechanical_dnf_regardless_of_confidence(monkeypatch):
    """Only "collision" is ever excluded -- a high-confidence mechanical
    classification is still a real reliability event."""
    gasly = RaceResult(driver_code="GAS", driver_name="Gasly", constructor="Alpine", grid=12, position=18, status="Retired", laps=30)

    monkeypatch.setattr(adjusted_module, "fetch_race_results", lambda season, rnd: [WINNER, gasly])
    monkeypatch.setattr(
        adjusted_module,
        "round_incidents",
        lambda season, rnd: {
            "GAS": DriverIncidentSummary(
                driver_code="GAS", round_number=rnd, penalties=0, investigations=0, no_further_action=0,
                safety_car_laps=0, red_flag_laps=0, retirement_cause="mechanical", retirement_confidence=0.95,
            )
        },
    )

    history = adjusted_module.adjusted_constructor_history(2026, [1])

    assert history["Alpine"].races == 1
    assert history["Alpine"].dnfs == 1


def test_adjusted_history_counts_a_dnf_with_no_incident_summary_normally(monkeypatch):
    """No matching incident summary at all (the common real case) -- counted
    exactly as reliability.constructor_history would, unaffected."""
    stroll = RaceResult(driver_code="STR", driver_name="Stroll", constructor="Aston Martin", grid=10, position=17, status="Retired", laps=20)

    monkeypatch.setattr(adjusted_module, "fetch_race_results", lambda season, rnd: [WINNER, stroll])
    monkeypatch.setattr(adjusted_module, "round_incidents", lambda season, rnd: {})

    history = adjusted_module.adjusted_constructor_history(2026, [1])

    assert history["Aston Martin"].dnfs == 1


def test_predict_race_order_adjusted_never_trains_on_the_target_round(monkeypatch):
    """A walk-forward smoke test, mirroring test_predict_race.py: the
    predictor must only ever be handed train_rounds strictly before the
    target, and must return a score per driver."""
    from f1_fantasy.results import QualifyingResult

    monkeypatch.setattr(
        adjusted_module,
        "fetch_qualifying",
        lambda season, rnd: [
            QualifyingResult(driver_code="A", driver_name="A", constructor="Team", position=1),
            QualifyingResult(driver_code="B", driver_name="B", constructor="Team", position=2),
        ],
    )
    monkeypatch.setattr(adjusted_module, "adjusted_constructor_history", lambda season, rounds: {})
    monkeypatch.setattr(adjusted_module.race, "_grid_rank", lambda season, rounds: {"A": 1, "B": 2})

    predicted = adjusted_module.predict_race_order_adjusted(2026, [1], 2)

    assert set(predicted) == {"A", "B"}
    assert predicted["A"] < predicted["B"]
