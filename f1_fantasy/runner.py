"""What a tick actually does once the schedule says something is due."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from f1_fantasy.api.endpoints import FantasyApi
from f1_fantasy.api.models import LeagueSnapshot, Phase
from f1_fantasy.calendar import RaceEvent
from f1_fantasy.collect import collect_league
from f1_fantasy.config import Config, Credentials
from f1_fantasy.publish.base import NullPublisher, Publisher, Report
from f1_fantasy.publish.email import EmailPublisher
from f1_fantasy.render import render_card
from f1_fantasy.report import lockout as lockout_report
from f1_fantasy.report import recap as recap_report
from f1_fantasy.schedule import Action
from f1_fantasy.store.snapshots import SnapshotStore

log = logging.getLogger(__name__)

#: Which snapshot phase each action captures.
PHASE_FOR = {
    Action.PREVIEW: Phase.PRE_LOCK,
    Action.LOCKOUT: Phase.LOCKED,
    Action.RECAP: Phase.FINAL,
}

#: Report builders, keyed by action. Preview and the pace card land in later phases.
BUILDERS = {
    Action.LOCKOUT: (lockout_report.build_lockout, lockout_report.caption, "lockout.html.j2"),
    Action.RECAP: (recap_report.build_recap, recap_report.caption, "recap.html.j2"),
}


def build_publisher(config: Config, credentials: Credentials, *, dry_run: bool) -> Publisher:
    if dry_run:
        log.info("dry run: rendering only, nothing will be sent")
        return NullPublisher()
    publisher = EmailPublisher(credentials, config.email_to)
    if not publisher.configured:
        log.warning("email not configured; reports will only be written to disk")
        return NullPublisher()
    return publisher


def run_action(
    action: Action,
    *,
    api: FantasyApi,
    config: Config,
    event: RaceEvent,
    race_id: int,
    store: SnapshotStore,
    publisher: Publisher,
) -> list[Path]:
    """Capture, render and publish one action. Returns the files written."""
    phase = PHASE_FOR.get(action)
    if phase is None:
        log.info("%s has no capture step yet", action.value)
        return []

    league_ids = config.leagues or [league.league_id for league in api.private_leagues()]
    primary = config.primary_league or (league_ids[0] if league_ids else None)

    written: list[Path] = []

    for league_id in league_ids:
        snapshot, access = collect_league(
            api,
            league_id=league_id,
            race_id=race_id,
            phase=phase,
            season=config.season,
        )
        written.append(store.write(snapshot))
        log.info("league %s: %s", league_id, access)

        # Every league is captured so its history accumulates, but only the
        # primary league produces a card to share.
        if league_id != primary:
            continue

        card = _render_and_publish(
            action,
            snapshot=snapshot,
            store=store,
            config=config,
            event=event,
            publisher=publisher,
        )
        written.extend(card)

    return written


def _render_and_publish(
    action: Action,
    *,
    snapshot: LeagueSnapshot,
    store: SnapshotStore,
    config: Config,
    event: RaceEvent,
    publisher: Publisher,
) -> list[Path]:
    builder = BUILDERS.get(action)
    if builder is None:
        return []
    build, caption, template = builder

    previous_race = store.previous_race_id(snapshot.season, snapshot.league_id, snapshot.race_id)
    previous = (
        store.latest(snapshot.season, snapshot.league_id, previous_race)
        if previous_race is not None
        else None
    )

    context = build(snapshot, previous, race_label=event.name)
    out_dir = Path(config.output_dir) / str(snapshot.season) / str(snapshot.race_id)
    image = render_card(template, context, out_dir / f"{action.value}.png")

    text = caption(context)
    (out_dir / f"{action.value}.txt").write_text(text + "\n", encoding="utf-8")

    publisher.publish(
        Report(
            kind=action.value,
            title=f"{event.name} — {context['eyebrow']}",
            caption=text,
            images=[image],
        )
    )
    return [image, out_dir / f"{action.value}.txt"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
