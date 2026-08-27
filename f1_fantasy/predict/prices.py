"""Price-change prediction: Points Per Million (PPM) tiers.

**Sourced, not fitted**: unlike every other model in this project, these
thresholds were not reverse-engineered from scratch -- they are the publicly
documented F1 Fantasy price algorithm (Gridside and Into The Chicane both
describe the same mechanism independently, and agree with each other), taken
as given and then checked against this project's own captured data rather
than derived blind.

A round's PPM is that round's fantasy points divided by the asset's price at
the time. Average PPM -- the mean of that ratio over the rounds immediately
before the one being predicted -- maps to one of four ratings (terrible /
poor / good / great), and that rating combines with the asset's own price
tier to give the delta: assets priced at or above $18.5M ("premium") move in
$0.1M/$0.3M steps, assets below it ("budget") in $0.2M/$0.6M steps. A price
at or below the $3.0M floor cannot fall further.

**Checked, not assumed**: validated against all of 2026's GamedayPoints /
OldPlayerValue / Value from the public driver feed (232 driver-rounds,
excluding round 1 -- which never changes price for anyone -- and Lawson,
whose feed entries are independently known to be inconsistent, see
reconcile.py). 91.4% (212/232) of predicted tier deltas match the real
recorded change exactly.

One deliberate departure from the public write-ups: they describe the first
two rounds as blended with synthetic zero-point "phantom" prior races. The
real data fits *better* without that padding -- averaging over however many
real rounds actually exist, rather than always dividing by 3 -- so that is
what ``average_ppm`` does. The remaining ~9% of mismatches have no single
explanation found so far (not concentrated in early rounds, sprint weekends,
or floor cases, which are each handled explicitly); reported as a residual
rather than explained away.
"""

from __future__ import annotations

from pathlib import Path

from f1_fantasy.predict.reconcile import KNOWN_INCONSISTENT_DRIVERS, fetch_driver_feed

#: terrible < 0.6 <= poor < 0.9 <= good < 1.2 <= great
PPM_THRESHOLDS = (0.6, 0.9, 1.2)

#: Assets priced at or above this move in the smaller ("premium") steps.
PRICE_TIER_BOUNDARY = 18.5

#: An asset at or below this price cannot drop further.
PRICE_FLOOR = 3.0

#: Rounds of prior history averaged into a PPM rating.
ROLLING_WINDOW = 3

#: (premium delta, budget delta) in $M, per rating.
TIER_DELTAS: dict[str, tuple[float, float]] = {
    "terrible": (-0.3, -0.6),
    "poor": (-0.1, -0.2),
    "good": (0.1, 0.2),
    "great": (0.3, 0.6),
}


def rating_for_ppm(avg_ppm: float) -> str:
    lo, mid, hi = PPM_THRESHOLDS
    if avg_ppm < lo:
        return "terrible"
    if avg_ppm < mid:
        return "poor"
    if avg_ppm < hi:
        return "good"
    return "great"


def average_ppm(recent_points_and_prices: list[tuple[float, float]]) -> float:
    """Mean per-round PPM over however many rounds are supplied (<= 3).

    Deliberately divides by the number of rounds actually given, not a fixed
    3 -- see the module docstring for why.
    """
    if not recent_points_and_prices:
        return 0.0
    ratios = [points / price if price else 0.0 for points, price in recent_points_and_prices]
    return sum(ratios) / len(ratios)


def predict_price_delta(avg_ppm: float, price_before: float) -> float:
    """The tiered price change this rating/price-tier combination produces."""
    premium = price_before >= PRICE_TIER_BOUNDARY
    delta = TIER_DELTAS[rating_for_ppm(avg_ppm)][0 if premium else 1]
    if price_before <= PRICE_FLOOR and delta < 0:
        return 0.0
    return delta


#: driver_code -> {round_number: (gameday_points, price_before, price_after)}
DriverPriceHistory = dict[str, dict[int, tuple[float, float, float]]]


def round_history(
    rounds: list[int], *, cache_dir: Path | str | None = None
) -> DriverPriceHistory:
    """Fetch and key the public driver feed's points/price fields by round."""
    history: DriverPriceHistory = {}
    for round_number in rounds:
        feed = fetch_driver_feed(round_number, cache_dir=cache_dir)
        for code, row in feed.items():
            if code in KNOWN_INCONSISTENT_DRIVERS:
                continue
            points = float(row.get("GamedayPoints") or 0)
            price_before = float(row.get("OldPlayerValue") or 0)
            price_after = float(row.get("Value") or 0)
            history.setdefault(code, {})[round_number] = (points, price_before, price_after)
    return history


def predict_round(history: dict[int, tuple[float, float, float]], target_round: int) -> float | None:
    """Predicted price delta for *target_round*, from strictly earlier rounds.

    Round 1 never changes price for any driver -- there is no history yet to
    base a change on -- so it is always 0.0 rather than looked up.
    """
    if target_round == 1:
        return 0.0
    window = [r for r in range(target_round - ROLLING_WINDOW, target_round) if r in history and r >= 1]
    if not window:
        return None
    if target_round in history:
        price_before = history[target_round][1]
    else:
        price_before = history[max(window)][2]
    recent = [(history[r][0], history[r][1]) for r in window]
    return predict_price_delta(average_ppm(recent), price_before)


def backtest_prices(rounds: list[int], *, cache_dir: Path | str | None = None) -> dict:
    """How often the predicted tier delta matches the real recorded change.

    Round 1 is excluded -- it is a fixed rule, not a prediction -- so results
    only cover rounds 2 onward.
    """
    history = round_history(rounds, cache_dir=cache_dir)
    matches = 0
    mismatches: list[dict] = []
    total = 0
    for code, by_round in history.items():
        for round_number, (_, price_before, price_after) in by_round.items():
            if round_number == 1:
                continue
            predicted = predict_round(by_round, round_number)
            if predicted is None:
                continue
            actual = round(price_after - price_before, 3)
            total += 1
            if abs(actual - predicted) < 0.05:
                matches += 1
            else:
                mismatches.append(
                    {"driver": code, "round": round_number, "actual": actual, "predicted": predicted}
                )
    return {
        "total": total,
        "matches": matches,
        "match_rate": matches / total if total else None,
        "mismatches": mismatches,
    }
