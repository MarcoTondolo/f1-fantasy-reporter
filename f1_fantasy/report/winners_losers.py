"""Post-race winners & losers: the verdict view of a race weekend.

Recap answers "where does the league stand"; this answers "who called it
right". Every number here already exists in the diff engine -- standings
movement, transfer scoring, captain scoring -- this module just re-frames them
around that question and adds the one thing recap doesn't cover: whether a
chip played this race actually paid off.
"""

from __future__ import annotations

from f1_fantasy.api.models import CHIP_LABELS, LeagueSnapshot
from f1_fantasy.store.diff import (
    StandingsMove,
    TransferScore,
    chip_activations,
    diff_standings,
    diff_teams,
    score_captains,
    score_changes,
)

#: How many entries to show in each top-movers list.
TOP_N = 3


def _swap_detail(score: TransferScore) -> str:
    if score.is_clean_swap:
        return f"{score.players_out[0].name} → {score.players_in[0].name}"
    if score.players_in:
        return (
            f"{len(score.players_out)} out, {len(score.players_in)} in "
            f"({', '.join(p.name for p in score.players_in[:2])}"
            f"{'…' if len(score.players_in) > 2 else ''})"
        )
    return "no changes"


def build_winners_losers(
    current: LeagueSnapshot,
    previous: LeagueSnapshot | None,
    *,
    race_label: str = "",
) -> dict:
    moves = diff_standings(previous, current)

    # Headline: the single best and worst race, by points scored this round.
    by_points = sorted(moves, key=lambda m: m.points_gained, reverse=True)
    headline_winner = by_points[0] if by_points and by_points[0].points_gained > 0 else None
    headline_loser = by_points[-1] if by_points else None
    if headline_loser and headline_winner and headline_loser.guid == headline_winner.guid:
        # A one-member league would otherwise crown the same person twice.
        headline_loser = None

    rank_gainers = sorted(
        (m for m in moves if m.rank_change > 0), key=lambda m: m.rank_change, reverse=True
    )[:TOP_N]
    rank_losers = sorted((m for m in moves if m.rank_change < 0), key=lambda m: m.rank_change)[
        :TOP_N
    ]

    changes = diff_teams(previous, current)
    scores = [s for s in score_changes(changes, current) if s.made_changes]
    best_transfers = [s for s in scores if s.delta > 0][:TOP_N]
    worst_transfers = sorted((s for s in scores if s.delta < 0), key=lambda s: s.delta)[:TOP_N]

    calls = [c for c in score_captains(current) if c.captain]
    best_captain = calls[0] if calls else None
    worst_captain = None
    if len(calls) > 1 and calls[-1].bonus < calls[0].bonus:
        worst_captain = calls[-1]

    # Did this race's chip plays pay off? Cross-reference against the points
    # each of those members actually scored.
    points_by_member = {m.member_name: m.points_gained for m in moves}
    chip_bets = [
        {"chip": CHIP_LABELS[chip], "name": name, "points": points_by_member.get(name)}
        for chip, names in sorted(chip_activations(current).items(), key=lambda kv: kv[0].value)
        for name in names
    ]

    return {
        "eyebrow": "Winners & losers",
        "title": race_label or f"Round {current.race_id}",
        "subtitle": f"{len(moves)} teams scored this round · {current.league_name}",
        "league_name": current.league_name,
        "footer_note": current.captured_at.strftime("%d %b %Y"),
        "headline_winner": _headline(headline_winner),
        "headline_loser": _headline(headline_loser),
        "rank_gainers": [_mover(m) for m in rank_gainers],
        "rank_losers": [_mover(m) for m in rank_losers],
        "best_transfers": [_transfer(s) for s in best_transfers],
        "worst_transfers": [_transfer(s) for s in worst_transfers],
        "best_captain": _captain(best_captain),
        "worst_captain": _captain(worst_captain),
        "chip_bets": chip_bets,
        "caveat": _caveat(previous),
    }


def _headline(move: StandingsMove | None) -> dict | None:
    if move is None:
        return None
    return {"name": move.member_name, "points": move.points_gained, "rank": move.rank}


def _mover(move: StandingsMove) -> dict:
    return {"name": move.member_name, "rank_change": move.rank_change, "rank": move.rank}


def _transfer(score: TransferScore) -> dict:
    return {"name": score.member_name, "delta": score.delta, "detail": _swap_detail(score)}


def _captain(call) -> dict | None:
    if call is None:
        return None
    return {
        "name": call.member_name,
        "captain": call.captain.name,
        "bonus": call.bonus,
        "multiplier": call.multiplier,
    }


def _caveat(previous: LeagueSnapshot | None) -> str:
    if previous is None:
        return (
            "First scored race captured for this league -- rank movement and "
            "transfer verdicts start from the next round."
        )
    return ""


def caption(context: dict) -> str:
    lines = [f"\U0001f3c6 *Winners & Losers* -- {context['title']}", ""]

    if context["headline_winner"]:
        winner = context["headline_winner"]
        lines.append(f"\U0001f947 Best race: *{winner['name']}* ({winner['points']:+.0f} pts)")
    if context["headline_loser"]:
        loser = context["headline_loser"]
        lines.append(f"\U0001f4c9 Roughest race: *{loser['name']}* ({loser['points']:+.0f} pts)")

    if context["best_transfers"]:
        t = context["best_transfers"][0]
        lines.append(f"\U0001f4c8 Best move: {t['name']} ({t['delta']:+g}) — {t['detail']}")
    if context["worst_transfers"]:
        t = context["worst_transfers"][0]
        lines.append(f"\U0001f480 Worst move: {t['name']} ({t['delta']:+g}) — {t['detail']}")

    if context["best_captain"]:
        c = context["best_captain"]
        lines.append(f"\U0001f3d7️ Best armband: {c['name']} on {c['captain']} (+{c['bonus']:g})")

    return "\n".join(lines)
