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

    preview = sub.add_parser(
        "preview", help="render the preview card for the next race (no credentials needed)"
    )
    preview.add_argument("--round", type=int, help="round number (default: latest in the calendar)")
    preview.set_defaults(func=cmd_preview)

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

    race_backtest = sub.add_parser(
        "race-backtest",
        help="walk-forward test: does DNF-risk discounting beat the plain form-predicted grid",
    )
    race_backtest.add_argument("--start", type=int, default=1, help="first round (default: 1)")
    race_backtest.add_argument("--end", type=int, default=12, help="last round, inclusive (default: 12)")
    race_backtest.add_argument("--out", help="output JSON path (default: data/pace/race_backtest_...)")
    race_backtest.set_defaults(func=cmd_race_backtest)

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
