"""Multi-season wiring, against synthetic per-season data.

The real walk-forward numbers that motivated this module's docstring (form
baseline 0.898 on 2026 vs 0.714/0.702 on 2024/2025; reliability-gating flat
or negative in all three) come from a real, separately-run backtest against
live Jolpica data -- these tests only check that backtest_season assembles
the three sub-backtests correctly, not those numbers.
"""

from __future__ import annotations

import pytest

from f1_fantasy.predict import multi_season


def test_backtest_season_reports_all_three_components(monkeypatch):
    from f1_fantasy.results import QualifyingResult

    def fake_fetch_qualifying(season, round_number):
        # A round where A < B < C in pace, consistent every round. (At
        # least 3 common drivers are needed for score_predictions to return
        # a correlation rather than None.)
        return [
            QualifyingResult(driver_code="A", driver_name="A", constructor="Team", position=1),
            QualifyingResult(driver_code="B", driver_name="B", constructor="Team", position=2),
            QualifyingResult(driver_code="C", driver_name="C", constructor="Team", position=3),
        ]

    monkeypatch.setattr(multi_season, "fetch_qualifying", fake_fetch_qualifying)
    monkeypatch.setattr(multi_season, "rolling_form", lambda season, rounds: {"A": 0.0, "B": 1.0, "C": 2.0})
    monkeypatch.setattr(
        multi_season, "predict_grid_rank", lambda season, train, target: {"A": 1.0, "B": 2.0, "C": 3.0}
    )
    monkeypatch.setattr(
        multi_season, "predict_race_order", lambda season, train, target: {"A": 1.0, "B": 2.0, "C": 3.0}
    )
    monkeypatch.setattr(multi_season, "actual_race_positions", lambda season, r: {"A": 1, "B": 2, "C": 3})

    result = multi_season.backtest_season(2099, [1, 2, 3])

    assert result["season"] == 2099
    assert set(result) == {"season", "form_vs_qualifying", "baseline_grid_rank_vs_race", "reliability_gated_vs_race"}
    # Both sides agree every round -- a perfect walk-forward score throughout.
    assert result["form_vs_qualifying"]["mean_spearman"] == pytest.approx(1.0)
    assert result["baseline_grid_rank_vs_race"]["mean_spearman"] == pytest.approx(1.0)
    assert result["reliability_gated_vs_race"]["mean_spearman"] == pytest.approx(1.0)


def test_backtest_seasons_keys_results_by_season(monkeypatch):
    monkeypatch.setattr(multi_season, "backtest_season", lambda season, rounds: {"season": season, "rounds": rounds})

    result = multi_season.backtest_seasons({2024: [1, 2], 2026: [1]})

    assert result == {2024: {"season": 2024, "rounds": [1, 2]}, 2026: {"season": 2026, "rounds": [1]}}
