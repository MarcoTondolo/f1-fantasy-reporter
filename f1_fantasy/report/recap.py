"""The post-race recap card.

Builds both the template context and the caption that goes with the image, so
the two can never drift -- the caption is generated from the same numbers, not
written by hand alongside them.
"""

from __future__ import annotations

from f1_fantasy.api.models import CHIP_LABELS, LeagueSnapshot
from f1_fantasy.render.teams import team_color
from f1_fantasy.store.diff import (
    chip_activations,
    diff_standings,
    diff_teams,
    score_captains,
    score_changes,
)


def _move(rank_change: int, has_previous: bool) -> tuple[str, str]:
    """Arrow glyph plus class. The glyph carries direction, colour reinforces."""
    if not has_previous:
        return "NEW", "flat"
    if rank_change > 0:
        return f"▲{rank_change}", "up"
    if rank_change < 0:
        return f"▼{abs(rank_change)}", "down"
    return "–", "flat"


def _swap_detail(score) -> str:
    """Human phrasing for what a member did, honest about multi-swap weeks."""
    if score.is_clean_swap:
        return f"{score.players_out[0].name} → {score.players_in[0].name}"
    if score.players_in and score.players_out:
        return (
            f"{len(score.players_out)} out, {len(score.players_in)} in "
            f"({', '.join(p.name for p in score.players_in[:3])}"
            f"{'…' if len(score.players_in) > 3 else ''})"
        )
    if score.players_in:
        return f"Added {', '.join(p.name for p in score.players_in[:3])}"
    return "No changes"


def build_recap(
    current: LeagueSnapshot,
    previous: LeagueSnapshot | None,
    *,
    race_label: str = "",
    you_guid: str | None = None,
) -> dict:
    """Assemble the recap context from a scored snapshot."""
    moves = diff_standings(previous, current)
    gains = [m.points_gained for m in moves]
    peak = max(gains) if gains and max(gains) > 0 else 1.0

    standings = []
    for move in moves:
        label, css_class = _move(move.rank_change, move.previous_rank is not None)
        standings.append(
            {
                "rank": move.rank,
                "name": move.member_name,
                "team_name": move.team_name,
                "gained": move.points_gained,
                "total": move.points,
                "bar_pct": max(0.0, round(move.points_gained / peak * 100, 1)),
                "move_label": label,
                "move_class": css_class,
                "is_podium": move.rank <= 3,
                "is_you": you_guid is not None and move.guid == you_guid,
            }
        )

    # Round winner: most points scored this race, not the overall leader.
    hero = None
    if moves and previous is not None:
        top = max(moves, key=lambda m: m.points_gained)
        if top.points_gained > 0:
            hero = {
                "name": top.member_name,
                "points": top.points_gained,
                "note": f"Now {_ordinal(top.rank)} overall on {top.points:g}",
            }

    # Transfer outcomes -- only meaningful for members who actually changed something.
    changes = diff_teams(previous, current)
    scores = [s for s in score_changes(changes, current) if s.made_changes]
    best_move = worst_move = None
    if scores:
        best, worst = scores[0], scores[-1]
        if best.delta > 0:
            best_move = {
                "name": best.member_name,
                "delta": best.delta,
                "detail": _swap_detail(best),
            }
        if worst.delta < 0 and worst.guid != best.guid:
            worst_move = {
                "name": worst.member_name,
                "delta": worst.delta,
                "detail": _swap_detail(worst),
            }

    # Captaincy.
    captain = None
    calls = [c for c in score_captains(current) if c.captain]
    if calls:
        best_call = calls[0]
        captain = {
            "best": {
                "name": best_call.member_name,
                "bonus": best_call.bonus,
                "captain": best_call.captain.name,
                "multiplier": best_call.multiplier,
                "color": _captain_color(current, best_call),
            },
            "worst": None,
        }
        worst_call = calls[-1]
        if len(calls) > 1 and worst_call.bonus < best_call.bonus:
            captain["worst"] = {
                "name": worst_call.member_name,
                "bonus": worst_call.bonus,
                "captain": worst_call.captain.name,
                "multiplier": worst_call.multiplier,
                "color": _captain_color(current, worst_call),
            }

    chips_played = [
        {"label": CHIP_LABELS[chip], "who": ", ".join(names)}
        for chip, names in sorted(chip_activations(current).items(), key=lambda kv: kv[0].value)
    ]

    return {
        "eyebrow": "Race recap",
        "title": race_label or f"Round {current.race_id}",
        "subtitle": f"{len(current.members)} teams · {current.league_name}",
        "league_name": current.league_name,
        "footer_note": current.captured_at.strftime("%d %b %Y"),
        "standings": standings,
        "hero": hero,
        "best_move": best_move,
        "worst_move": worst_move,
        "captain": captain,
        "chips_played": chips_played,
        "caveat": _caveat(current, previous),
    }


def _captain_color(snapshot: LeagueSnapshot, call) -> str:
    player = snapshot.players.get(call.captain.player_id)
    return team_color(player.constructor if player else None)


def _ordinal(value: int) -> str:
    if 10 <= value % 100 <= 20:
        return f"{value}th"
    return f"{value}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(value % 10, 'th') }"


def _caveat(current: LeagueSnapshot, previous: LeagueSnapshot | None) -> str:
    """State plainly when the card is working from incomplete data."""
    if previous is None:
        return (
            "First capture for this league -- movement and transfer results start "
            "from the next race."
        )
    covered = len(current.teams)
    total = len(current.members)
    if covered < total:
        missing = total - covered
        return (
            f"Team details unavailable for {missing} of {total} members; "
            "transfer and chip sections cover the rest only."
        )
    return ""


def caption(context: dict) -> str:
    """The text to paste alongside the image in the chat."""
    lines = [f"\U0001f3c1 *{context['title']}* -- {context['league_name']}"]

    if context.get("hero"):
        hero = context["hero"]
        lines.append(f"\U0001f947 Round winner: *{hero['name']}* with {hero['points']:g} pts")

    top = context["standings"][:3]
    if top:
        lines.append("")
        lines.append("*Top 3*")
        for row in top:
            lines.append(f"{row['rank']}. {row['name']} — {row['total']:g}")

    if context.get("best_move"):
        best = context["best_move"]
        lines.append("")
        lines.append(f"\U0001f4c8 Best move: {best['name']} ({best['delta']:+g}) — {best['detail']}")
    if context.get("worst_move"):
        worst = context["worst_move"]
        lines.append(f"\U0001f4c9 Worst move: {worst['name']} ({worst['delta']:+g}) — {worst['detail']}")

    if context.get("chips_played"):
        lines.append("")
        played = "; ".join(f"{c['label']} ({c['who']})" for c in context["chips_played"])
        lines.append(f"\U0001f3b4 Chips: {played}")

    return "\n".join(lines)
