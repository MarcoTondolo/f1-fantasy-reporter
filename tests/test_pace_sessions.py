"""load_laps's SessionUnavailable conversion, against a fake FastF1 session.

Not a general FastF1 integration test -- just the one failure mode that
matters here: FastF1's ``.load()`` can return without raising even when a
session's data genuinely isn't available yet (it swallows its own internal
SessionNotAvailableError per data category and only logs a warning), so
the real failure surfaces later, on the ``.laps`` property access.
"""

from __future__ import annotations

import fastf1
import pytest
from fastf1.exceptions import DataNotLoadedError

from f1_fantasy.pace import sessions as sessions_module
from f1_fantasy.pace.sessions import SessionUnavailable, load_laps


class _FakeSessionLapsRaise:
    def load(self, **kwargs):
        return None  # "succeeds" without actually populating _laps

    @property
    def laps(self):
        raise DataNotLoadedError("laps data has not been loaded yet")


def test_load_laps_converts_a_post_load_failure_to_session_unavailable(monkeypatch):
    """Regression test for the same real failure hit live in GitHub
    Actions (see tests/test_news_lineup_watch.py's identical case for
    news/lineup_watch.py, which follows this function's own precedent):
    a DataNotLoadedError from the ``.laps`` property used to propagate
    uncaught past this function's try/except instead of degrading to
    SessionUnavailable like every other "session isn't ready yet" case."""
    load_laps.cache_clear()
    monkeypatch.setattr(sessions_module, "_ensure_cache", lambda cache_dir=None: None)
    monkeypatch.setattr(fastf1, "get_session", lambda season, rnd, name: _FakeSessionLapsRaise())

    with pytest.raises(SessionUnavailable):
        load_laps(2026, 13, "FP1")

    load_laps.cache_clear()


def test_load_laps_raises_session_unavailable_when_load_itself_raises(monkeypatch):
    load_laps.cache_clear()

    class RaisingSession:
        def load(self, **kwargs):
            raise RuntimeError("no such session")

    monkeypatch.setattr(sessions_module, "_ensure_cache", lambda cache_dir=None: None)
    monkeypatch.setattr(fastf1, "get_session", lambda season, rnd, name: RaisingSession())

    with pytest.raises(SessionUnavailable):
        load_laps(2026, 14, "FP1")

    load_laps.cache_clear()
