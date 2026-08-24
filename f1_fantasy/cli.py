"""Command line entry point.

``probe`` is the one to run first: it answers whether this account can read
other league members' teams, which decides how much of the reporting is
possible at all.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from f1_fantasy.api.client import AuthExpired, FantasyClient, FantasyError
from f1_fantasy.api.endpoints import FantasyApi
from f1_fantasy.api.models import Phase
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


def cmd_probe(args: argparse.Namespace) -> int:
    """Determine how much league data this account can actually read."""
    credentials = Credentials.from_env()
    api = build_api(credentials)

    print("== Authentication ==")
    race_id = args.race or api.current_race_id()
    print(f"  ok -- authenticated, current race id looks like {race_id}")

    print("\n== Leagues ==")
    leagues = api.private_leagues()
    if not leagues:
        print("  no private leagues found for this account")
        return 1
    for league in leagues:
        print(f"  {league.league_id:>8}  {league.league_name}  ({league.member_count} members)")

    target = args.league or leagues[0].league_id
    print(f"\n== Leaderboard for league {target} ==")
    league, members = api.leaderboard(target)
    print(f"  {league.league_name}: {len(members)} members")
    for member in members[:5]:
        print(f"    #{member.rank:<3} {member.user_name:<20} {member.points:>8.0f}")
    if len(members) > 5:
        print(f"    ... and {len(members) - 5} more")

    print("\n== Own team ==")
    own = api.teams(race_id)
    if not own:
        print("  WARNING: could not read your own team -- something is wrong beyond sharing")
        return 1
    team = own[0]
    print(f"  ok -- {len(team.picks)} picks, captain {team.captain_id}, value {team.value}")

    # The actual question.
    print("\n== Other members' teams ==")
    others = [m for m in members if m.guid != api.guid]
    if not others:
        print("  you are the only member; cannot test")
        return 0

    readable, unreadable = [], []
    for member in others:
        result = api.try_teams(race_id, guid=member.guid)
        (readable if result else unreadable).append(member.user_name)

    print(f"  readable:   {len(readable)}/{len(others)}")
    if readable:
        print(f"    {', '.join(readable[:8])}{' ...' if len(readable) > 8 else ''}")
    if unreadable:
        print(f"  unreadable: {', '.join(unreadable[:8])}{' ...' if len(unreadable) > 8 else ''}")

    print("\n== Verdict ==")
    if not unreadable:
        print("  Full access. Every report in the plan is possible:")
        print("  lockout changes, chip watch, ownership, transfer winners and losers.")
        return 0
    if readable:
        print("  Partial access. Reports will cover only the readable members,")
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
        )
        path = store.write(snapshot)
        print(f"{snapshot.league_name}: {access} -> {path}")

    return 0


# --------------------------------------------------------------------------
# wiring
# --------------------------------------------------------------------------


def cmd_demo(args: argparse.Namespace) -> int:
    """Render every card from synthetic data, for judging layout by eye."""
    from f1_fantasy.demo import demo_snapshots
    from f1_fantasy.render import render_card
    from f1_fantasy.report import recap as recap_report

    config = Config.load(args.config)
    out_dir = Path(args.out or config.output_dir / "demo")
    previous, current = demo_snapshots()

    context = recap_report.build_recap(
        current,
        previous,
        race_label="Dutch Grand Prix",
        you_guid="guid-01",
    )
    png = render_card("recap.html.j2", context, out_dir / "recap.png")
    (out_dir / "recap.txt").write_text(recap_report.caption(context) + "\n", encoding="utf-8")

    print(f"rendered {png}")
    print(f"caption  {out_dir / 'recap.txt'}")
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

    demo = sub.add_parser("demo", help="render cards from synthetic data")
    demo.add_argument("--out", help="output directory (default: out/demo)")
    demo.set_defaults(func=cmd_demo)

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
