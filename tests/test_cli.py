"""``_resolve_event``: which round a run acts on, and what it may mark done.

The rest of cli.py is thin argparse wiring over modules that have their own
tests. This one function is worth pinning on its own because getting it wrong
does not fail loudly -- it silently attributes work to the wrong round.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from f1_fantasy import cli
from f1_fantasy.calendar import parse_calendar
from f1_fantasy.config import Config
from f1_fantasy.schedule import Action

UTC = timezone.utc


def _race(round_number: int, date: str) -> dict:
    return {
        "season": "2026",
        "round": str(round_number),
        "raceName": f"Round {round_number} Grand Prix",
        "date": date,
        "time": "13:00:00Z",
        "Circuit": {"circuitName": "Somewhere", "Location": {}},
        "Qualifying": {"date": date, "time": "09:00:00Z"},
    }


EVENTS = parse_calendar(
    {"MRData": {"RaceTable": {"Races": [_race(14, "2026-09-13"), _race(15, "2026-09-26")]}}}
)


@pytest.fixture
def at(monkeypatch, tmp_path):
    """Resolve an event at a chosen moment, against a throwaway state file."""
    monkeypatch.setattr(cli, "Config", Config)
    monkeypatch.chdir(tmp_path)

    def resolve(now: datetime, force: str | None = None, round_number: int | None = None):
        monkeypatch.setattr("f1_fantasy.calendar.fetch_calendar", lambda season: EVENTS)
        monkeypatch.setattr("f1_fantasy.runner.utcnow", lambda: now)
        return cli._resolve_event(Config(season=2026), force, round_number)

    return resolve


def test_a_forced_action_without_a_round_targets_the_race_it_belongs_to(at):
    """Two days after round 14 its recap is still the outstanding work."""
    event, due, _ = at(datetime(2026, 9, 15, 12, 0, tzinfo=UTC), force="recap")

    assert event.round == 14
    assert due == [Action.RECAP]


def test_an_explicit_round_wins_over_the_calendar(at):
    """The fix for round 14: a manual run says which round it means, so it can
    never render one round's card while marking another round's slot done."""
    event, due, _ = at(datetime(2026, 9, 24, 12, 0, tzinfo=UTC), force="recap", round_number=14)

    # Round 15 is the current event by then -- the override still picks 14.
    assert at(datetime(2026, 9, 24, 12, 0, tzinfo=UTC))[0].round == 15
    assert event.round == 14
    assert due == [Action.RECAP]


def test_an_unknown_round_resolves_to_nothing_rather_than_the_wrong_race(at):
    event, due, state = at(datetime(2026, 9, 15, 12, 0, tzinfo=UTC), force="recap", round_number=99)

    assert event is None
    assert due == []
    assert state is None


# -- picks: where a driver's constructor comes from -------------------------


def test_constructor_attribution_covers_every_driver_the_game_prices():
    """Regression test for Aston Martin's 0.4 expected points at round 15.

    cmd_picks used to read constructor_of from a qualifying classification,
    which lists only drivers who took part in that session. Anyone who missed
    it -- Hadjar, injured for round 14, and Stroll and Bearman, absent from
    that classification -- fell out of the mapping, so their expected points
    never reached their constructor and it was valued as if it fielded one car
    or none. The driver feed's own TeamName covers the whole field, so this
    pins the property that matters: every priced driver has a constructor.
    """
    from f1_fantasy.predict.points import constructor_points_from_drivers

    driver_points = {"STR": 4.1, "ALO": -2.6, "BEA": 4.0, "OCO": 4.6}
    # What a qualifying classification gave us: two of the four missing.
    from_qualifying = {"ALO": "Aston Martin", "OCO": "Haas F1 Team"}
    # What the feed's TeamName gives us: all four.
    from_feed = {
        "STR": "Aston Martin", "ALO": "Aston Martin",
        "BEA": "Haas F1 Team", "OCO": "Haas F1 Team",
    }

    partial = constructor_points_from_drivers(driver_points, from_qualifying)
    complete = constructor_points_from_drivers(driver_points, from_feed)

    # The dropped drivers are pure loss to their constructor, not redistributed.
    assert complete["Aston Martin"] - partial["Aston Martin"] == pytest.approx(4.1)
    assert complete["Haas F1 Team"] - partial["Haas F1 Team"] == pytest.approx(4.0)
    # And a driver missing from the mapping is silent, not an error -- which is
    # exactly why this went unnoticed.
    assert set(partial) == {"Aston Martin", "Haas F1 Team"}


def test_a_constructor_never_scores_more_than_its_two_cars():
    """Round 15 had Verstappen, Lawson and Hadjar all listed at Red Bull Racing.

    The driver feed keys drivers by contracted team, so a mid-season swap puts
    three drivers on one constructor and summing them credits a third car that
    cannot score.
    """
    from f1_fantasy.cli import _two_best_drivers_per_constructor

    constructor_of = {
        "VER": "Red Bull Racing", "LAW": "Red Bull Racing", "HAD": "Red Bull Racing",
        "RUS": "Mercedes", "ANT": "Mercedes",
    }
    driver_points = {"VER": 17.4, "LAW": 7.6, "HAD": 13.4, "RUS": 25.6, "ANT": 28.6}

    kept = _two_best_drivers_per_constructor(constructor_of, driver_points)

    assert sorted(k for k, v in kept.items() if v == "Red Bull Racing") == ["HAD", "VER"]
    assert sorted(k for k, v in kept.items() if v == "Mercedes") == ["ANT", "RUS"]


def test_a_driver_with_no_predicted_points_is_dropped_not_counted_at_zero():
    """A driver the form model has never seen must not displace one it has."""
    from f1_fantasy.cli import _two_best_drivers_per_constructor

    kept = _two_best_drivers_per_constructor(
        {"VER": "Red Bull Racing", "LAW": "Red Bull Racing", "NEW": "Red Bull Racing"},
        {"VER": 17.4, "LAW": 7.6},
    )

    assert sorted(kept) == ["LAW", "VER"]
