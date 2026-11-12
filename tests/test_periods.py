"""Tests du regroupement jour / semaine / mois du dashboard (pandas pur)."""

import pandas as pd
import pytest

from app.periods import aggregate_by_period, rate


def _daily(start: str, end: str) -> pd.DataFrame:
    days = pd.date_range(start, end, freq="D")
    return pd.DataFrame({"day": days.date, "impressions": range(1, len(days) + 1)})


def test_day_granularity_keeps_one_row_per_day_and_nothing_partial():
    out = aggregate_by_period(_daily("2026-09-21", "2026-09-27"), "day", "Jour", ["impressions"])

    assert len(out) == 7
    assert out["impressions"].tolist() == [1, 2, 3, 4, 5, 6, 7]
    assert not out["partial"].any()


def test_week_granularity_starts_on_monday_and_flags_incomplete_edges():
    # Mer. 23/09 -> mar. 06/10 : semaine du 21/09 entamée, du 28/09 complète, du 05/10 entamée.
    out = aggregate_by_period(_daily("2026-09-23", "2026-10-06"), "day", "Semaine", ["impressions"])

    assert [d.strftime("%Y-%m-%d") for d in out["period_start"]] == ["2026-09-21", "2026-09-28", "2026-10-05"]
    assert all(d.weekday() == 0 for d in out["period_start"])
    assert out["partial"].tolist() == [True, False, True]
    assert out["impressions"].sum() == sum(range(1, 15))  # rien perdu ni compté deux fois


def test_month_granularity_groups_by_calendar_month():
    out = aggregate_by_period(_daily("2026-06-29", "2026-09-30"), "day", "Mois", ["impressions"])

    assert [d.strftime("%Y-%m") for d in out["period_start"]] == ["2026-06", "2026-07", "2026-08", "2026-09"]
    assert out["period_end"].iloc[1].strftime("%Y-%m-%d") == "2026-07-31"
    assert out["partial"].tolist() == [True, False, False, False]  # juin commence le 29


def test_sparse_days_are_summed_into_their_period():
    # Jours sans tweet absents du mart : la semaine somme ce qui existe.
    df = pd.DataFrame({"day": pd.to_datetime(["2026-09-21", "2026-09-24", "2026-09-28"]).date, "tweets": [3, 2, 5]})

    out = aggregate_by_period(df, "day", "Semaine", ["tweets"])

    assert out["tweets"].tolist() == [5, 5]


def test_empty_input_returns_empty_frame_with_expected_columns():
    out = aggregate_by_period(pd.DataFrame(columns=["day", "x"]), "day", "Mois", ["x"])

    assert out.empty
    assert list(out.columns) == ["period_start", "period_end", "x", "partial"]


def test_unknown_granularity_is_rejected():
    with pytest.raises(ValueError):
        aggregate_by_period(_daily("2026-09-21", "2026-09-22"), "day", "Trimestre", ["impressions"])


def test_rate_is_percentage_and_blank_when_no_impressions():
    out = rate(pd.Series([2, 1]), pd.Series([400, 0]))

    assert out.iloc[0] == 0.5
    assert pd.isna(out.iloc[1])
