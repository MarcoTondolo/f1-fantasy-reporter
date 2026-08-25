"""round_pace's session-combining logic, with FastF1 itself mocked out.

The one thing worth pinning here without a live network call: stint numbers
reset to 1 at the start of every session, so naively concatenating FP1 and
FP2 laps would conflate FP1's stint 1 with FP2's stint 1 under a bare
groupby("Stint"). This checks the namespacing that prevents that.
"""

from __future__ import annotations

import pandas as pd
import pytest

from f1_fantasy.pace import dataset as dataset_module
from f1_fantasy.pace.sessions import SessionUnavailable


def _laps(driver: str, times: list[float], stint: str = "1") -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "Driver": [driver] * len(times),
            "LapTime": pd.to_timedelta(times, unit="s"),
            "TyreLife": list(range(1, len(times) + 1)),
            "Stint": [stint] * len(times),
        }
    )
    return frame


def test_stints_from_different_sessions_are_not_conflated(monkeypatch):
    # Both sessions report "stint 1", but FP1's is a fast short-fuel run and
    # FP2's is a slower long-fuel run -- if they got merged into one group,
    # the regression fit would blend two unrelated pace levels.
    fp1 = _laps("A", [78.0, 78.1, 78.0, 78.2, 78.1], stint="1")
    fp2 = _laps("A", [82.0, 82.3, 82.1, 82.4, 82.2], stint="1")

    sessions = {"FP1": fp1, "FP2": fp2}

    def fake_load_clean_laps(season, round_number, session_name, **kwargs):
        if session_name not in sessions:
            raise SessionUnavailable(f"no {session_name}")
        return sessions[session_name]

    monkeypatch.setattr(dataset_module, "load_clean_laps", fake_load_clean_laps)

    pace, used = dataset_module.round_pace(2026, 1, sprint_weekend=False)

    assert used == ["FP1", "FP2"]
    (driver_pace,) = pace
    # Two distinct stints of 5 laps each -- if conflated into one 10-lap
    # group the fit would be garbage; kept separate, the best (FP1's
    # quicker) stint should be picked as the long-run pace.
    assert driver_pace.long_run_pace == pytest.approx(78.1, abs=0.05)


def test_sprint_weekend_only_requests_fp1(monkeypatch):
    requested = []

    def fake_load_clean_laps(season, round_number, session_name, **kwargs):
        requested.append(session_name)
        return _laps("A", [80.0, 80.1, 80.0, 80.2, 80.1])

    monkeypatch.setattr(dataset_module, "load_clean_laps", fake_load_clean_laps)

    dataset_module.round_pace(2026, 12, sprint_weekend=True)

    assert requested == ["FP1"]


def test_a_round_with_no_available_sessions_returns_empty(monkeypatch):
    def always_unavailable(*a, **k):
        raise SessionUnavailable("nothing here")

    monkeypatch.setattr(dataset_module, "load_clean_laps", always_unavailable)

    pace, used = dataset_module.round_pace(2026, 1, sprint_weekend=False)

    assert pace == []
    assert used == []
