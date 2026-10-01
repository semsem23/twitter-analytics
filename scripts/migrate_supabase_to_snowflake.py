"""Migration one-off : copie tweet_metrics_raw de Supabase vers Snowflake.

Idempotent : une ligne déjà présente dans Snowflake (même tweet_id + même
extracted_at, comparé en UTC à la microseconde) n'est jamais réinsérée — le
script peut être relancé autant de fois que nécessaire, y compris pendant la
période où les deux pipelines tournent en parallèle.

Les extracted_at d'origine sont conservés (pas de nouveau timestamp), pour que
extraction_week et la déduplication dbt donnent le même résultat des deux côtés.

Usage (depuis la racine du repo, .env rempli avec SUPABASE_* et SNOWFLAKE_*) :
    python scripts/migrate_supabase_to_snowflake.py --dry-run
    python scripts/migrate_supabase_to_snowflake.py
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from supabase import create_client

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.load_to_snowflake import COLUMNS, TABLE_NAME, get_connection, write_rows  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

PAGE_SIZE = 1000  # limite par défaut de PostgREST (Supabase)


def _key(tweet_ids: pd.Series, extracted_at: pd.Series) -> pd.Series:
    """Clé d'idempotence "tweet_id|extracted_at UTC à la microseconde"."""
    timestamps = pd.to_datetime(extracted_at, utc=True, format="ISO8601").dt.strftime("%Y-%m-%dT%H:%M:%S.%f")
    return tweet_ids.astype(str) + "|" + timestamps


def rows_to_insert(source: pd.DataFrame, existing_keys: set[str]) -> pd.DataFrame:
    """Lignes de `source` dont la clé (tweet_id, extracted_at) est absente de Snowflake."""
    if source.empty:
        return source
    keys = _key(source["tweet_id"], source["extracted_at"])
    return source[~keys.isin(existing_keys)]


def fetch_supabase_rows() -> pd.DataFrame:
    """Lit toute la table Supabase, paginée (ordre stable pour ne rien sauter)."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError("Les variables d'environnement SUPABASE_URL et SUPABASE_KEY sont requises.")
    client = create_client(url, key)

    rows: list[dict] = []
    start = 0
    while True:
        page = (
            client.table(TABLE_NAME)
            .select(",".join(COLUMNS))
            .order("extracted_at")
            .order("tweet_id")
            .range(start, start + PAGE_SIZE - 1)
            .execute()
            .data
        )
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return pd.DataFrame(rows, columns=COLUMNS)


def snowflake_count(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(f"select count(*) from {TABLE_NAME}")
        return cur.fetchone()[0]


def snowflake_keys(conn) -> set[str]:
    query = f"""
        select tweet_id || '|' || to_varchar(
            convert_timezone('UTC', extracted_at), 'YYYY-MM-DD"T"HH24:MI:SS.FF6'
        )
        from {TABLE_NAME}
    """
    with conn.cursor() as cur:
        cur.execute(query)
        return {row[0] for row in cur.fetchall()}


def migrate(dry_run: bool = False) -> None:
    source = fetch_supabase_rows()
    logger.info("Supabase  %s : %d ligne(s)", TABLE_NAME, len(source))

    conn = get_connection()
    try:
        before = snowflake_count(conn)
        logger.info("Snowflake %s : %d ligne(s) avant migration", TABLE_NAME, before)

        missing = rows_to_insert(source, snowflake_keys(conn))
        logger.info(
            "%d ligne(s) à insérer, %d déjà présente(s) (tweet_id + extracted_at)",
            len(missing),
            len(source) - len(missing),
        )

        if dry_run:
            logger.info("--dry-run : aucune écriture.")
            return

        inserted = write_rows(conn, missing) if not missing.empty else 0
        after = snowflake_count(conn)
    finally:
        conn.close()

    logger.info("%d ligne(s) insérée(s)", inserted)
    logger.info("Snowflake %s : %d ligne(s) après migration", TABLE_NAME, after)
    # Égalité attendue juste après la migration ; Snowflake peut avoir plus de
    # lignes si le pipeline Snowflake a déjà tourné de son côté.
    if after < len(source):
        logger.warning("ATTENTION : Snowflake a moins de lignes que Supabase (%d < %d).", after, len(source))
    else:
        logger.info("OK : toutes les lignes Supabase sont présentes dans Snowflake.")


if __name__ == "__main__":
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="compter sans rien écrire dans Snowflake")
    migrate(dry_run=parser.parse_args().dry_run)
