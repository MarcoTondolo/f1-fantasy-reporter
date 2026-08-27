"""The DNF hazard model: history tallying and shrinkage estimation.

``constructor_history`` is tested against the same real-shaped payload used
in test_results.py, since it depends on the corrected classification logic
(a "Lapped" driver who actually retired must count as a DNF). The shrinkage
maths in ``dnf_probability`` is tested against synthetic records with known
answers.
"""

from __future__ import annotations

import pytest

from f1_fantasy.predict.reliability import ReliabilityRecord, constructor_history, dnf_probability
from f1_fantasy.results import parse_race_results
from tests.test_results import RAW_RESULTS_REAL_SHAPE


def test_constructor_history_distinguishes_retirements_from_classified_finishers(monkeypatch):
    """Norris (Finished) and Tsunoda (Lapped) are classified; Albon and
    Verstappen (Retired) and Stroll (Did not start) are DNFs."""
    from f1_fantasy.predict import reliability as reliability_module

    monkeypatch.setattr(
        reliability_module, "fetch_race_results",
        lambda season, rnd: parse_race_results(RAW_RESULTS_REAL_SHAPE),
    )

    history = constructor_history(2026, [1])

    assert history["Red Bull"].races == 1
    assert history["Red Bull"].dnfs == 1  # VER retired
    assert history["Racing Bulls"].dnfs == 0  # TSU lapped but classified
    assert history["Aston Martin"].dnfs == 1  # STR did not start


def test_constructor_history_counts_a_mislabelled_lapped_retirement_as_a_dnf(monkeypatch):
    """The case the module's docstring cites: Stroll (round 1, 43/58 laps)
    and Albon (round 7, 55/66 laps) were both tagged "Lapped" by the results
    feed despite having actually retired, and the game scored both -20. A
    tally that trusted the status alone would undercount every such case."""
    from f1_fantasy.predict import reliability as reliability_module
    from f1_fantasy.results import RaceResult

    def fake_results(season, rnd):
        return [
            RaceResult(driver_code="STR", driver_name="Stroll", constructor="Aston Martin",
                       grid=22, position=17, status="Lapped", laps=43),
            RaceResult(driver_code="WINNER", driver_name="Winner", constructor="McLaren",
                       grid=1, position=1, status="Finished", laps=58),
        ]

    monkeypatch.setattr(reliability_module, "fetch_race_results", fake_results)

    history = constructor_history(2026, [1])

    assert history["Aston Martin"].dnfs == 1
    assert history["Aston Martin"].races == 1


def test_constructor_history_pools_both_cars_across_rounds(monkeypatch):
    from f1_fantasy.predict import reliability as reliability_module
    from f1_fantasy.results import RaceResult

    def fake_results(season, rnd):
        # Two rounds, one team, one DNF total across both cars.
        by_round = {
            1: [
                RaceResult(driver_code="A", driver_name="A", constructor="Team", grid=1,
                           position=1, status="Finished", laps=50),
                RaceResult(driver_code="B", driver_name="B", constructor="Team", grid=2,
                           position=2, status="Finished", laps=50),
            ],
            2: [
                RaceResult(driver_code="A", driver_name="A", constructor="Team", grid=1,
                           position=10, status="Retired", laps=20),
                RaceResult(driver_code="B", driver_name="B", constructor="Team", grid=2,
                           position=3, status="Finished", laps=50),
            ],
        }
        return by_round[rnd]

    monkeypatch.setattr(reliability_module, "fetch_race_results", fake_results)

    history = constructor_history(2026, [1, 2])

    assert history["Team"].races == 4  # 2 cars x 2 rounds
    assert history["Team"].dnfs == 1
    assert history["Team"].raw_rate == pytest.approx(0.25)


def test_dnf_probability_pulls_a_new_constructor_toward_the_prior():
    """No history at all -- the estimate should equal the prior rate exactly."""
    assert dnf_probability(None) == pytest.approx(0.216)


def test_dnf_probability_pulls_a_short_record_toward_the_prior():
    """One DNF in one race is not "100% DNF rate" -- shrinkage pulls it well
    below the raw rate."""
    record = ReliabilityRecord(constructor="New Team", races=1, dnfs=1)

    estimate = dnf_probability(record)

    assert estimate < 1.0
    assert estimate > 0.216  # still pulled up from the prior by the one DNF


def test_dnf_probability_trusts_a_long_consistent_record():
    """20 races, exactly the prior rate's worth of DNFs -- barely moves."""
    record = ReliabilityRecord(constructor="Established Team", races=20, dnfs=4)  # 20% raw

    estimate = dnf_probability(record)

    assert estimate == pytest.approx(0.20, abs=0.02)


def test_dnf_probability_reflects_a_genuinely_unreliable_car():
    """Aston Martin's real 2026 record: 11 DNFs in 24 races (45.8%)."""
    record = ReliabilityRecord(constructor="Aston Martin", races=24, dnfs=11)

    estimate = dnf_probability(record)

    assert estimate > 0.40
    assert estimate < record.raw_rate  # shrinkage still pulls it down slightly


def test_dnf_probability_reflects_a_genuinely_reliable_car():
    """Alpine's real 2026 record: 1 DNF in 24 races (4.2%)."""
    record = ReliabilityRecord(constructor="Alpine", races=24, dnfs=1)

    estimate = dnf_probability(record)

    assert estimate < 0.10
    assert estimate > record.raw_rate  # shrinkage pulls a very low rate up slightly
