"""Regroupement des séries quotidiennes par jour / semaine / mois pour le dashboard.

Les marts dbt sont au grain jour (tweet_metrics_daily, stg_account_daily_metrics) ;
la granularité choisie dans Streamlit est appliquée ici, en pandas, pour ne pas
multiplier les modèles. Module sans dépendance à Streamlit : testable seul.
"""

from __future__ import annotations

import pandas as pd

GRANULARITIES = ("Jour", "Semaine", "Mois")


def period_start(dates: pd.Series, granularity: str) -> pd.Series:
    """Début de période de chaque date : le jour, le lundi de la semaine ou le 1er du mois."""
    days = pd.to_datetime(dates).dt.normalize()
    if granularity == "Jour":
        return days
    if granularity == "Semaine":
        return days - pd.to_timedelta(days.dt.weekday, unit="D")
    if granularity == "Mois":
        return days.dt.to_period("M").dt.to_timestamp()
    raise ValueError(f"Granularité inconnue : {granularity!r}")


def period_end(starts: pd.Series, granularity: str) -> pd.Series:
    """Dernier jour (inclus) de chaque période."""
    if granularity == "Jour":
        return starts
    if granularity == "Semaine":
        return starts + pd.Timedelta(days=6)
    return starts + pd.offsets.MonthEnd(0)


def aggregate_by_period(df: pd.DataFrame, date_column: str, granularity: str, sum_columns: list[str]) -> pd.DataFrame:
    """Somme `sum_columns` par période.

    Retourne period_start, period_end, les sommes et `partial` : True quand la
    période déborde de la plage couverte par les données (première ou dernière
    semaine / mois incomplets) — à signaler, pas à comparer tel quel.
    """
    if df.empty:
        return pd.DataFrame(columns=["period_start", "period_end", *sum_columns, "partial"])

    dates = pd.to_datetime(df[date_column]).dt.normalize()
    grouped = (
        df.assign(period_start=period_start(dates, granularity))
        .groupby("period_start", as_index=False)[sum_columns]
        .sum()
        .sort_values("period_start")
        .reset_index(drop=True)
    )
    grouped["period_end"] = period_end(grouped["period_start"], granularity)
    grouped["partial"] = (grouped["period_start"] < dates.min()) | (grouped["period_end"] > dates.max())
    return grouped[["period_start", "period_end", *sum_columns, "partial"]]


def rate(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """Taux en % arrondi à 2 décimales, vide (NaN) quand le dénominateur est nul."""
    return (numerator / denominator.where(denominator != 0) * 100).astype(float).round(2)
