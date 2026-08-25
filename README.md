# F1 Fantasy League Reporter

Pulls your private F1 Fantasy leagues each race weekend and turns them into
designed graphics for the league WhatsApp chat: standings, chip usage, who
changed what at lockout, whose moves paid off, and a pre-race preview with pace
and news.

Reports are rendered as PNGs and emailed to you to forward into the chat.
Nothing posts to WhatsApp automatically — see [Why not auto-post](#why-not-auto-post).

## Status

| Phase | State |
| --- | --- |
| 0 — Access spike (`probe`) | **Confirmed live: full access to other members' teams** |
| 1 — API client, models, snapshot store | Done, confirmed against the live API |
| 2 — Diff engine | Done |
| 3 — Render pipeline + cards | Recap and lockout done; preview, chips, ownership remaining |
| 4 — Actions automation + email | Done; `probe` runs green in Actions |
| 5 — News and articles | Not started |
| 6 — Pace dataset (FastF1) | Not started |

## Getting started

```bash
pip install -e ".[dev]"
playwright install chromium          # skip if a Chromium is already provided
cp config.example.toml config.toml   # then edit
```

### Credentials

F1 accounts sit behind Imperva bot protection, so scripted password login is not
reliable. Instead, capture a session cookie by hand:

1. Open <https://fantasy.formula1.com> and log in.
2. DevTools (F12) → **Network**.
3. Reload, and find the request to `/services/session/login`.
4. From its **Response**, copy `Token` and `GUID`.

```bash
export F1_FANTASY_TOKEN="<Token>"
export F1_USER_GUID="<GUID>"
```

**The token lasts roughly five days.** That is the main operating cost of this
tool. When it expires the CLI says so explicitly and prints these steps again,
rather than failing with a stack trace.

### First run

```bash
f1-fantasy probe
```

`probe` answers the question the whole design depends on: **can this account read
other league members' teams?** Confirmed live (2026-08-25): **yes, full access** —
every report in the plan is possible. Re-run it any time credentials change or
you want to sanity-check a league; it's also how three real payload-shape bugs
in the parsers got caught and fixed on first contact with the live API (see
"Endpoints confirmed live" below).

### Seeing a card without credentials

```bash
f1-fantasy demo
```

Renders the recap card from synthetic data to `out/demo/`. Useful for judging
layout without waiting for a race weekend.

## Commands

| Command | What it does |
| --- | --- |
| `probe` | Check how much league data is readable |
| `doctor` | Validate credentials, config and delivery |
| `capture --phase {pre_lock,locked,final}` | Write a snapshot of each league |
| `plan` | Print what the calendar says is due (used by CI to gate) |
| `tick [--force X] [--dry-run]` | Run whatever is due |
| `demo` | Render cards from synthetic data |

## How it works

The API only ever returns *current* state, so "what changed since last race" can
only be answered from history the tool captured itself. Snapshots are plain JSON
committed to the repo:

```
snapshots/{season}/{league_id}/{race_id}/{phase}.json
```

Three phases per race — `pre_lock`, `locked`, `final` — feed two derived reports:

- **Lockout report** — diff this race's locked teams against last race's:
  transfers, captain changes, chips played, team value movement.
- **Winners and losers** — once points land, value every player moved in against
  every player moved out.

On scoring transfers: when someone makes a single swap it is fair to say "X out,
Y in, +14". When they make five on a wildcard there is no principled way to say
which incoming player replaced which outgoing one, so the delta is always
computed in aggregate and the head-to-head framing is only used when it is
genuinely one swap.

**What is and isn't backfillable:** standings history comes back via
`getusergamedaysv1`, which is keyed by race across the whole season. Team-change
history does **not** — the API exposes no past lineups. Change reports start
accumulating from the first run onward.

### Endpoints confirmed live

The API is undocumented; every field name and envelope shape below started as
an inference from a third-party client's source, and every one of the first
three turned out to need a correction on first contact with the real service —
tracked here rather than left as folklore.

| Endpoint | Envelope | Confirmed |
| --- | --- | --- |
| `getusergamedaysv1` | `Data.Value` is a **bare list**, not `{"data": [...]}` | Live, 2026-08-25 |
| `.../1/0/0/privateleague` | `Data.Value.Details` — mixes in F1's own promotional league (`isJoined: 0`), filtered out | Live, 2026-08-25 |
| `.../getuserleague/1` | `Data.Value.leaguesdata` — its own distinct misspelling, `memeberCount` | Live, 2026-08-25 |
| `pvtleagueuserrankget` (leaderboard) | `Data.Value.{leagueInfo, memRank}` | Live, 2026-08-25 — the "full access" verdict requires this to have parsed real members |
| `getteam`, own team and every other member's | `Data.Value.{mdid, userTeam}` | Live, 2026-08-25 — same verdict; every member in the league came back readable |
| `/feeds/drivers/{race}_en.json` | Public, `Value` is a list of player dicts | Not exercised by `probe` — first real test is the first rendered card against live data |

`probe` deliberately dumps raw JSON to the Actions log when a result looks
suspicious (zero leagues, zero members, zero picks) rather than trusting an
empty parse — that's what caught the first three. If a future run hits one of
the "not yet confirmed" rows and something looks off, the same dump will show
up in the log; update this table and the corresponding parser together.

## Design notes

Cards render to fixed PNGs for a chat thread, so there is one committed look
rather than light/dark theming.

Constructor colours are used **only** as identity rails and dots beside a written
team name, never to encode a value. That is measured, not a preference: run as a
series palette, the ten team colours fail CVD separation badly — Haas grey against
Alpine pink is deutan ΔE 1.0, effectively identical, and Haas against Williams is
11.8 even with full colour vision. Magnitude uses one sequential blue ramp;
gains and losses use a diverging pair and always carry a sign or arrow so
direction never rests on colour alone.

Fonts (Barlow Condensed, Inter) are vendored in `assets/` and inlined as data
URIs at render time, so a card looks identical on a laptop and on a CI runner.

## Why not auto-post

Meta's official WhatsApp Groups API only covers groups the business itself
creates, capped at 8 members — it cannot post into an existing league chat.
Everything that can (Baileys, whatsapp-web.js, hosted gateways) drives a real
WhatsApp account against the Web protocol, violates the ToS, and risks a ban on
the number.

So delivery is human-in-the-loop: the tool emails you the PNG and a ready-to-paste
caption, and you forward it. The publisher sits behind an interface, so an
auto-posting adapter could be added later without touching report code.

## Privacy

Snapshots and rendered cards contain your league members' real names.
**Keep this repository private.** If it is ever made public, strip `snapshots/`
and `out/` from history first.

## Tests

```bash
pytest
```

All 76 run offline against fixtures — no live API, no credentials. The
parser tests deliberately reproduce the API's real payloads including its
misspellings (`FUllName`, `OverallPpints`, `isnonigativetaken`) and its habit of
sending numbers as strings; if the API changes shape, those break first.

## Scheduling

One hourly workflow that asks the race calendar what is due, rather than a cron
line per report. Sessions get rescheduled, sprint weekends lock a day earlier at
the Sprint rather than at Qualifying, and clocks shift twice a year — a fixed
schedule quietly reports at the wrong time through all of that.

| Report | Fires |
| --- | --- |
| Pace | After final practice, before lockout |
| Preview | ~24h before lockout |
| Lockout | 15 min after lockout, letting last-second edits settle |
| Recap | Once race points stop moving between reads |

Windows close after a grace period, so a tool that was offline for a week does
not wake up and post a preview for a race that has already run. Actions are
recorded in `state.json` only on success, so a failure stays due and retries on
the next tick.

The recap waits for two consecutive identical point reads rather than assuming a
fixed delay after the flag: penalties and classification changes move fantasy
points hours later, and a recap published from provisional numbers has to be
corrected in the chat.

### Repository secrets

| Secret | Purpose |
| --- | --- |
| `F1_FANTASY_TOKEN` | Session cookie (refresh ~every 5 days) |
| `F1_USER_GUID` | Your account guid |
| `SMTP_USER` / `SMTP_PASS` | Gmail address and **app password** |
| `SMTP_HOST` / `SMTP_PORT` | Optional, default to Gmail |

When a run fails on authentication the workflow opens (or comments on) an issue
with the refresh steps, rather than leaving a red X in the Actions tab — an
expired cookie is a routine five-day event, not a crash.
