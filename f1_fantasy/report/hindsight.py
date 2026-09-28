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

**Optimal team is solved per member's own budget, not one flat cap.** The
optimal team changes shape as the cap it is solved under changes -- a $97M
squad and a $103M squad cannot both be measured against the same $100M
"optimal" without one of them being unfairly flattered or punished, and a
season of price moves and prior transfers means real members rarely share
an identical cap. ``Team.budget_cap`` (value + unspent bank) is each
member's real spend, so their gap is computed against the optimal team
solved at *that* cap, not a shared assumption. ``cap`` (default $100M) is
kept only as a fallback for a member whose team data couldn't be read, and
as a single reference figure alongside the per-member breakdown. A $5M
budget ladder across the range of caps actually held in the league this
round gives a broader reference table -- see ``_budget_ladder`` -- so a
member whose exact cap falls between rungs can still see roughly where they
sit.

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

#: F1 Fantasy's standard cost cap. Used as the headline reference figure and
#: as a fallback cap for a member whose own budget can't be read -- not as
#: the cap every member's gap is measured against (see module docstring).
DEFAULT_CAP = 100.0

#: Standard captain multiplier (this project does not model mega-captains
#: here -- Team.mega_captain_id exists for the real diff engine, but the
#: retrospective "optimal" team has no actual captain pick to read one from).
CAPTAIN_MULTIPLIER = 2

#: Step size for the budget ladder (issue: "optimal team should be
#: calculated at each $5M budget increment").
LADDER_STEP = 5.0


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


def _optimal_with_captain(players: dict[str, Player], *, cap: float) -> tuple[TeamSelection | None, float, str | None]:
    driver_points, driver_prices, constructor_points, constructor_prices = _split_players(players)
    selection = optimise_team(driver_points, driver_prices, constructor_points, constructor_prices, cap=cap)
    if selection is None:
        return None, 0.0, None

    best_captain = max(selection.drivers, key=lambda d: driver_points.get(d, 0.0), default=None)
    captain_bonus = driver_points.get(best_captain, 0.0) * (CAPTAIN_MULTIPLIER - 1) if best_captain else 0.0
    return selection, selection.expected_points + captain_bonus, best_captain


def _member_budget(current: LeagueSnapshot, member) -> float | None:
    team = current.team_for(member)
    if team is not None and team.budget_cap is not None:
        return team.budget_cap
    return None


def _budget_ladder(players: dict[str, Player], budgets: list[float], *, step: float = LADDER_STEP) -> list[dict]:
    """The optimal team at every ``step`` increment spanning the range of
    *budgets* actually held this round -- a reference table for members
    whose own cap falls between rungs, not tied to any one member."""
    if not budgets:
        return []
    lo = (min(budgets) // step) * step
    hi = -((-max(budgets)) // step) * step  # ceil via floor-negation, no extra import
    rungs = []
    budget = lo
    while budget <= hi + 1e-9:
        selection, total, captain = _optimal_with_captain(players, cap=budget)
        rungs.append(
            {
                "budget": round(budget, 1),
                "budget_label": f"${budget:.0f}M",
                "optimal_total": round(total, 2) if selection else None,
                "captain": captain,
            }
        )
        budget += step
    return rungs


def _ladder_note(ladder: list[dict]) -> str:
    """Flag it in plain language when the ladder isn't monotonic -- a lower
    budget rung scoring *more* than the rung above it. This is a real,
    already-documented artifact of ``_optimal_with_captain``'s two-step
    search (raw points first, captain bonus added after -- see module
    docstring): a smaller cap can force a differently-shaped team whose top
    scorer happens to be stronger, and that captain bonus can outweigh the
    larger cap's higher raw-points total. Reported when it actually happens
    rather than caveated unconditionally, so the note only appears when it's
    true of this round's numbers."""
    totals = [(rung["budget"], rung["optimal_total"]) for rung in ladder if rung["optimal_total"] is not None]
    for (lower_budget, lower_total), (higher_budget, higher_total) in zip(totals, totals[1:]):
        if higher_total < lower_total:
            return (
                f"${lower_budget:.0f}M scores higher than ${higher_budget:.0f}M above it -- the captain bonus is "
                "picked after the raw-points-optimal team, not jointly optimised with it, so a smaller cap can "
                "occasionally land a stronger single scorer. Not a search error -- see optimise.py."
            )
    return ""


def build_hindsight(
    current: LeagueSnapshot, previous: LeagueSnapshot | None, *, race_label: str = "", cap: float = DEFAULT_CAP
) -> dict:
    """Ignores *previous* for the optimal-team computation itself (this is
    season-state, not a diff) -- kept only so the shared runner.py dispatch
    signature ``build(current, previous, *, race_label)`` stays uniform
    across every companion card, the same convention chips/ownership
    already follow. *previous* is used only via diff_standings, to get each
    member's real points scored this round. *cap* is a fallback/reference
    only -- see module docstring for why each member's gap is measured
    against their own ``Team.budget_cap`` instead."""
    moves = diff_standings(previous, current)
    actual_by_member = {m.member_name: m.points_gained for m in moves}

    budgets_by_member: dict[str, float] = {}
    for member in current.members:
        budget = _member_budget(current, member)
        if budget is not None:
            budgets_by_member[member.user_name] = budget

    solve_cache: dict[float, tuple[TeamSelection | None, float, str | None]] = {}

    def solved(budget: float) -> tuple[TeamSelection | None, float, str | None]:
        if budget not in solve_cache:
            solve_cache[budget] = _optimal_with_captain(current.players, cap=budget)
        return solve_cache[budget]

    gaps = []
    for name, actual in actual_by_member.items():
        budget = budgets_by_member.get(name)
        effective_budget = budget if budget is not None else cap
        selection, optimal_total, captain = solved(effective_budget)
        bar_pct = round(min(100.0, actual / optimal_total * 100), 1) if selection and optimal_total else 0.0
        gaps.append(
            {
                "name": name,
                "actual": actual,
                "budget_cap": round(effective_budget, 1),
                "budget_cap_label": f"${effective_budget:.1f}M" if budget is not None else f"assumed ${cap:.0f}M",
                "optimal_at_budget": round(optimal_total, 2) if selection else None,
                "optimal_captain_at_budget": captain,
                "gap": round(optimal_total - actual, 2) if selection else None,
                "bar_pct": bar_pct,
            }
        )
    gaps.sort(key=lambda g: (g["gap"] is None, g["gap"]))

    scored = [g for g in gaps if g["gap"] is not None]
    closest = scored[0] if scored else None
    furthest = scored[-1] if scored else None
    average_gap = round(sum(g["gap"] for g in scored) / len(scored), 2) if scored else None

    ladder = _budget_ladder(current.players, sorted(set(budgets_by_member.values())) or [cap])
    ladder_note = _ladder_note(ladder)

    # Headline reference at the project's flat default cap -- distinct from
    # any member's own gap above, which always uses their real budget.
    flat_selection, flat_total, flat_captain = solved(cap)
    optimal_names = list(flat_selection.drivers) + list(flat_selection.constructors) if flat_selection is not None else []

    return {
        "eyebrow": "Hindsight",
        "title": race_label or f"Round {current.race_id}",
        "subtitle": f"the optimal team in retrospect, at each team's own budget · {current.league_name}",
        "league_name": current.league_name,
        "footer_note": current.captured_at.strftime("%d %b %Y"),
        "default_cap": cap,
        "optimal_team": optimal_names,
        "optimal_captain": flat_captain,
        "optimal_total": round(flat_total, 2) if flat_selection else None,
        "gaps": gaps,
        "closest": closest,
        "furthest": furthest,
        "ladder": ladder,
        "ladder_note": ladder_note,
        "average_gap": average_gap,
        "caveat": _caveat(flat_selection, previous),
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
        lines.append(
            f"Optimal @ ${context['default_cap']:.0f}M scored {context['optimal_total']:.0f} pts "
            f"(captain: {context['optimal_captain']})"
        )
    if context["closest"]:
        c = context["closest"]
        lines.append(f"Closest to their own optimal: {c['name']} ({c['actual']:.0f} pts, -{c['gap']:.0f} off {c['budget_cap_label']})")
    if context["furthest"]:
        f = context["furthest"]
        lines.append(f"Furthest from their own optimal: {f['name']} ({f['actual']:.0f} pts, -{f['gap']:.0f} off {f['budget_cap_label']})")
    if context["average_gap"] is not None:
        lines.append(f"League average gap to (own-budget) optimal: {context['average_gap']:.1f} pts")
    return "\n".join(lines)
