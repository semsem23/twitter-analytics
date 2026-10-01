"""Tests du parsing des exports X "Account overview" (aucun appel réseau)."""

from datetime import date

import pandas as pd
import pytest

from scripts.load_x_account_analytics import METRIC_COLUMNS, parse_export

HEADER = (
    "Date,Impressions,Likes,Engagements,Bookmarks,Shares,New follows,Unfollows,"
    "Replies,Reposts,Profile visits,Create Post,Video views,Media views\n"
)


def _write(tmp_path, body: str):
    path = tmp_path / "account_overview_analytics.csv"
    path.write_text(HEADER + body, encoding="utf-8")
    return path


def test_parse_export_normalises_dates_columns_and_freshness(tmp_path):
    path = _write(
        tmp_path,
        '"Sun, Sep 27, 2026",398,0,0,0,0,0,0,0,0,0,5,0,0\n'
        '"Fri, Sep 25, 2026",205,1,1,0,0,0,1,0,0,0,7,0,0\n',
    )

    df = parse_export(path)

    assert df["metric_date"].tolist() == [date(2026, 9, 25), date(2026, 9, 27)]  # trié
    assert set(METRIC_COLUMNS) <= set(df.columns)
    row = df.iloc[0]
    assert row["impressions"] == 205
    assert row["unfollows"] == 1
    assert row["posts_created"] == 7
    assert (df["export_max_date"] == date(2026, 9, 27)).all()
    assert (df["source_file"] == "account_overview_analytics.csv").all()


def test_parse_export_rejects_duplicate_days(tmp_path):
    path = _write(
        tmp_path,
        '"Sun, Sep 27, 2026",398,0,0,0,0,0,0,0,0,0,5,0,0\n'
        '"Sun, Sep 27, 2026",1,0,0,0,0,0,0,0,0,0,5,0,0\n',
    )

    with pytest.raises(ValueError, match="même jour"):
        parse_export(path)


def test_parse_export_rejects_other_export_types(tmp_path):
    path = tmp_path / "account_analytics_content.csv"
    path.write_text("Post id,Date,Post text\n1,\"Wed, Aug 19, 2026\",hello\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Account overview"):
        parse_export(path)


def test_parse_export_reads_xlsx_saved_from_excel(tmp_path):
    csv_path = _write(tmp_path, '"Sun, Sep 27, 2026",398,0,0,0,0,0,0,0,0,0,5,0,0\n')
    df_csv = pd.read_csv(csv_path)
    df_csv["Date"] = pd.to_datetime(df_csv["Date"], format="%a, %b %d, %Y")  # Excel stocke une vraie date
    xlsx_path = tmp_path / "account_overview_analytics.xlsx"
    df_csv.to_excel(xlsx_path, index=False)

    df = parse_export(xlsx_path)

    assert df["metric_date"].tolist() == [date(2026, 9, 27)]
    assert df.iloc[0]["impressions"] == 398


def test_parse_export_explains_csv_resaved_by_excel(tmp_path):
    path = _write(tmp_path, "27/09/2026,398,0,0,0,0,0,0,0,0,0,5,0,0\n")

    with pytest.raises(ValueError, match="xlsx"):
        parse_export(path)
