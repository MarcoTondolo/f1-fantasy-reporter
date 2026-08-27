"""The pre-race picks card: expected points, price moves, and a captain call.

No league/team data dependency -- same class of card as ``report/preview.py``,
built from ``predict/simulate.py``'s Monte Carlo summaries and
``predict/optimise.py``'s team selection rather than a league snapshot diff.
"""

from __future__ import annotations

from f1_fantasy.calendar import RaceEvent
from f1_fantasy.predict.optimise import TeamSelection
from f1_fantasy.predict.simulate import SimulationSummary, captaincy_ev

#: How many entries to show in the top-picks and price-risers lists.
TOP_N = 8

#: Shown on every card so a reader never mistakes this for the game's own
#: projection, and knows roughly how much to trust it -- this project's
#: established transparency convention (see form.py's own "0.898 is
#: 2026-specific" caveat) applied to a new card.
MODEL_CAVEAT = (
    "Model projection, not the game's own ProjectedGamedayPoints. "
    "Form backtest: 0.898 mean Spearman (2026) -- weaker in settled-regulation "
    "seasons (see multi_season.py). Treat as directional, not exact."
)


def _pick_row(summary: SimulationSummary) -> dict:
    return {
        "driver": summary.driver,
        "price": round(summary.price, 1),
        "mean": round(summary.mean, 1),
        "p10": round(summary.p10, 1),
        "p90": round(summary.p90, 1),
    }


def _riser_row(summary: SimulationSummary) -> dict:
    return {
        "driver": summary.driver,
        "price": round(summary.price, 1),
        "p_price_rise": round(summary.p_price_rise * 100.0),
        "mean_delta_budget": round(summary.mean_delta_budget, 2),
    }


def build_picks(
    event: RaceEvent,
    summaries: dict[str, SimulationSummary],
    team_selection: TeamSelection | None,
    *,
    league_name: str = "",
    top_n: int = TOP_N,
) -> dict:
    ranked_by_mean = sorted(summaries.values(), key=lambda s: -s.mean)[:top_n]
    risers = sorted(
        (s for s in summaries.values() if s.p_price_rise > 0), key=lambda s: -s.p_price_rise
    )[:top_n]

    captain_ranked = captaincy_ev(summaries)
    captain_suggestion = (
        {"driver": captain_ranked[0][0], "ev": round(captain_ranked[0][1], 1)} if captain_ranked else None
    )

    optimal_team = None
    if team_selection is not None:
        optimal_team = {
            "drivers": list(team_selection.drivers),
            "constructors": list(team_selection.constructors),
            "expected_points": round(team_selection.expected_points, 1),
            "total_price": round(team_selection.total_price, 1),
        }

    return {
        "eyebrow": "Race picks",
        "title": event.name,
        "subtitle": f"{event.circuit}, {event.locality}" if event.circuit else "",
        "league_name": league_name,
        "footer_note": "",
        "top_picks": [_pick_row(s) for s in ranked_by_mean],
        "price_risers": [_riser_row(s) for s in risers],
        "captain_suggestion": captain_suggestion,
        "optimal_team": optimal_team,
        "caveat": MODEL_CAVEAT,
    }


def caption(context: dict) -> str:
    lines = [f"\U0001f3c1 *Race picks* -- {context['title']}"]
    if context["subtitle"]:
        lines.append(context["subtitle"])
    lines.append("")

    if context["top_picks"]:
        lines.append("\U0001f4ca Top expected points:")
        for row in context["top_picks"][:5]:
            lines.append(f"  {row['driver']}: {row['mean']:.1f} (p10 {row['p10']:.1f} / p90 {row['p90']:.1f})")

    if context["captain_suggestion"]:
        c = context["captain_suggestion"]
        lines.append("")
        lines.append(f"\U0001f3d7️ Captain suggestion: {c['driver']} (EV {c['ev']:.1f})")

    if context["price_risers"]:
        lines.append("")
        lines.append("\U0001f4c8 Likely price risers:")
        for row in context["price_risers"][:5]:
            lines.append(f"  {row['driver']}: {row['p_price_rise']:.0f}% (Δ{row['mean_delta_budget']:+.2f}M)")

    if context["optimal_team"]:
        team = context["optimal_team"]
        lines.append("")
        lines.append(
            f"\U0001f9e9 Optimal team: {', '.join(team['drivers'])} + {', '.join(team['constructors'])} "
            f"({team['expected_points']:.1f} pts, ${team['total_price']:.1f}M)"
        )

    return "\n".join(lines)
