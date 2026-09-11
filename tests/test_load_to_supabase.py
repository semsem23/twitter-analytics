"""Tests unitaires pour load_to_supabase.py — le client Supabase est mocké."""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.load_to_supabase import _get_client, load_to_supabase

COLUMNS = ["tweet_id", "created_at", "text", "likes", "retweets", "replies", "impressions"]


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "tweet_id": "1",
                "created_at": pd.Timestamp("2026-09-08T10:00:00Z"),
                "text": "Breaking news update",
                "likes": 10,
                "retweets": 2,
                "replies": 1,
                "impressions": 500,
            }
        ],
        columns=COLUMNS,
    )


@patch("src.load_to_supabase._get_client")
def test_load_to_supabase_inserts_rows_with_extracted_at(mock_get_client):
    mock_table = MagicMock()
    mock_table.insert.return_value.execute.return_value = MagicMock(data=[{"tweet_id": "1"}])
    mock_client = MagicMock()
    mock_client.table.return_value = mock_table
    mock_get_client.return_value = mock_client

    inserted_count = load_to_supabase(_sample_df())

    assert inserted_count == 1
    mock_client.table.assert_called_once_with("tweet_metrics_raw")

    payload = mock_table.insert.call_args[0][0]
    assert len(payload) == 1
    row = payload[0]
    assert row["tweet_id"] == "1"
    assert row["likes"] == 10
    assert "extracted_at" in row
    assert isinstance(row["created_at"], str)  # sérialisé pour le payload JSON


@patch("src.load_to_supabase._get_client")
def test_load_to_supabase_empty_dataframe_skips_insert(mock_get_client):
    empty_df = pd.DataFrame(columns=COLUMNS)

    inserted_count = load_to_supabase(empty_df)

    assert inserted_count == 0
    mock_get_client.assert_not_called()


def test_get_client_raises_without_env_vars(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)

    with pytest.raises(RuntimeError):
        _get_client()
