"""The hindsight card: the optimal team in retrospect vs what members picked.

Unlike every predictive card in this project, hindsight needs no prediction
at all -- the round has already happened, so it calls
``optimise.optimise_team`` with *realised* points and prices straight from
``current.players`` (``Player.race_points``, ``Player.price``), the same
way ``report/picks.py`` calls it with simulated ones. It can be built and
tested the moment optimise.py exists, without waiting on points.py or
simulate.py.

Each member's actual score for the round is ``store.diff.diff_standings``'s
``points_gained`` -- the league's own real points delta, not a recomputation
from picks, so this never drifts from what the rest of this project's cards
already report as "what actually happened".

One acknowledged simplification: the optimal team is chosen by raw points
first, then the best possible captain bonus (2x the highest scorer among
the 5 picked drivers) is added on top. This is not a single joint
optimisation over team-and-captain together, which could in principle find
a slightly lower-raw-points team whose captain upside is large enough to
win overall -- a genuine but second-order gap, left as a known
simplification rather than built speculatively.
"""

from __future__ import annotations

from f1_fantasy.api.models import LeagueSnapshot, Player
from f1_fantasy.predict.optimise import TeamSelection, optimise_team
from f1_fantasy.store.diff import diff_standings

#: F1 Fantasy's standard cost cap.
DEFAULT_CAP = 100.0

#: Standard captain multiplier (this project does not model mega-captains
#: here -- Team.mega_captain_id exists for the real diff engine, but the
#: retrospective "optimal" team has no actual captain pick to read one from).
CAPTAIN_MULTIPLIER = 2


def _split_players(players: dict[str, Player]) -> tuple[dict[str, float], dict[str, float], dict[str, float], dict[str, float]]:
    driver_points, driver_prices, constructor_points, constructor_prices = {}, {}, {}, {}
    for player in players.values():
        key = player.short_name or player.player_id
        if player.is_constructor:
            constructor_points[key] = player.race_points
            constructor_prices[key] = player.price
        else:
            driver_points[key] = player.race_points
            driver_prices[key] = player.price
    return driver_points, driver_prices, constructor_points, constructor_prices


def _optimal_with_captain(players: dict[str, Player], *, cap: float = DEFAULT_CAP) -> tuple[TeamSelection | None, float, str | None]:
    driver_points, driver_prices, constructor_points, constructor_prices = _split_players(players)
    selection = optimise_team(driver_points, driver_prices, constructor_points, constructor_prices, cap=cap)
    if selection is None:
        return None, 0.0, None

    best_captain = max(selection.drivers, key=lambda d: driver_points.get(d, 0.0), default=None)
    captain_bonus = driver_points.get(best_captain, 0.0) * (CAPTAIN_MULTIPLIER - 1) if best_captain else 0.0
    return selection, selection.expected_points + captain_bonus, best_captain


def build_hindsight(
    current: LeagueSnapshot, previous: LeagueSnapshot | None, *, race_label: str = "", cap: float = DEFAULT_CAP
) -> dict:
    """Ignores *previous* for the optimal-team computation itself (this is
    season-state, not a diff) -- kept only so the shared runner.py dispatch
    signature ``build(current, previous, *, race_label)`` stays uniform
    across every companion card, the same convention chips/ownership
    already follow. *previous* is used only via diff_standings, to get each
    member's real points scored this round. *cap* defaults to F1 Fantasy's
    real $100M cost cap; overridable mainly for testing against a smaller
    synthetic player catalogue."""
    selection, optimal_total, captain = _optimal_with_captain(current.players, cap=cap)

    moves = diff_standings(previous, current)
    actual_by_member = {m.member_name: m.points_gained for m in moves}

    gaps = [
        {"name": name, "actual": actual, "gap": round(optimal_total - actual, 2)}
        for name, actual in actual_by_member.items()
    ]
    gaps.sort(key=lambda g: g["gap"])

    closest = gaps[0] if gaps else None
    furthest = gaps[-1] if gaps else None
    average_gap = round(sum(g["gap"] for g in gaps) / len(gaps), 2) if gaps else None

    # _split_players keys driver/constructor points by short_name already,
    # not player_id, so selection.drivers/.constructors are the names
    # themselves -- no further lookup needed.
    optimal_names = list(selection.drivers) + list(selection.constructors) if selection is not None else []

    return {
        "eyebrow": "Hindsight",
        "title": race_label or f"Round {current.race_id}",
        "subtitle": f"the optimal team in retrospect · {current.league_name}",
        "league_name": current.league_name,
        "footer_note": current.captured_at.strftime("%d %b %Y"),
        "optimal_team": optimal_names,
        "optimal_captain": captain,
        "optimal_total": round(optimal_total, 2) if selection else None,
        "closest": closest,
        "furthest": furthest,
        "average_gap": average_gap,
        "caveat": _caveat(selection, previous),
    }


def _caveat(selection: TeamSelection | None, previous: LeagueSnapshot | None) -> str:
    if selection is None:
        return "Not enough priced players this round to compute an optimal team."
    if previous is None:
        return "First scored race captured for this league -- no prior points to diff against."
    return ""


def caption(context: dict) -> str:
    lines = [f"\U0001f52e *Hindsight* -- {context['title']}", ""]
    if context["optimal_total"] is not None:
        lines.append(f"Optimal team scored {context['optimal_total']:.0f} pts (captain: {context['optimal_captain']})")
    if context["closest"]:
        c = context["closest"]
        lines.append(f"Closest to perfect: {c['name']} ({c['actual']:.0f} pts, -{c['gap']:.0f})")
    if context["furthest"]:
        f = context["furthest"]
        lines.append(f"Furthest from perfect: {f['name']} ({f['actual']:.0f} pts, -{f['gap']:.0f})")
    if context["average_gap"] is not None:
        lines.append(f"League average gap to optimal: {context['average_gap']:.1f} pts")
    return "\n".join(lines)
