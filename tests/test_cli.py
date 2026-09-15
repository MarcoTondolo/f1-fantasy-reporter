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
