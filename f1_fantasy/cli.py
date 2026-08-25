"""Command line entry point.

``probe`` is the one to run first: it answers whether this account can read
other league members' teams, which decides how much of the reporting is
possible at all.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from f1_fantasy.api.client import AuthExpired, FantasyClient, FantasyError
from f1_fantasy.api.endpoints import FantasyApi
from f1_fantasy.api.models import Phase
from f1_fantasy.schedule import Action
from f1_fantasy.collect import collect_league
from f1_fantasy.config import Config, Credentials
from f1_fantasy.store.snapshots import SnapshotStore

log = logging.getLogger("f1_fantasy")

REFRESH_HELP = """
The session cookie is missing or expired. To refresh it:

  1. Open https://fantasy.formula1.com and log in.
  2. Open DevTools (F12) -> Network.
  3. Reload, and find the request to /services/session/login.
  4. From its response, copy "Token" and "GUID".
  5. Set F1_FANTASY_TOKEN to the token and F1_USER_GUID to the guid
     (locally in .env, or as repository secrets for Actions).

The token lasts roughly five days, so expect to repeat this.
""".strip()


def build_api(credentials: Credentials) -> FantasyApi:
    return FantasyApi(FantasyClient(credentials.token, credentials.guid))


# --------------------------------------------------------------------------
# probe
# --------------------------------------------------------------------------


def _team_signature(team) -> tuple:
    """A cheap fingerprint for "is this actually the same team as that one".

    Comparing full objects would flag two genuinely different teams that
    happen to share a captain as distinct-but-similar; comparing just this is
    enough to catch the real failure mode confirmed live -- an endpoint that
    silently hands back the caller's own team for every guid requested.
    """
    return (team.team_name, tuple(sorted(team.player_ids)))


def _dump_raw(api: FantasyApi, label: str, path: str) -> None:
    """Print an endpoint's raw JSON, for diagnosing an unverified payload shape
    without needing another CI round-trip to add ad-hoc logging.
    """
    try:
        raw = api.client.get_raw(path)
    except Exception as exc:  # noqa: BLE001 - this is a diagnostic dump
        print(f"  {label}: request failed: {exc}")
        return
    print(f"\n  -- {label} --\n{json.dumps(raw, indent=2)[:4000]}")


def cmd_probe(args: argparse.Namespace) -> int:
    """Determine how much league data this account can actually read."""
    credentials = Credentials.from_env()
    api = build_api(credentials)

    print("== Authentication ==")
    race_id = args.race or api.current_race_id()
    print(f"  ok -- authenticated, current race id looks like {race_id}")

    print("\n== Leagues ==")
    leagues = api.private_leagues()
    source = "/privateleague"
    if not leagues:
        # Two unverified guesses already turned out wrong once each (payload
        # shape, and possibly this filtered endpoint itself), so try the
        # unfiltered listing before concluding anything -- it costs one extra
        # call and might save a full CI round-trip.
        print("  /privateleague returned none -- trying the unfiltered league list")
        leagues = api.all_leagues()
        source = "/getuserleague"

    if not leagues:
        print("  no leagues found via either endpoint. Raw responses:")
        _dump_raw(api, "/privateleague", f"/services/user/league/{api.guid}/1/0/0/privateleague")
        _dump_raw(api, "/getuserleague", f"/services/user/league/{api.guid}/getuserleague/1")
        return 1

    print(f"  ({source}) found {len(leagues)} league(s):")
    for league in leagues:
        print(f"  {league.league_id:>8}  {league.league_name}  ({league.member_count} members)")

    target = args.league or leagues[0].league_id
    print(f"\n== Leaderboard for league {target} ==")
    league, members = api.leaderboard(target)
    if not members:
        # A silent empty parse here would masquerade as "you're the only
        # member" below -- worse than a crash, since it looks like a real
        # answer. Two guesses about envelope shape have already been wrong
        # once each, so don't trust a third without seeing the raw body.
        print("  0 members parsed -- this endpoint's shape hasn't been confirmed live.")
        _dump_raw(
            api,
            "leaderboard",
            f"/services/user/leaderboard/{api.guid}/pvtleagueuserrankget/1/{target}/0/1/1/1000/",
        )
        return 1
    print(f"  {league.league_name}: {len(members)} members")
    for member in members[:5]:
        print(f"    #{member.rank:<3} {member.user_name:<20} {member.points:>8.0f}")
    if len(members) > 5:
        print(f"    ... and {len(members) - 5} more")

    print("\n== Own team ==")
    own = api.teams(race_id)
    if not own or not own[0].picks:
        print("  WARNING: could not read your own team -- something is wrong beyond sharing")
        _dump_raw(api, "getteam", f"/services/user/gameplay/{api.guid}/getteam/1/1/{race_id}/1")
        return 1
    team = own[0]
    print(f"  ok -- {len(team.picks)} picks, captain {team.captain_id}, value {team.value}")

    # The actual question -- and the one a non-empty response alone cannot
    # answer. Confirmed live: getteam ignores the guid in its URL and just
    # returns the caller's own team every time, so a naive "did I get
    # something back" check gives a false "full access" verdict. Comparing
    # content against the caller's own team is what actually tells readable
    # apart from "this endpoint is just handing back my team again".
    print("\n== Other members' teams ==")
    others = [m for m in members if m.guid != api.guid]
    if not others:
        print("  you are the only member; cannot test")
        return 0

    own_signature = _team_signature(team)
    readable, echoing_own, unreadable = [], [], []
    for member in others:
        result = api.try_teams(race_id, guid=member.guid)
        if not result:
            unreadable.append(member.user_name)
            continue
        if any(_team_signature(t) == own_signature for t in result):
            echoing_own.append(member.user_name)
        else:
            readable.append(member.user_name)

    print(f"  readable:      {len(readable)}/{len(others)}")
    if readable:
        print(f"    {', '.join(readable[:8])}{' ...' if len(readable) > 8 else ''}")
    if echoing_own:
        print(f"  echoing own:   {len(echoing_own)}/{len(others)} (same picks as your team -- not real)")
        print(f"    {', '.join(echoing_own[:8])}{' ...' if len(echoing_own) > 8 else ''}")
    if unreadable:
        print(f"  unreadable:    {len(unreadable)}/{len(others)}")
        print(f"    {', '.join(unreadable[:8])}{' ...' if len(unreadable) > 8 else ''}")

    print("\n== Verdict ==")
    if echoing_own and len(echoing_own) == len(others):
        print("  This endpoint does not support reading other members' teams --")
        print("  it silently returns your own team for every guid requested.")
        print("  Ownership, chip watch, and lockout diffs for other members are NOT")
        print("  real data right now and must not be trusted or shipped as-is.")
        print("  Next step: open the league standings page with DevTools -> Network,")
        print("  click into a rival's team, and note which request the site itself")
        print("  makes. That endpoint can then be wired in. Until then, reports")
        print("  fall back to leaderboard-only: standings, rank movement, points.")
        return 2
    if not unreadable and not echoing_own:
        print("  Full access, content-verified. Every report in the plan is possible:")
        print("  lockout changes, chip watch, ownership, transfer winners and losers.")
        return 0
    if readable:
        print("  Partial access. Reports will cover only the content-verified members,")
        print("  and will say so rather than implying the league is fully covered.")
        return 0

    print("  No access to other members' teams via this endpoint.")
    print("  Next step: open the league standings page with DevTools -> Network,")
    print("  click through to a rival's team, and note which request the site makes.")
    print("  That endpoint can then be wired in. Reports fall back to standings only.")
    return 2


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------


def cmd_doctor(args: argparse.Namespace) -> int:
    """Check credentials, configuration and delivery are all usable."""
    config = Config.load(args.config)
    credentials = Credentials.from_env()

    problems = 0

    print("== Credentials ==")
    print(f"  token:  {'set' if credentials.token else 'MISSING'}")
    print(f"  guid:   {'set' if credentials.guid else 'MISSING'}")
    if not (credentials.token and credentials.guid):
        problems += 1

    print("\n== Config ==")
    print(f"  season:         {config.season}")
    print(f"  leagues:        {config.leagues or 'all private leagues'}")
    print(f"  primary league: {config.primary_league or 'first found'}")
    print(f"  email to:       {config.email_to or 'NOT SET'}")

    print("\n== Delivery ==")
    if credentials.can_send_email and config.email_to:
        print(f"  ok -- SMTP via {credentials.smtp_host} as {credentials.smtp_user}")
    else:
        print("  email not configured; reports will be written to disk only")

    if problems:
        print(f"\n{REFRESH_HELP}")
        return 1

    print("\n== Live check ==")
    api = build_api(credentials)
    race_id = api.current_race_id()
    leagues = api.private_leagues()
    print(f"  ok -- race {race_id}, {len(leagues)} private league(s)")
    return 0


# --------------------------------------------------------------------------
# showcase
# --------------------------------------------------------------------------


def cmd_showcase(args: argparse.Namespace) -> int:
    """Capture and render every configured league -- not just a primary one.

    A one-off utility for seeing real cards before committing to a
    ``primary_league``: the automated `tick` flow only ever renders a card for
    one league per run, which isn't useful when you haven't decided which
    league that should be yet. Only renders chips and ownership -- the two
    card types that are genuinely rich on a first-ever capture, since they
    read the API's cumulative season state rather than diffing against a
    previous snapshot this tool hasn't captured yet.
    """
    from f1_fantasy.render import render_card
    from f1_fantasy.report import chips as chips_report
    from f1_fantasy.report import ownership as ownership_report

    config = Config.load(args.config)
    credentials = Credentials.from_env()
    api = build_api(credentials)
    store = SnapshotStore(config.snapshot_dir)

    race_id = args.race or api.current_race_id()
    league_ids = config.leagues or [league.league_id for league in api.private_leagues()]
    if not league_ids:
        print("no leagues found", file=sys.stderr)
        return 1

    for league_id in league_ids:
        snapshot, access = collect_league(
            api,
            league_id=league_id,
            race_id=race_id,
            phase=Phase.LOCKED,
            season=config.season,
            max_team_fetches=args.top if args.top is not None else config.max_team_fetches,
        )
        store.write(snapshot)
        print(f"{snapshot.league_name} ({league_id}): {access}")

        out_dir = Path(config.output_dir) / str(config.season) / str(race_id) / f"league-{league_id}"
        for name, build, template in (
            ("chips", chips_report.build_chips, "chips.html.j2"),
            ("ownership", ownership_report.build_ownership, "ownership.html.j2"),
        ):
            context = build(snapshot, race_label=f"Round {race_id}")
            path = render_card(template, context, out_dir / f"{name}.png")
            print(f"  {path}")

    return 0


# --------------------------------------------------------------------------
# capture
# --------------------------------------------------------------------------


def cmd_capture(args: argparse.Namespace) -> int:
    """Snapshot every configured league at the given phase."""
    config = Config.load(args.config)
    credentials = Credentials.from_env()
    api = build_api(credentials)
    store = SnapshotStore(config.snapshot_dir)

    race_id = args.race or api.current_race_id()
    phase = Phase(args.phase)

    league_ids = config.leagues or [league.league_id for league in api.private_leagues()]
    if not league_ids:
        print("no leagues to capture", file=sys.stderr)
        return 1

    for league_id in league_ids:
        snapshot, access = collect_league(
            api,
            league_id=league_id,
            race_id=race_id,
            phase=phase,
            season=config.season,
            max_team_fetches=config.max_team_fetches,
        )
        path = store.write(snapshot)
        print(f"{snapshot.league_name}: {access} -> {path}")

    return 0


# --------------------------------------------------------------------------
# plan / tick
# --------------------------------------------------------------------------


def _resolve_event(config: Config, force: str | None):
    """Current race event, the actions due for it, and the run state."""
    from f1_fantasy.calendar import current_event, fetch_calendar
    from f1_fantasy.runner import utcnow
    from f1_fantasy.schedule import Action, RunState, due_actions

    events = fetch_calendar(config.season)
    now = utcnow()
    event = current_event(events, now)
    if event is None:
        return None, [], None

    state = RunState()
    if force:
        return event, [Action(force)], state
    return event, due_actions(event, now, state.done(config.season, event.round)), state


def cmd_plan(args: argparse.Namespace) -> int:
    """Print what is due, in GitHub Actions output format.

    Separate from ``tick`` so the workflow can skip installing a browser on the
    many hourly runs where nothing is happening.
    """
    config = Config.load(args.config)
    event, due, _ = _resolve_event(config, args.force)

    if event is None:
        print("due=")
        print("season=")
        print("round=")
        return 0

    print(f"due={','.join(action.value for action in due)}")
    print(f"season={event.season}")
    print(f"round={event.round}")
    return 0


def cmd_tick(args: argparse.Namespace) -> int:
    """Run whatever the calendar says is due."""
    from f1_fantasy.runner import build_publisher, run_action

    config = Config.load(args.config)
    credentials = Credentials.from_env()

    event, due, state = _resolve_event(config, args.force)
    if event is None:
        print("no upcoming race in the calendar")
        return 0
    if not due:
        print(f"nothing due for round {event.round} ({event.name})")
        return 0

    print(f"round {event.round} ({event.name}): {', '.join(a.value for a in due)}")

    api = build_api(credentials)
    store = SnapshotStore(config.snapshot_dir)
    publisher = build_publisher(config, credentials, dry_run=args.dry_run)
    race_id = args.race or api.current_race_id()

    for action in due:
        written = run_action(
            action,
            api=api,
            config=config,
            event=event,
            race_id=race_id,
            store=store,
            publisher=publisher,
        )
        for path in written:
            print(f"  {action.value}: {path}")
        # Recorded only on success, so a failed action stays due and retries
        # on the next hourly tick rather than being silently skipped.
        if not args.dry_run:
            state.mark(config.season, event.round, action)

    if not args.dry_run:
        state.save()
    return 0


# --------------------------------------------------------------------------
# wiring
# --------------------------------------------------------------------------


def cmd_demo(args: argparse.Namespace) -> int:
    """Render every card from synthetic data, for judging layout by eye."""
    from f1_fantasy.demo import demo_snapshots
    from f1_fantasy.render import render_card
    from f1_fantasy.report import chips as chips_report
    from f1_fantasy.report import lockout as lockout_report
    from f1_fantasy.report import ownership as ownership_report
    from f1_fantasy.report import recap as recap_report
    from f1_fantasy.report import winners_losers as winners_losers_report

    config = Config.load(args.config)
    out_dir = Path(args.out or config.output_dir / "demo")
    previous, current = demo_snapshots()
    label = "Dutch Grand Prix"

    recap_context = recap_report.build_recap(current, previous, race_label=label, you_guid="guid-01")
    lockout_context = lockout_report.build_lockout(current, previous, race_label=label, you_guid="guid-01")
    chips_context = chips_report.build_chips(current, race_label=label)
    ownership_context = ownership_report.build_ownership(current, race_label=label)
    winners_context = winners_losers_report.build_winners_losers(current, previous, race_label=label)

    cards = [
        ("recap", "recap.html.j2", recap_context, recap_report.caption(recap_context)),
        ("lockout", "lockout.html.j2", lockout_context, lockout_report.caption(lockout_context)),
        ("chips", "chips.html.j2", chips_context, chips_report.caption(chips_context)),
        ("ownership", "ownership.html.j2", ownership_context, ownership_report.caption(ownership_context)),
        (
            "winners_losers",
            "winners_losers.html.j2",
            winners_context,
            winners_losers_report.caption(winners_context),
        ),
    ]

    for name, template, ctx, text in cards:
        png = render_card(template, ctx, out_dir / f"{name}.png")
        (out_dir / f"{name}.txt").write_text(text + "\n", encoding="utf-8")
        print(f"rendered {png}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="f1-fantasy", description=__doc__)
    parser.add_argument("--config", default="config.toml", help="path to config.toml")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    probe = sub.add_parser("probe", help="check how much league data is readable")
    probe.add_argument("--league", type=int, help="league id to test against")
    probe.add_argument("--race", type=int, help="race id (default: current)")
    probe.set_defaults(func=cmd_probe)

    doctor = sub.add_parser("doctor", help="validate credentials and configuration")
    doctor.set_defaults(func=cmd_doctor)

    capture = sub.add_parser("capture", help="write a snapshot of each league")
    capture.add_argument(
        "--phase",
        choices=[p.value for p in Phase],
        default=Phase.LOCKED.value,
    )
    capture.add_argument("--race", type=int, help="race id (default: current)")
    capture.set_defaults(func=cmd_capture)

    showcase = sub.add_parser(
        "showcase", help="capture and render chips/ownership for every configured league"
    )
    showcase.add_argument("--race", type=int, help="race id (default: current)")
    showcase.add_argument(
        "--top", type=int, help="cap team fetches to the top N by rank (default: config.max_team_fetches)"
    )
    showcase.set_defaults(func=cmd_showcase)

    demo = sub.add_parser("demo", help="render cards from synthetic data")
    demo.add_argument("--out", help="output directory (default: out/demo)")
    demo.set_defaults(func=cmd_demo)

    actions = [action.value for action in Action]

    plan = sub.add_parser("plan", help="print what is due, for CI to gate on")
    plan.add_argument("--force", choices=actions, help="ignore the calendar")
    plan.set_defaults(func=cmd_plan)

    tick = sub.add_parser("tick", help="run whatever the calendar says is due")
    tick.add_argument("--force", choices=actions, help="ignore the calendar")
    tick.add_argument("--dry-run", action="store_true", help="render but do not send")
    tick.add_argument("--race", type=int, help="race id (default: current)")
    tick.set_defaults(func=cmd_tick)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        return args.func(args)
    except AuthExpired as exc:
        print(f"\nAuthentication failed: {exc}\n\n{REFRESH_HELP}", file=sys.stderr)
        return 3
    except FantasyError as exc:
        print(f"\nAPI error: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
