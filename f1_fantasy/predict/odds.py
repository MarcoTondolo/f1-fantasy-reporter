"""Betting-odds collection: a minimal, always-degrading stub.

Earlier research in this project concluded betting odds are *plausibly* a
strong predictor but access to betting-site data from this environment is
unverified -- direct fetches to the betting sites checked were unreliable
or blocked. Rather than defer this entirely, this module exists as a small,
honest placeholder: the shape a real collector would have, wired to zero
real sources by default, so nothing downstream (points.py, simulate.py,
optimise.py) can ever come to depend on it succeeding.

If a reliable source turns up later, it plugs in as one more callable in
``sources`` -- a zero-argument function returning ``{selection: decimal_odds}``
for one market. Until then, ``fetch_odds_snapshot`` with no sources supplied
just reports that plainly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from f1_fantasy.calendar import RaceEvent

log = logging.getLogger(__name__)

#: A source is a zero-argument callable returning {selection: decimal_odds}
#: for one market, or raising on failure. No real sources are wired in yet --
#: see the module docstring.
OddsSource = Callable[[], dict[str, float]]

DEFAULT_SOURCES: list[tuple[str, OddsSource]] = []


@dataclass
class OddsSnapshot:
    market: str
    captured_at: datetime
    source: str
    entries: dict[str, float] = field(default_factory=dict)
    fetch_succeeded: bool = False
    note: str = ""


def fetch_odds_snapshot(
    event: RaceEvent, *, market: str = "race_winner", sources: list[tuple[str, OddsSource]] | None = None
) -> OddsSnapshot:
    """Best-effort, pre-Q1 odds collection for one market.

    Tries each (name, source) pair in order and returns the first that
    succeeds. Never raises: a source that errors is logged and skipped, and
    running out of sources (including the default empty list) returns
    ``fetch_succeeded=False`` with a human-readable note rather than an
    exception, so a CLI or card calling this can never be broken by it.
    """
    captured_at = datetime.now(timezone.utc)
    for name, source in sources if sources is not None else DEFAULT_SOURCES:
        try:
            entries = source()
        except Exception as exc:  # noqa: BLE001 -- any one source failing must not break the collector
            log.warning("odds source %r failed for %s: %s", name, event.name, exc)
            continue
        if entries:
            return OddsSnapshot(market=market, captured_at=captured_at, source=name, entries=entries, fetch_succeeded=True)

    return OddsSnapshot(
        market=market,
        captured_at=captured_at,
        source="",
        fetch_succeeded=False,
        note="no odds source configured or all sources failed -- see the module docstring",
    )
