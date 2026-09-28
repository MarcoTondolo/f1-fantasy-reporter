"""Renders the same lockout/recap card set the live tick pipeline renders
for `config.primary_league`, but for the other leagues this project tracks
league-standings history for.

Purely an assembly step over what `snapshots/` already holds -- no live API
access, so this is safe (and cheap) to call from `build-site` on every run,
same as the rest of `f1_fantasy/site.py`.
"""

from __future__ import annotations

from pathlib import Path

from f1_fantasy.api.models import Phase
from f1_fantasy.render import render_card
from f1_fantasy.report import budget as budget_report
from f1_fantasy.report import chips as chips_report
from f1_fantasy.report import hindsight as hindsight_report
from f1_fantasy.report import lockout as lockout_report
from f1_fantasy.report import ownership as ownership_report
from f1_fantasy.report import recap as recap_report
from f1_fantasy.report import winners_losers as winners_losers_report
from f1_fantasy.store.snapshots import SnapshotStore

#: Leagues the live tick pipeline doesn't render a card for (that's only ever
#: config.primary_league) but the site should show fantasy-team visuals for
#: anyway. DTOUR 2026 -- Ciao Squadra 2026, the primary league, already gets
#: its cards from the live pipeline and is folded in at site-build time
#: instead of being re-rendered here (see `f1_fantasy.site`).
SECONDARY_VISUAL_LEAGUES = [2623604]

#: (card key, builder, caption fn, template) -- same shape as runner.py's
#: BUILDERS/COMPANION_BUILDERS, just flattened into the two card sets a round
#: can produce, since this module isn't gated by config.reports toggles the
#: way a live tick is.
_LOCKOUT_SET = [
    ("lockout", lockout_report.build_lockout, lockout_report.caption, "lockout.html.j2"),
    ("chips", chips_report.build_chips, chips_report.caption, "chips.html.j2"),
    ("ownership", ownership_report.build_ownership, ownership_report.caption, "ownership.html.j2"),
    ("budget", budget_report.build_budget, budget_report.caption, "budget.html.j2"),
]
_RECAP_SET = [
    ("recap", recap_report.build_recap, recap_report.caption, "recap.html.j2"),
    (
        "winners_losers",
        winners_losers_report.build_winners_losers,
        winners_losers_report.caption,
        "winners_losers.html.j2",
    ),
    ("hindsight", hindsight_report.build_hindsight, hindsight_report.caption, "hindsight.html.j2"),
]


def _render_set(cards, current, previous, race_label: str, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for name, build, caption_fn, template in cards:
        context = build(current, previous, race_label=race_label)
        render_card(template, context, dest / f"{name}.png")
        (dest / f"{name}.txt").write_text(caption_fn(context) + "\n", encoding="utf-8")


def render_secondary_league_cards(
    season: int,
    race_labels: dict[int, str],
    *,
    league_ids: list[int] = SECONDARY_VISUAL_LEAGUES,
    snapshot_dir: Path = Path("snapshots"),
    out_dir: Path = Path("out"),
) -> None:
    """Regenerate every card these leagues have snapshots for, from scratch.

    Cheap (no network, just already-loaded JSON through the same builders
    live ticks use) and keeps these cards from drifting if a report template
    or the diff logic changes -- there is no staleness to track.
    """
    store = SnapshotStore(snapshot_dir)
    for league_id in league_ids:
        for race_id in store.race_ids(season, league_id):
            race_label = race_labels.get(race_id, f"Round {race_id}")
            previous_race = store.previous_race_id(season, league_id, race_id)
            previous = store.latest(season, league_id, previous_race) if previous_race is not None else None
            dest = out_dir / str(season) / str(race_id) / f"league-{league_id}"

            if store.exists(season, league_id, race_id, Phase.LOCKED):
                current = store.read(season, league_id, race_id, Phase.LOCKED)
                _render_set(_LOCKOUT_SET, current, previous, race_label, dest)
            if store.exists(season, league_id, race_id, Phase.FINAL):
                current = store.read(season, league_id, race_id, Phase.FINAL)
                _render_set(_RECAP_SET, current, previous, race_label, dest)
