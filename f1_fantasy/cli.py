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
# refresh-token
# --------------------------------------------------------------------------


def cmd_refresh_token(args: argparse.Namespace) -> int:
    """Open a real, visible browser, let the user log in, and capture the
    resulting session token -- see refresh_token.py's module docstring for
    why this drives a real login instead of scripting one."""
    from f1_fantasy.refresh_token import capture_session, push_github_secrets, write_env_file

    try:
        captured = capture_session(timeout_s=args.timeout)
    except TimeoutError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1

    env_path = Path(args.env_file)
    write_env_file(captured, env_path=env_path)
    print(f"\nCaptured a new session token; wrote F1_FANTASY_TOKEN/F1_USER_GUID to {env_path}")

    if args.github_repo:
        if push_github_secrets(captured, repo=args.github_repo):
            print(f"pushed F1_FANTASY_TOKEN/F1_USER_GUID to {args.github_repo} repo secrets")
        else:
            print(
                f"could not push to {args.github_repo} repo secrets (see warning above) -- "
                f"{env_path} is still updated",
                file=sys.stderr,
            )
    return 0


# --------------------------------------------------------------------------
# showcase
# --------------------------------------------------------------------------


def cmd_showcase(args: argparse.Namespace) -> int:
    """Capture and render every configured league -- not just a primary one.

    A one-off utility for seeing real cards before committing to a
    ``primary_league``: the automated `tick` flow only ever renders a card for
    one league per run, which isn't useful when you haven't decided which
    league that should be yet. Only renders chips, ownership and budget --
    the card types that are genuinely rich on a first-ever capture, since
    they read the API's cumulative season state rather than diffing against
    a previous snapshot this tool hasn't captured yet.
    """
    from f1_fantasy.render import render_card
    from f1_fantasy.report import budget as budget_report
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
            ("budget", budget_report.build_budget, "budget.html.j2"),
        ):
            context = build(snapshot, race_label=f"Round {race_id}")
            path = render_card(template, context, out_dir / f"{name}.png")
            print(f"  {path}")

    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    """Render the preview card for the next race: session times, grid
    penalties confirmed from the last race, and flagged headlines.

    No league credentials needed -- calendar, results and news are all public.
    """
    from datetime import datetime, timezone

    from f1_fantasy.calendar import current_event, fetch_calendar, previous_event
    from f1_fantasy.news.bulletins import headlines as fetch_headlines
    from f1_fantasy.render import render_card
    from f1_fantasy.report import preview as preview_report
    from f1_fantasy.results import detect_grid_penalties, fetch_qualifying, fetch_race_results

    config = Config.load(args.config)
    events = fetch_calendar(config.season)
    if not events:
        print("no calendar data", file=sys.stderr)
        return 1

    if args.round is not None:
        by_round = {e.round: e for e in events}
        next_event = by_round.get(args.round)
        if next_event is None:
            print(f"round {args.round} not found in the {config.season} calendar", file=sys.stderr)
            return 1
        last_event = by_round.get(args.round - 1)
    else:
        now = datetime.now(timezone.utc)
        next_event = current_event(events, now)
        if next_event is None:
            print("no upcoming race found in the calendar", file=sys.stderr)
            return 1
        last_event = previous_event(events, now)

    penalties = []
    if last_event is not None:
        try:
            qualifying = fetch_qualifying(config.season, last_event.round)
            results = fetch_race_results(config.season, last_event.round)
            penalties = detect_grid_penalties(qualifying, results)
        except Exception as exc:  # noqa: BLE001 -- results may not exist yet
            log.warning("could not fetch last race's results: %s", exc)

    try:
        news = [
            {"title": h.title, "summary": h.summary, "matched": list(h.matched)}
            for h in fetch_headlines()
        ]
    except Exception as exc:  # noqa: BLE001 -- news is a nice-to-have, not a hard dependency
        log.warning("could not fetch headlines: %s", exc)
        news = []

    context = preview_report.build_preview(
        next_event,
        last_race_name=last_event.name if last_event else "",
        last_race_penalties=penalties,
        headlines=news,
        timezone=config.timezone,
    )
    out_dir = Path(config.output_dir) / str(config.season) / str(next_event.round)
    path = render_card("preview.html.j2", context, out_dir / "preview.png")
    (out_dir / "preview.txt").write_text(preview_report.caption(context) + "\n", encoding="utf-8")
    print(f"rendered {path}")
    return 0


def cmd_pace_backtest(args: argparse.Namespace) -> int:
    """Backfill the pace pipeline over a range of rounds and evaluate it.

    Fits a pace-based ranking model with leave-one-round-out cross-validation
    -- each round is scored only by a model that never saw it -- against
    actual qualifying and race positions, and against a one-lap-pace-only
    baseline. No league credentials needed: FastF1 and Jolpica are both
    public. Slow -- FastF1 downloads full session timing data per round.
    """
    import json

    from f1_fantasy.pace.backtest import run_backfill

    config = Config.load(args.config)
    rounds = list(range(args.start, args.end + 1))
    print(f"backfilling {config.season} rounds {rounds[0]}-{rounds[-1]} (this can take a while)...")
    result = run_backfill(config.season, rounds)

    out_path = Path(args.out or f"data/pace/backtest_{config.season}_r{rounds[0]}-{rounds[-1]}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    print(f"rounds used: {result['rounds_used']}")
    if result["skipped"]:
        print(f"skipped: {result['skipped']}")
    print("quali:", result["quali"]["summary"])
    print("race:", result["race"]["summary"])
    print(f"written {out_path}")
    return 0


def cmd_track_backtest(args: argparse.Namespace) -> int:
    """Backfill the track-segment pace pipeline and evaluate cross-track prediction.

    Splits each track into corner (slow/medium/fast) and straight segments
    from qualifying telemetry, ranks drivers by segment-class strength from
    every *other* round, and scores that prediction against the held-out
    round's real results -- i.e. whether one track's segment performance
    predicts another's. Much slower than pace-backtest: pulls full telemetry
    (not just lap summaries) for every driver, every round.
    """
    import json

    from f1_fantasy.pace.track_backtest import backtest_track_model

    config = Config.load(args.config)
    rounds = list(range(args.start, args.end + 1))
    print(f"backfilling track segments for {config.season} rounds {rounds[0]}-{rounds[-1]} (slow)...")
    result = backtest_track_model(config.season, rounds)

    out_path = Path(args.out or f"data/pace/track_backtest_{config.season}_r{rounds[0]}-{rounds[-1]}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    print(f"rounds used: {result['rounds_used']}")
    if result["skipped"]:
        print(f"skipped: {result['skipped']}")
    print("summary:", result["summary"])
    print(f"written {out_path}")
    return 0


def cmd_hers_backtest(args: argparse.Namespace) -> int:
    """Gate 3: falsification test for the H-ERS (circuit energy x PU efficiency) hypothesis.

    Checks whether 2026 clipping severity (time spent at full throttle but
    decelerating -- energy depletion under the new power-unit regs) predicts
    each circuit's year-on-year lap-time loss versus 2025, better than a flat
    per-team offset would. Requires FastF1 telemetry for both seasons at
    every matched circuit -- slow, and the 2025 side is not yet cached by
    anything else in this project.
    """
    import json

    from f1_fantasy.pace.hers import hers_falsification_test

    config = Config.load(args.config)
    rounds = list(range(args.start, args.end + 1))
    print(f"H-ERS falsification test: {config.season} rounds {rounds[0]}-{rounds[-1]} vs {args.compare_season}...")
    result = hers_falsification_test(config.season, args.compare_season, rounds)

    out_path = Path(args.out or f"data/pace/hers_backtest_{config.season}_r{rounds[0]}-{rounds[-1]}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    print(f"circuits checked: {result['circuits_checked']}")
    print(f"ranked by clipping: {result['ranked_circuits_by_clipping']}")
    print(f"Spa rank: {result['spa_rank']}, Monaco rank: {result['monaco_rank']} of {result['n_circuits_ranked']}")
    print(f"correlation (severity vs YoY delta): {result['correlation_severity_vs_delta']}")
    print(f"Mercedes Monaco rank: {result['mercedes_monaco_rank']} of {result['mercedes_monaco_n_teams']}")
    print(f"written {out_path}")
    return 0


def cmd_tyre_asymmetry(args: argparse.Namespace) -> int:
    """Correlate each constructor's degradation against track corner-direction balance.

    A directional proxy, not a wear measurement -- FastF1 carries no tyre
    sensor data, so this cannot say *which* corner (front-right, ...) is
    limiting, only whether a constructor's degradation tracks how much a
    track turns left vs right overall. Reuses the same qualifying telemetry
    as track-backtest, so run that first if you want the fetch to be cached.
    """
    import json

    from f1_fantasy.pace.tyre_asymmetry import run_backfill

    config = Config.load(args.config)
    rounds = list(range(args.start, args.end + 1))
    print(f"correlating degradation vs corner direction for {config.season} rounds {rounds[0]}-{rounds[-1]}...")
    result = run_backfill(config.season, rounds)

    out_path = Path(args.out or f"data/pace/tyre_asymmetry_{config.season}_r{rounds[0]}-{rounds[-1]}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    print(f"rounds used: {result['rounds_used']}")
    if result["skipped"]:
        print(f"skipped: {result['skipped']}")
    ranked = sorted(
        result["constructor_direction_sensitivity"].items(),
        key=lambda kv: -abs(kv[1]["correlation"]) if kv[1]["correlation"] is not None else 1,
    )
    for team, stats in ranked:
        corr = stats["correlation"]
        print(f"  {team:<16} corr={corr:+.3f} n={stats['n']}" if corr is not None else f"  {team:<16} n/a n={stats['n']}")
    print(f"written {out_path}")
    return 0


def cmd_reconcile_scoring(args: argparse.Namespace) -> int:
    """Prove the fantasy scoring table against the game's own published points.

    Reconstructs each driver's qualifying and race points from public results
    and compares them to what F1 Fantasy actually awarded. Qualifying must
    match exactly. Race points are reconciled without overtakes -- which no
    results feed carries -- so the leftover residual should be a small
    non-negative integer for every driver. Anything negative or fractional
    means a scoring rule is wrong.
    """
    from f1_fantasy.predict.reconcile import reconcile_round, summarise

    config = Config.load(args.config)
    rounds = list(range(args.start, args.end + 1))
    everything = []
    print(f"{'rnd':>4}{'drv':>5}{'Q ok':>6}{'Q bad':>7}{'plausible':>11}{'implausible':>13}")
    for round_number in rounds:
        try:
            rows = reconcile_round(config.season, round_number, cache_dir=args.cache_dir)
        except Exception as exc:  # noqa: BLE001 -- one bad round shouldn't abort the sweep
            print(f"{round_number:>4}  failed: {exc}")
            continue
        everything.extend(rows)
        s = summarise(rows)
        print(f"{round_number:>4}{s['drivers']:>5}{s['qualifying_exact']:>6}"
              f"{s['qualifying_mismatches']:>7}{s['residual_plausible_as_overtakes']:>11}"
              f"{s['residual_implausible']:>13}")

    total = summarise(everything)
    print("\n== overall ==")
    for key, value in total.items():
        print(f"  {key}: {value}")

    failed = total["qualifying_mismatches"] or total["residual_implausible"]
    if failed:
        print("\nGATE FAILED: a scoring rule does not reproduce the game's own points.")
        for row in everything:
            if not row.qualifying_matches or not row.residual_looks_like_overtakes:
                print(f"  R{row.round_number} {row.driver}: "
                      f"Q {row.actual_qualifying:g} vs {row.expected_qualifying:g}, "
                      f"race residual {row.residual:g}")
        return 1
    print("\nGATE PASSED: qualifying exact, every race residual a non-negative integer.")
    return 0


def cmd_price_backtest(args: argparse.Namespace) -> int:
    """Gate 2: check the PPM price-tier mechanism against real recorded price changes.

    The thresholds are not fitted here -- they are the publicly documented
    F1 Fantasy algorithm (see predict/prices.py) -- this just checks how
    often it reproduces what the game actually did, round by round, using
    the same public driver feed reconcile-scoring caches.
    """
    from f1_fantasy.predict.prices import backtest_prices

    config = Config.load(args.config)
    rounds = list(range(args.start, args.end + 1))
    print(f"checking PPM price-tier predictions for {config.season} rounds {rounds[0]}-{rounds[-1]}...")
    result = backtest_prices(rounds, cache_dir=args.cache_dir)

    print(f"matches: {result['matches']}/{result['total']} ({result['match_rate']:.1%})" if result["total"] else "no data")
    for row in result["mismatches"]:
        print(f"  R{row['round']} {row['driver']}: actual {row['actual']:+.1f} vs predicted {row['predicted']:+.1f}")
    return 0


def cmd_collect_odds(args: argparse.Namespace) -> int:
    """Best-effort betting-odds snapshot for the next race. No real source is
    wired in yet (see predict/odds.py) -- this always exits 0, even on total
    failure, so it can never break automation."""
    import json
    from datetime import datetime, timezone

    from f1_fantasy.calendar import current_event, fetch_calendar
    from f1_fantasy.predict.odds import fetch_odds_snapshot

    config = Config.load(args.config)
    events = fetch_calendar(config.season)
    if not events:
        print("no calendar data", file=sys.stderr)
        return 0

    if args.round is not None:
        by_round = {e.round: e for e in events}
        event = by_round.get(args.round)
    else:
        event = current_event(events, datetime.now(timezone.utc))
    if event is None:
        print("no matching race found in the calendar", file=sys.stderr)
        return 0

    snapshot = fetch_odds_snapshot(event)
    out_path = Path(args.out or f"data/pace/odds_{config.season}_r{event.round}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(snapshot.__dict__, indent=2, default=str), encoding="utf-8")

    if snapshot.fetch_succeeded:
        print(f"collected {len(snapshot.entries)} entries from {snapshot.source!r}")
    else:
        print(f"no odds collected: {snapshot.note}")
    print(f"written {out_path}")
    return 0


def cmd_track_upgrades(args: argparse.Namespace) -> int:
    """Fetch upgrade-package mentions across autosport/motorsport/racefans/
    F1.com, attribute them to constructors and rounds, measure each
    attributed upgrade's before/after competitiveness step-change relative
    to the field, and render the upgrade tracker card. No league
    credentials needed -- everything here is public.
    """
    import dataclasses
    import json
    from datetime import datetime, timezone

    from f1_fantasy.calendar import current_event, fetch_calendar
    from f1_fantasy.news.upgrades import track_upgrades
    from f1_fantasy.predict.upgrades import evaluate_attributed_upgrades
    from f1_fantasy.render import render_card
    from f1_fantasy.report import upgrades as upgrades_report

    config = Config.load(args.config)
    print(f"fetching upgrade mentions for {config.season}...")
    tracked = track_upgrades(config.season)

    end = args.end
    if end is None:
        # Recurring automation (the daily GitHub Actions run) never passes
        # --end, so a hardcoded default would silently stop growing once
        # the season passes it -- resolve to the latest round with real
        # results instead, the same "current/next event" calendar lookup
        # cmd_picks/cmd_daily_digest already use.
        events = fetch_calendar(config.season)
        next_event = current_event(events, datetime.now(timezone.utc)) if events else None
        end = (next_event.round - 1) if next_event else max((e.round for e in events), default=args.start)
        end = max(end, args.start)
    available_rounds = list(range(args.start, end + 1))
    effects = evaluate_attributed_upgrades(config.season, tracked["groups"], available_rounds, window=args.window)

    result = {
        "season": config.season,
        "mentions": [m.model_dump(mode="json") for m in tracked["mentions"]],
        "effects": [dataclasses.asdict(e) for e in effects],
    }

    out_path = Path(args.out or f"data/pace/upgrades_{config.season}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    print(f"mentions found: {len(tracked['mentions'])}")
    print(f"attributed (constructor, round) pairs: {len(tracked['groups'])}")
    print(f"measured effects: {len(effects)}")
    for effect in effects:
        verdict = "improved vs field" if effect.relative_delta is not None and effect.relative_delta < 0 else "no measurable edge vs field"
        print(f"  {effect.constructor} r{effect.upgrade_round}: relative_delta={effect.relative_delta} (n_before={effect.n_before}, n_after={effect.n_after}) -- {verdict}")

    context = upgrades_report.build_upgrades(tracked["mentions"], effects, season=config.season)
    out_dir = Path(config.output_dir) / str(config.season) / "upgrades"
    image = render_card("upgrades.html.j2", context, out_dir / "upgrades.png")
    (out_dir / "upgrades.txt").write_text(upgrades_report.caption(context) + "\n", encoding="utf-8")

    print(f"written {out_path}")
    print(f"written {image}")
    return 0


def cmd_daily_digest(args: argparse.Namespace) -> int:
    """Fetch, classify and email the daily news digest: confirmed lineup
    changes (data-driven, via news/lineup_watch.py) alongside rumoured
    lineup news, upgrade packages, and penalties/reliability stories (all
    classified from one RSS fetch by news/digest.py). No league
    credentials needed -- everything here is public; only email sending
    needs SMTP credentials, and degrades to disk-only when unconfigured,
    same as every other publisher in this project.
    """
    from datetime import datetime, timezone

    from f1_fantasy.calendar import current_event, fetch_calendar
    from f1_fantasy.config import Credentials
    from f1_fantasy.news.digest import build_digest
    from f1_fantasy.news.lineup_watch import (
        constructor_of_for_round,
        constructor_of_from_practice,
        detect_lineup_changes,
    )
    from f1_fantasy.pace.sessions import SessionUnavailable
    from f1_fantasy.publish.base import NullPublisher, Report
    from f1_fantasy.publish.email import EmailPublisher
    from f1_fantasy.report import digest as digest_report

    config = Config.load(args.config)
    events = fetch_calendar(config.season)
    if not events:
        print("no calendar data", file=sys.stderr)
        return 1

    if args.round is not None:
        by_round = {e.round: e for e in events}
        target_event = by_round.get(args.round)
        if target_event is None:
            print(f"round {args.round} not found in the {config.season} calendar", file=sys.stderr)
            return 1
    else:
        target_event = current_event(events, datetime.now(timezone.utc))
        if target_event is None:
            print("no upcoming race found in the calendar", file=sys.stderr)
            return 1

    target_round = target_event.round
    previous_round = target_round - 1

    confirmed_changes = []
    lineup_watch_note = ""
    if previous_round < 1:
        lineup_watch_note = "no prior round to diff lineup changes against yet"
    else:
        before = constructor_of_for_round(config.season, previous_round)
        after = constructor_of_for_round(config.season, target_round)
        if not after:
            try:
                after = constructor_of_from_practice(config.season, target_round)
            except SessionUnavailable as exc:
                lineup_watch_note = f"no qualifying or practice session data yet for round {target_round} ({exc})"
        if before and after:
            confirmed_changes = detect_lineup_changes(target_round, before, after)

    digest = build_digest(
        config.season, target_round, events,
        confirmed_changes=confirmed_changes, lineup_watch_note=lineup_watch_note,
    )
    context = digest_report.build_digest_report(digest)
    text = digest_report.caption(context)

    out_dir = Path(config.output_dir) / str(config.season) / "digest"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "digest.txt"
    out_path.write_text(text + "\n", encoding="utf-8")

    if args.dry_run:
        publisher = NullPublisher()
    else:
        publisher = EmailPublisher(Credentials.from_env(), config.email_to)
        if not publisher.configured:
            print("email not configured; digest written to disk only", file=sys.stderr)
            publisher = NullPublisher()

    publisher.publish(Report(kind="digest", title=context["title"], caption=text))

    print(text)
    print(f"written {out_path}")
    return 0


def cmd_record_benchmark(args: argparse.Namespace) -> int:
    """Append a manually-captured external benchmark snapshot to this
    season's benchmark store -- e.g. numbers copied by hand off
    f1fantasytools.com's Elite Data table. No scraping: that table loads
    client-side and is not reachable by a plain fetch or headless-browser
    automation (see predict/benchmarks.py's module docstring); this is the
    deliberate manual-capture path instead.
    """
    from datetime import datetime, timezone

    from f1_fantasy.predict.benchmarks import ExternalBenchmarkSnapshot, append_snapshot, parse_manual_entries

    config = Config.load(args.config)
    entries = parse_manual_entries(args.entries)
    if not entries:
        print("no usable entries parsed from --entries", file=sys.stderr)
        return 1

    snapshot = ExternalBenchmarkSnapshot(
        source=args.source,
        season=config.season,
        round_number=args.round,
        session_label=args.session or "",
        captured_at=datetime.now(timezone.utc),
        entries=entries,
        note=args.note or "",
    )
    path = append_snapshot(snapshot)

    print(f"recorded {len(entries)} entries from {args.source!r} (round {args.round}, session {args.session or '-'})")
    print(f"written {path}")
    return 0


def cmd_collect_benchmark_snapshot(args: argparse.Namespace) -> int:
    """Capture the automatable benchmark signals for one round -- the
    official feed's own ProjectedGamedayPoints, crowd-consensus ownership
    %, our own current expected-points mean, and (when ANTHROPIC_API_KEY is
    set) all four f1fantasytools.com pages (team-calculator, statistics,
    budget-builder, elite-data -- see predict/f1fantasytools_capture.py's
    F1FT_PAGES) read via the Claude API's vision input -- and append each
    as a dated snapshot. Safe to run repeatedly through a race weekend;
    never hard-fails on one bad source, matching predict/odds.py's
    never-raises standard.
    """
    from datetime import datetime, timezone

    from f1_fantasy.calendar import current_event, fetch_calendar
    from f1_fantasy.config import Credentials
    from f1_fantasy.predict.benchmarks import (
        ExternalBenchmarkSnapshot,
        append_snapshot,
        crowd_consensus_snapshot,
        official_projected_snapshot,
    )
    from f1_fantasy.predict.f1fantasytools_capture import F1FT_PAGES, capture_all_f1fantasytools_snapshots
    from f1_fantasy.predict.points import build_round_distributions

    config = Config.load(args.config)
    season = config.season
    session_label = args.session or ""

    round_number = args.round
    if round_number is None:
        events = fetch_calendar(season)
        next_event = current_event(events, datetime.now(timezone.utc)) if events else None
        if next_event is None:
            print("no upcoming race found in the calendar", file=sys.stderr)
            return 1
        round_number = next_event.round

    captured: list[str] = []

    try:
        snapshot = official_projected_snapshot(
            season, round_number, session_label=session_label, cache_dir=args.cache_dir
        )
        append_snapshot(snapshot)
        captured.append("official_projected")
    except Exception as exc:  # noqa: BLE001 -- one bad source must not block the others
        print(f"could not capture official_projected snapshot: {exc}", file=sys.stderr)

    try:
        snapshot = crowd_consensus_snapshot(season, round_number, cache_dir=args.cache_dir)
        append_snapshot(snapshot)
        captured.append("crowd_consensus")
    except Exception as exc:  # noqa: BLE001
        print(f"could not capture crowd_consensus snapshot: {exc}", file=sys.stderr)

    train_rounds = list(range(1, round_number))
    if train_rounds:
        try:
            distributions = build_round_distributions(season, train_rounds, round_number)
            if distributions:
                snapshot = ExternalBenchmarkSnapshot(
                    source="ours",
                    season=season,
                    round_number=round_number,
                    session_label=session_label,
                    captured_at=datetime.now(timezone.utc),
                    entries={driver: dist.mean for driver, dist in distributions.items()},
                )
                append_snapshot(snapshot)
                captured.append("ours")
        except Exception as exc:  # noqa: BLE001
            print(f"could not capture our own distribution snapshot: {exc}", file=sys.stderr)

    api_key = Credentials.from_env().anthropic_api_key
    if not api_key:
        print("ANTHROPIC_API_KEY not set; skipping f1fantasytools screenshot captures", file=sys.stderr)
    else:
        f1ft_snapshots = capture_all_f1fantasytools_snapshots(
            season, round_number, api_key=api_key, session_label=session_label
        )
        for snapshot in f1ft_snapshots:
            append_snapshot(snapshot)
            captured.append(snapshot.source)
        missing = len(F1FT_PAGES) - len(f1ft_snapshots)
        if missing:
            print(f"{missing} f1fantasytools page(s) returned nothing (see warnings above)", file=sys.stderr)

    if not captured:
        print("no benchmark snapshots captured", file=sys.stderr)
        return 0
    print(f"captured snapshots: {', '.join(captured)}")
    return 0


def cmd_benchmarks(args: argparse.Namespace) -> int:
    """Render the benchmark tracker card: our own current picks next to
    every snapshot captured so far for this round, plus our own
    already-committed backtested track record. No league credentials
    needed -- same class of card as picks.py/upgrades.py.
    """
    from datetime import datetime, timezone

    from f1_fantasy.calendar import current_event, fetch_calendar
    from f1_fantasy.predict.benchmarks import load_backtest_track_record, load_snapshots
    from f1_fantasy.predict.prices import round_history
    from f1_fantasy.predict.reconcile import fetch_driver_feed
    from f1_fantasy.predict.simulate import simulate_round
    from f1_fantasy.render import render_card
    from f1_fantasy.report import benchmarks as benchmarks_report

    config = Config.load(args.config)
    events = fetch_calendar(config.season)
    if not events:
        print("no calendar data", file=sys.stderr)
        return 1

    if args.round is not None:
        by_round = {e.round: e for e in events}
        next_event = by_round.get(args.round)
        if next_event is None:
            print(f"round {args.round} not found in the {config.season} calendar", file=sys.stderr)
            return 1
    else:
        next_event = current_event(events, datetime.now(timezone.utc))
        if next_event is None:
            print("no upcoming race found in the calendar", file=sys.stderr)
            return 1

    target_round = next_event.round
    train_rounds = list(range(1, target_round))
    summaries = {}
    if train_rounds:
        last_round = train_rounds[-1]
        try:
            driver_feed = fetch_driver_feed(last_round, cache_dir=args.cache_dir)
        except Exception as exc:  # noqa: BLE001 -- show whatever snapshots exist even if the live feed is down
            print(f"could not fetch the public driver feed for round {last_round}: {exc}", file=sys.stderr)
            driver_feed = {}
        if driver_feed:
            price_before = {code: float(row.get("Value") or 0) for code, row in driver_feed.items()}
            recent_price_history = round_history(train_rounds, cache_dir=args.cache_dir)
            summaries = simulate_round(
                config.season, train_rounds, target_round,
                price_before=price_before, recent_price_history=recent_price_history,
                sprint=next_event.is_sprint_weekend, n_samples=args.n_samples,
            )

    snapshots = load_snapshots(config.season)
    backtest_summary = load_backtest_track_record(config.season)

    context = benchmarks_report.build_benchmarks(
        snapshots, summaries, backtest_summary,
        season=config.season, round_number=target_round, league_name=args.league_name or "",
    )
    out_dir = Path(config.output_dir) / str(config.season) / str(target_round)
    path = render_card("benchmarks.html.j2", context, out_dir / "benchmarks.png")
    (out_dir / "benchmarks.txt").write_text(benchmarks_report.caption(context) + "\n", encoding="utf-8")
    print(f"written {path}")
    return 0


def cmd_picks(args: argparse.Namespace) -> int:
    """Render the picks card for the next race: expected points, price-rise
    probability, captain suggestion, optimal team.

    No league credentials needed -- calendar and the public driver feed are
    both public. Needs at least one round of real results to train form
    from, and the public driver feed for the most recent completed round to
    get real current prices -- see predict/simulate.py and predict/prices.py.
    With --email, also sends the rendered card via EmailPublisher (same
    mechanism as daily-digest) -- unlike the tick/preview/lockout/recap
    pipeline, this never needs F1_FANTASY_TOKEN/GUID, so it still works
    when the league session cookie has expired.
    """
    from datetime import datetime, timezone

    from f1_fantasy.calendar import current_event, fetch_calendar
    from f1_fantasy.predict.optimise import optimise_team
    from f1_fantasy.predict.points import constructor_points_from_drivers
    from f1_fantasy.predict.prices import round_history
    from f1_fantasy.predict.reconcile import fetch_constructor_feed_rows, fetch_driver_feed, to_feed_constructor_name
    from f1_fantasy.predict.simulate import simulate_round
    from f1_fantasy.render import render_card
    from f1_fantasy.report import picks as picks_report
    from f1_fantasy.results import fetch_qualifying

    config = Config.load(args.config)
    events = fetch_calendar(config.season)
    if not events:
        print("no calendar data", file=sys.stderr)
        return 1

    if args.round is not None:
        by_round = {e.round: e for e in events}
        next_event = by_round.get(args.round)
        if next_event is None:
            print(f"round {args.round} not found in the {config.season} calendar", file=sys.stderr)
            return 1
    else:
        next_event = current_event(events, datetime.now(timezone.utc))
        if next_event is None:
            print("no upcoming race found in the calendar", file=sys.stderr)
            return 1

    target_round = next_event.round
    train_rounds = list(range(1, target_round))
    if not train_rounds:
        print(f"no prior rounds to train from for round {target_round}", file=sys.stderr)
        return 1
    last_round = train_rounds[-1]

    try:
        driver_feed = fetch_driver_feed(last_round, cache_dir=args.cache_dir)
        constructor_feed = fetch_constructor_feed_rows(last_round, cache_dir=args.cache_dir)
    except Exception as exc:  # noqa: BLE001 -- the public feed may be briefly unavailable, not worth a traceback
        print(f"could not fetch the public driver feed for round {last_round}: {exc}", file=sys.stderr)
        return 1

    price_before = {code: float(row.get("Value") or 0) for code, row in driver_feed.items()}
    constructor_prices = {name: float(row.get("Value") or 0) for name, row in constructor_feed.items()}
    recent_price_history = round_history(train_rounds, cache_dir=args.cache_dir)

    summaries = simulate_round(
        config.season, train_rounds, target_round,
        price_before=price_before, recent_price_history=recent_price_history,
        sprint=next_event.is_sprint_weekend, n_samples=args.n_samples,
    )
    if not summaries:
        print("not enough form history to build picks yet", file=sys.stderr)
        return 1

    constructor_of = {q.driver_code: q.constructor for q in fetch_qualifying(config.season, last_round)}
    driver_points = {d: s.mean for d, s in summaries.items()}
    # constructor_of is Jolpica-named; constructor_prices above is keyed by
    # the feed's FUllName -- translate before aggregating, or the four names
    # that differ get silently valued at 0 by optimise_team.
    constructor_points = constructor_points_from_drivers(
        driver_points,
        {d: to_feed_constructor_name(c) for d, c in constructor_of.items()},
        p_q3={d: s.p_q3 for d, s in summaries.items()},
        driver_dotd_points={d: s.mean_dotd_points for d, s in summaries.items()},
    )

    team_selection = optimise_team(
        driver_points, price_before, constructor_points, constructor_prices, captain_multiplier=2.0
    )

    context = picks_report.build_picks(next_event, summaries, team_selection, league_name=args.league_name or "")
    out_dir = Path(config.output_dir) / str(config.season) / str(target_round)
    path = render_card("picks.html.j2", context, out_dir / "picks.png")
    caption_text = picks_report.caption(context)
    (out_dir / "picks.txt").write_text(caption_text + "\n", encoding="utf-8")
    print(f"written {path}")

    if args.email:
        from f1_fantasy.config import Credentials
        from f1_fantasy.publish.base import Report
        from f1_fantasy.publish.email import EmailPublisher

        publisher = EmailPublisher(Credentials.from_env(), config.email_to)
        if not publisher.configured:
            print("email not configured (SMTP secrets/email_to); picks card written to disk only", file=sys.stderr)
        else:
            publisher.publish(Report(kind="picks", title=context["title"], caption=caption_text, images=[path]))
            print(f"emailed to {config.email_to}")

    return 0


def cmd_race_backtest(args: argparse.Namespace) -> int:
    """Falsification test: does discounting for DNF risk beat the plain form-predicted grid?

    Walk-forward only -- each round's prediction sees strictly earlier rounds.
    Reports Spearman correlation and rank MAE against actual race order for
    the form-only baseline and the reliability-gated predictor side by side.
    """
    import json

    from f1_fantasy.predict.race import backtest_race_order

    config = Config.load(args.config)
    rounds = list(range(args.start, args.end + 1))
    print(f"walk-forward race-order backtest, {config.season} rounds {rounds[0]}-{rounds[-1]}...")
    result = backtest_race_order(config.season, rounds)

    out_path = Path(args.out or f"data/pace/race_backtest_{config.season}_r{rounds[0]}-{rounds[-1]}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    for label in ("baseline_grid_rank", "reliability_gated"):
        print(f"{label}: {result[label]['summary']}")
    print(f"written {out_path}")
    return 0


#: Default round count per season -- 2024 and 2025 both ran the full 24-race
#: calendar; 2026 is backfilled only through the 12 rounds this project has
#: covered elsewhere.
DEFAULT_SEASON_ROUNDS = {2024: 24, 2025: 24, 2026: 12}


def cmd_points_backtest(args: argparse.Namespace) -> int:
    """The integrative Phase-8 gate: walk-forward expected-points MAE, rank
    correlation, top-pick hit-rate per season, benchmarked against the
    game's own ProjectedGamedayPoints (2026 only), plus the full optimiser
    backtest for 2026. 2024/2025 use reconstruct_points (Jolpica-only, no
    overtakes, no public feed) rather than faking a number that can't exist.
    """
    import json

    from f1_fantasy.predict.backtest_points import backtest_points_seasons

    seasons = [int(s) for s in args.seasons.split(",")]
    season_rounds = {season: list(range(1, DEFAULT_SEASON_ROUNDS.get(season, 24) + 1)) for season in seasons}
    print(f"walk-forward points-accuracy backtest: {season_rounds} (n_samples={args.n_samples})...")
    result = backtest_points_seasons(season_rounds, cache_dir=args.cache_dir, n_samples=args.n_samples)

    out_path = Path(args.out or "data/pace/points_backtest.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    for season, by_season in result.items():
        print(f"season {season}: rounds_evaluated={by_season['rounds_evaluated']}")
        for key, value in by_season["summary"].items():
            print(f"  {key}: {value}")
        if "optimiser_backtest" in by_season:
            print(f"  optimiser_backtest: {by_season['optimiser_backtest']['summary']}")
    print(f"written {out_path}")
    return 0


def cmd_optimiser_backtest(args: argparse.Namespace) -> int:
    """Gate: does the optimiser beat a naive and a harder baseline on
    realised points and budget growth, walk-forward against real 2026 data?
    """
    import json

    from f1_fantasy.predict.optimise import backtest_optimiser

    config = Config.load(args.config)
    rounds = list(range(args.start, args.end + 1))
    print(f"walk-forward optimiser backtest, {config.season} rounds {rounds[0]}-{rounds[-1]}...")
    result = backtest_optimiser(config.season, rounds, cache_dir=args.cache_dir)

    out_path = Path(args.out or f"data/pace/optimiser_backtest_{config.season}_r{rounds[0]}-{rounds[-1]}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    print(f"rounds evaluated: {result['rounds_evaluated']}")
    for key, value in result["summary"].items():
        print(f"  {key}: {value}")
    print(f"written {out_path}")
    return 0


def cmd_adjusted_backtest(args: argparse.Namespace) -> int:
    """Falsification test: does excluding high-confidence collision DNFs from
    reliability beat the plain grid-rank baseline, where raw reliability-
    gating (race.py, task #15) did not?
    """
    import json

    from f1_fantasy.predict.adjusted import backtest_adjusted_reliability

    config = Config.load(args.config)
    rounds = list(range(args.start, args.end + 1))
    print(f"walk-forward incident-adjusted reliability backtest, {config.season} rounds {rounds[0]}-{rounds[-1]}...")
    result = backtest_adjusted_reliability(config.season, rounds)

    out_path = Path(args.out or f"data/pace/adjusted_backtest_{config.season}_r{rounds[0]}-{rounds[-1]}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    for label in ("baseline_grid_rank", "raw_reliability_gated", "adjusted_reliability_gated"):
        print(f"{label}: {result[label]}")
    print(f"written {out_path}")
    return 0


def cmd_multi_season_backtest(args: argparse.Namespace) -> int:
    """Robustness check: do the form/reliability walk-forward numbers hold outside 2026?

    Only form.py and reliability.py are checked here -- Gate 1 (scoring) and
    Gate 2 (prices) cannot run on 2024/2025 at all, since the public fantasy
    feeds only ever carry the current season.
    """
    import json

    from f1_fantasy.predict.multi_season import backtest_seasons

    seasons = [int(s) for s in args.seasons.split(",")]
    season_rounds = {
        season: list(range(1, DEFAULT_SEASON_ROUNDS.get(season, 24) + 1)) for season in seasons
    }
    print(f"walk-forward multi-season backtest: {season_rounds}...")
    result = backtest_seasons(season_rounds)

    out_path = Path(args.out or "data/pace/multi_season_backtest.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    for season, by_season in result.items():
        print(f"season {season}:")
        for label in ("form_vs_qualifying", "baseline_grid_rank_vs_race", "reliability_gated_vs_race"):
            print(f"  {label}: {by_season[label]}")
    print(f"written {out_path}")
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
    from f1_fantasy.report import budget as budget_report
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
    budget_context = budget_report.build_budget(current, race_label=label)
    winners_context = winners_losers_report.build_winners_losers(current, previous, race_label=label)

    cards = [
        ("recap", "recap.html.j2", recap_context, recap_report.caption(recap_context)),
        ("lockout", "lockout.html.j2", lockout_context, lockout_report.caption(lockout_context)),
        ("chips", "chips.html.j2", chips_context, chips_report.caption(chips_context)),
        ("ownership", "ownership.html.j2", ownership_context, ownership_report.caption(ownership_context)),
        ("budget", "budget.html.j2", budget_context, budget_report.caption(budget_context)),
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

    refresh_token = sub.add_parser(
        "refresh-token",
        help="open a real browser window, let you log in, and capture the resulting session "
        "token (run this locally with a display, not from CI)",
    )
    refresh_token.add_argument("--timeout", type=float, default=300.0, help="seconds to wait for login (default: 300)")
    refresh_token.add_argument("--env-file", default=".env", help="local .env file to write (default: .env)")
    refresh_token.add_argument(
        "--github-repo", help="also push the token/guid to this owner/repo's GitHub secrets via `gh secret set`"
    )
    refresh_token.set_defaults(func=cmd_refresh_token)

    preview = sub.add_parser(
        "preview", help="render the preview card for the next race (no credentials needed)"
    )
    preview.add_argument("--round", type=int, help="round number (default: latest in the calendar)")
    preview.set_defaults(func=cmd_preview)

    track_upgrades = sub.add_parser(
        "track-upgrades",
        help="fetch upgrade-package news mentions, attribute to constructors/rounds, and measure before/after effect",
    )
    track_upgrades.add_argument("--start", type=int, default=1, help="first round considered available (default: 1)")
    track_upgrades.add_argument(
        "--end", type=int, help="last round considered available, inclusive (default: latest completed round)"
    )
    track_upgrades.add_argument("--window", type=int, default=3, help="rounds either side of the upgrade round to average (default: 3)")
    track_upgrades.add_argument("--out", help="output JSON path (default: data/pace/upgrades_<season>.json)")
    track_upgrades.set_defaults(func=cmd_track_upgrades)

    daily_digest = sub.add_parser(
        "daily-digest",
        help="fetch and email the daily news digest (confirmed lineup changes, rumoured lineup news, upgrades, penalties)",
    )
    daily_digest.add_argument("--round", type=int, help="round number (default: current/next in the calendar)")
    daily_digest.add_argument("--dry-run", action="store_true", help="write to disk only, never send email")
    daily_digest.set_defaults(func=cmd_daily_digest)

    record_benchmark = sub.add_parser(
        "record-benchmark",
        help="append a manually-captured external benchmark snapshot (e.g. numbers pasted off f1fantasytools.com)",
    )
    record_benchmark.add_argument("--round", type=int, required=True, help="round number this snapshot is for")
    record_benchmark.add_argument(
        "--source", default="f1fantasytools", help="source label for this snapshot (default: f1fantasytools)"
    )
    record_benchmark.add_argument("--session", help="session label, e.g. FP1/FP2/FP3/pre_quali")
    record_benchmark.add_argument(
        "--entries", required=True, help='comma-separated driver:value pairs, e.g. "VER:185,NOR:172.5"'
    )
    record_benchmark.add_argument("--note", help="free-text note to store alongside the snapshot")
    record_benchmark.set_defaults(func=cmd_record_benchmark)

    collect_benchmark_snapshot = sub.add_parser(
        "collect-benchmark-snapshot",
        help="capture the official feed's ProjectedGamedayPoints, crowd-consensus ownership, our own current estimate, and (with ANTHROPIC_API_KEY set) f1fantasytools.com's four pages, for one round",
    )
    collect_benchmark_snapshot.add_argument(
        "--round", type=int, help="round number to capture (default: current/next in the calendar)"
    )
    collect_benchmark_snapshot.add_argument("--session", help="session label, e.g. FP1/FP2/FP3/pre_quali")
    collect_benchmark_snapshot.add_argument("--cache-dir", help="directory to cache driver feeds in")
    collect_benchmark_snapshot.set_defaults(func=cmd_collect_benchmark_snapshot)

    benchmarks = sub.add_parser(
        "benchmarks", help="render the benchmark tracker card for the next race (no credentials needed)"
    )
    benchmarks.add_argument("--round", type=int, help="round number (default: current/next in the calendar)")
    benchmarks.add_argument("--cache-dir", help="directory to cache driver feeds in")
    benchmarks.add_argument("--n-samples", type=int, default=2000, help="Monte Carlo samples per driver (default: 2000)")
    benchmarks.add_argument("--league-name", help="league name to show on the card")
    benchmarks.set_defaults(func=cmd_benchmarks)

    picks = sub.add_parser("picks", help="render the picks card for the next race (no credentials needed)")
    picks.add_argument("--round", type=int, help="round number (default: current/next in the calendar)")
    picks.add_argument("--cache-dir", help="directory to cache driver feeds in")
    picks.add_argument("--n-samples", type=int, default=2000, help="Monte Carlo samples per driver (default: 2000)")
    picks.add_argument("--league-name", help="league name to show on the card")
    picks.add_argument("--email", action="store_true", help="also email the rendered card (uses config.email_to and SMTP secrets)")
    picks.set_defaults(func=cmd_picks)

    pace_backtest = sub.add_parser(
        "pace-backtest", help="backfill practice pace and evaluate it against actual results"
    )
    pace_backtest.add_argument("--start", type=int, default=1, help="first round (default: 1)")
    pace_backtest.add_argument("--end", type=int, default=12, help="last round, inclusive (default: 12)")
    pace_backtest.add_argument("--out", help="output JSON path (default: data/pace/backtest_...)")
    pace_backtest.set_defaults(func=cmd_pace_backtest)

    track_backtest = sub.add_parser(
        "track-backtest", help="backfill per-segment track pace and evaluate cross-track prediction"
    )
    track_backtest.add_argument("--start", type=int, default=1, help="first round (default: 1)")
    track_backtest.add_argument("--end", type=int, default=12, help="last round, inclusive (default: 12)")
    track_backtest.add_argument("--out", help="output JSON path (default: data/pace/track_backtest_...)")
    track_backtest.set_defaults(func=cmd_track_backtest)

    hers_backtest = sub.add_parser(
        "hers-backtest",
        help="Gate 3: does circuit clipping severity predict year-on-year team lap-time loss",
    )
    hers_backtest.add_argument("--start", type=int, default=1, help="first round, new season (default: 1)")
    hers_backtest.add_argument("--end", type=int, default=12, help="last round, new season, inclusive (default: 12)")
    hers_backtest.add_argument("--compare-season", type=int, default=2025, help="prior season to compare against (default: 2025)")
    hers_backtest.add_argument("--out", help="output JSON path (default: data/pace/hers_backtest_...)")
    hers_backtest.set_defaults(func=cmd_hers_backtest)

    tyre_asymmetry = sub.add_parser(
        "tyre-asymmetry", help="correlate constructor degradation against track corner-direction balance"
    )
    tyre_asymmetry.add_argument("--start", type=int, default=1, help="first round (default: 1)")
    tyre_asymmetry.add_argument("--end", type=int, default=12, help="last round, inclusive (default: 12)")
    tyre_asymmetry.add_argument("--out", help="output JSON path (default: data/pace/tyre_asymmetry_...)")
    tyre_asymmetry.set_defaults(func=cmd_tyre_asymmetry)

    reconcile = sub.add_parser(
        "reconcile-scoring",
        help="prove the fantasy scoring table against the game's own published points",
    )
    reconcile.add_argument("--start", type=int, default=1, help="first round (default: 1)")
    reconcile.add_argument("--end", type=int, default=12, help="last round, inclusive (default: 12)")
    reconcile.add_argument("--cache-dir", help="directory to cache driver feeds in")
    reconcile.set_defaults(func=cmd_reconcile_scoring)

    price_backtest = sub.add_parser(
        "price-backtest",
        help="check the PPM price-tier mechanism against real recorded price changes",
    )
    price_backtest.add_argument("--start", type=int, default=1, help="first round (default: 1)")
    price_backtest.add_argument("--end", type=int, default=12, help="last round, inclusive (default: 12)")
    price_backtest.add_argument("--cache-dir", help="directory to cache driver feeds in")
    price_backtest.set_defaults(func=cmd_price_backtest)

    collect_odds = sub.add_parser(
        "collect-odds", help="best-effort betting-odds snapshot for the next race (no credentials needed)"
    )
    collect_odds.add_argument("--round", type=int, help="round number (default: current/next in the calendar)")
    collect_odds.add_argument("--out", help="output JSON path (default: data/pace/odds_...)")
    collect_odds.set_defaults(func=cmd_collect_odds)

    race_backtest = sub.add_parser(
        "race-backtest",
        help="walk-forward test: does DNF-risk discounting beat the plain form-predicted grid",
    )
    race_backtest.add_argument("--start", type=int, default=1, help="first round (default: 1)")
    race_backtest.add_argument("--end", type=int, default=12, help="last round, inclusive (default: 12)")
    race_backtest.add_argument("--out", help="output JSON path (default: data/pace/race_backtest_...)")
    race_backtest.set_defaults(func=cmd_race_backtest)

    points_backtest = sub.add_parser(
        "points-backtest", help="the integrative gate: walk-forward expected-points accuracy per season"
    )
    points_backtest.add_argument("--seasons", default="2024,2025,2026", help="comma-separated seasons (default: 2024,2025,2026)")
    points_backtest.add_argument("--cache-dir", help="directory to cache driver feeds in")
    points_backtest.add_argument("--n-samples", type=int, default=500, help="Monte Carlo samples per round (default: 500)")
    points_backtest.add_argument("--out", help="output JSON path (default: data/pace/points_backtest.json)")
    points_backtest.set_defaults(func=cmd_points_backtest)

    optimiser_backtest = sub.add_parser(
        "optimiser-backtest", help="does the team optimiser beat a naive and a harder baseline on realised outcomes"
    )
    optimiser_backtest.add_argument("--start", type=int, default=1, help="first round (default: 1)")
    optimiser_backtest.add_argument("--end", type=int, default=12, help="last round, inclusive (default: 12)")
    optimiser_backtest.add_argument("--cache-dir", help="directory to cache driver feeds in")
    optimiser_backtest.add_argument("--out", help="output JSON path (default: data/pace/optimiser_backtest_...)")
    optimiser_backtest.set_defaults(func=cmd_optimiser_backtest)

    adjusted_backtest = sub.add_parser(
        "adjusted-backtest",
        help="does excluding high-confidence collision DNFs from reliability beat the grid-rank baseline",
    )
    adjusted_backtest.add_argument("--start", type=int, default=1, help="first round (default: 1)")
    adjusted_backtest.add_argument("--end", type=int, default=12, help="last round, inclusive (default: 12)")
    adjusted_backtest.add_argument("--out", help="output JSON path (default: data/pace/adjusted_backtest_...)")
    adjusted_backtest.set_defaults(func=cmd_adjusted_backtest)

    multi_season_backtest = sub.add_parser(
        "multi-season-backtest",
        help="check whether the form/reliability walk-forward numbers hold outside 2026",
    )
    multi_season_backtest.add_argument(
        "--seasons", default="2024,2025,2026", help="comma-separated seasons (default: 2024,2025,2026)"
    )
    multi_season_backtest.add_argument("--out", help="output JSON path (default: data/pace/multi_season_backtest.json)")
    multi_season_backtest.set_defaults(func=cmd_multi_season_backtest)

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
