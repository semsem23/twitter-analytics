"""Tests de la logique d'idempotence du script de migration (aucun appel réseau)."""

import pandas as pd

from scripts.migrate_supabase_to_snowflake import rows_to_insert


def _supabase_rows() -> pd.DataFrame:
    # Formats tels que renvoyés par PostgREST : précision variable, offset +00:00.
    return pd.DataFrame(
        [
            {"tweet_id": "1", "extracted_at": "2026-09-14T04:20:13.123456+00:00", "likes": 1},
            {"tweet_id": "2", "extracted_at": "2026-09-14T04:20:13.1234+00:00", "likes": 2},
            {"tweet_id": "1", "extracted_at": "2026-09-21T04:19:02+00:00", "likes": 3},
        ]
    )


def test_rows_to_insert_skips_rows_already_in_snowflake():
    # Clés telles que produites par to_varchar(..., 'YYYY-MM-DD"T"HH24:MI:SS.FF6') côté Snowflake.
    existing = {
        "1|2026-09-14T04:20:13.123456",
        "2|2026-09-14T04:20:13.123400",
    }

    missing = rows_to_insert(_supabase_rows(), existing)

    assert missing["likes"].tolist() == [3]


def test_rows_to_insert_same_tweet_other_run_is_not_a_duplicate():
    missing = rows_to_insert(_supabase_rows(), {"1|2026-09-21T04:19:02.000000"})

    assert sorted(missing["likes"].tolist()) == [1, 2]


def test_rows_to_insert_normalises_offsets_to_utc():
    rows = pd.DataFrame([{"tweet_id": "1", "extracted_at": "2026-09-14T06:20:13+02:00", "likes": 1}])

    assert rows_to_insert(rows, {"1|2026-09-14T04:20:13.000000"}).empty


def test_rows_to_insert_everything_already_migrated_is_a_no_op():
    rows = _supabase_rows()
    existing = {
        "1|2026-09-14T04:20:13.123456",
        "2|2026-09-14T04:20:13.123400",
        "1|2026-09-21T04:19:02.000000",
    }

    assert rows_to_insert(rows, existing).empty
