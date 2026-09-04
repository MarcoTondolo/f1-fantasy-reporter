# Formula Data Analysis: weekly review catalogue

**Source:** Mirco Bartolozzi ("Formula Data Analysis" — performance engineer, PhD
in vehicle dynamics), publishing telemetry breakdowns most race weekends to
Telegram (`t.me/s/FDataAn`, publicly readable without login), Bluesky, X,
Threads, Facebook, and a subreddit. Catalogued during Phase 7 research
(~43 posts across the 2026 season reviewed at the time) and reproduced here as
a standing project asset rather than a one-off research note.

**How to use this each race weekend:**
1. Check `t.me/s/FDataAn` (and/or Bluesky) for that weekend's posts.
2. Match each post to a row below by analysis type.
3. If it's a type this project already implements ("Status" column), compare
   his numbers against ours as a sanity check — agreement is reassuring,
   disagreement is worth digging into (wrong on our side, wrong on his, or a
   real methodological difference worth understanding).
4. If it's a type this project doesn't implement, use the "Reproducible from
   FastF1" and "Fantasy value" columns to judge whether it's worth building,
   or just useful as read-only weekend context (captain/pick calls, not
   automated into `predict/points.py`).
5. A "null result" status below is not a reason to skip re-checking that
   type — a hypothesis that failed on 12 rounds of 2026 data could read
   differently with more data, or on a specific circuit type.

**Reading the Status column:** "Built (gated)" means implemented, backtested,
and reported as a real, working signal. "Built (null)" means implemented and
backtested, but the falsification test failed — kept in the codebase as an
honest negative result, not wired into predictions. "Partial" means a piece
of the mechanism exists but not the specific analysis Bartolozzi does. "Not
built" is exactly that.

| # | Analysis | Reproducible from FastF1? | Status in this project | Fantasy value |
|---|---|---|---|---|
| 1 | Race pace analysis, corrected for extra tyre sets / traffic | Yes | Partial — `pace/features.py::_long_run_pace` computes fuel/degradation-corrected long-run pace, feeding `pace/model.py`; not traffic-corrected the way his is, and the long-run-pace signal was one of the falsified nulls in the multi-season robustness pass | High — his correction is exactly what our null lacked |
| 2 | Long runs by compound, length, consistency | Yes | Built (null) — `pace/features.py::_long_run_pace`, `pace/tyre_asymmetry.py` | High — direct race-pace signal |
| 3 | Clipping (full-throttle yet decelerating = energy depletion) | Yes — Speed + Throttle channels | **Built (gated)** — `pace/energy.py` (detection), `pace/hers.py` (Gate 3: circuit energy-demand × team PU-efficiency hypothesis, confirmed) | Very high — the one hypothesis here that actually beat baseline |
| 4 | ERS deployment/harvesting strategy (e.g. harvesting at full throttle below 150 km/h) | Partly — inferable from accel-vs-speed curves | Not built as its own analysis — `pace/energy.py`'s clipping detection is adjacent but doesn't classify harvest vs. deploy | High — explains circuit-specific team strength beyond clipping alone |
| 5 | Time at maximum throttle | Yes — `Throttle == 100` duration | Built — `pace/energy.py` | High — the energy-demand half of the clipping interaction |
| 6 | Top speed / top speed per lap | Yes | Partial — available via FastF1 telemetry already loaded by `pace/sessions.py`, not surfaced as its own feature | Medium — engine-mode/drag proxy |
| 7 | Aero performance (top speed vs. corner minimum speed) | Yes — segments already built | Partial — `pace/track_profile.py`/`pace/segments.py` classify corners by speed band; the explicit top-speed-vs-corner-speed trade isn't computed as one metric | High — downforce/drag trade-off per circuit |
| 8 | Race start & acceleration times (0–100 km/h), anti-stall detection | Yes — lap-1 speed trace | Built (Gate A: weak/inconclusive) — `pace/starts.py` | High in principle — feeds `POINTS_PER_PLACE_GAINED` directly, but our own backtest didn't find a clean signal yet |
| 9 | Tyre degradation by compound | Yes | Built — `pace/tyre_asymmetry.py`, `pace/features.py` | High — pairs with long-run work |
| 10 | Stints / tyre strategy | Yes | Not built — deliberately deferred ("strategy is post-lockout", i.e. decided after teams' fantasy-relevant picks lock) | Medium |
| 11 | Minisector dominance (% of lap fastest, gain when fastest) | Yes — extends existing segments | Not built — `pace/segments.py` does track-segment classification, not per-minisector fastest-driver attribution | Medium |
| 12 | Gap to race winner | Yes | Not built as a feature (purely descriptive) | Low |
| 13 | Year-on-year telemetry, same circuit (season N vs. N-1) | Yes | **Built (gated)** — `pace/hers.py`'s Gate 3 falsification test, confirmed (Spa #1/12 clipping severity, Monaco #12/12, Mercedes smallest Monaco YoY loss — matches his published numbers) | Very high — the mechanistic explanation for circuit-specific team form |
| 14 | Engine development across eras (e.g. 2022→25 ≈ 2.28s/lap average) | Yes | Superseded — an earlier flat era-offset model was weak (r=0.20) and was replaced by the H-ERS mechanism (#13) rather than kept alongside it | High — quantifies the era correction, but H-ERS explains *why* it varies by team/circuit instead of assuming one flat number |
| 15 | Turn-specific minimum speed | Yes | Partial — corner-level data exists via `pace/track_profile.py`; not surfaced as a standalone per-turn comparison | Medium |
| 16 | Gear ratio / RPM analysis (e.g. running below the fuel-flow threshold) | Yes — `nGear`, `RPM` | Not built | Low — fascinating, too fine-grained for fantasy relevance |
| 17 | Power rankings (his own predictions) | N/A — human judgement | Not built — no page to scrape/screenshot; would need the same manual-capture pattern as `record-benchmark` if tracked | Useful only as an external benchmark, same caveat as every other external source in `report/benchmarks.py` |

## Gaps worth closing, roughly in fantasy-value order

1. **ERS harvesting classification (#4)** — the natural next layer on top of
   the already-gated clipping work (#3); would sharpen circuit-specific team
   strength beyond what H-ERS alone captures.
2. **Aero trade-off as one metric (#7)** — the pieces (top speed, corner
   speed) already exist separately; combining them into the actual trade-off
   ratio he reports is a small addition, not a new data source.
3. **Minisector dominance (#11)** — same story: `pace/segments.py` almost
   gets there.
4. **Stints/strategy (#10)** — deliberately out of scope for the *pre-lock*
   prediction problem this project is built around; would only matter for a
   live-race feature, which `report/benchmarks.py`'s own CAVEAT already says
   this project isn't building (duplicates the official app).

## What's already ahead of the catalogue

Row #3 and #13 (clipping and year-on-year telemetry) are the two hypotheses
this project actually built, gated, and confirmed against his own published
numbers — not just reproduced his method, but used it to explain a real gap
(the weak r=0.20 prior-year-circuit-affinity baseline) that his article
predicted the mechanism for. That's the one item on this list where the
project's own work has gone past "review his post," which is worth
remembering when using this table: it's a checklist for staying current, not
evidence that everything here is still unexplored.
