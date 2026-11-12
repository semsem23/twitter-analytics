"""Point d'entrée du pipeline hebdomadaire : extraction API X -> chargement Snowflake.

Enchaîne Phase 1 (extraction) et Phase 2 (chargement). Pas encore de dbt ni de Streamlit.

--dry-run : extraction réelle (appel API X facturé) mais aucune écriture — vérifie
seulement que la connexion Snowflake s'ouvre, puis affiche ce qui aurait été chargé.
"""

from __future__ import annotations

import argparse
import logging
import os

from dotenv import load_dotenv

from load_to_snowflake import TABLE_NAME, get_connection, load_to_snowflake
from x_api_client import fetch_weekly_tweets

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def check_snowflake_connection() -> None:
    """Ouvre une connexion et lit la table cible, sans rien écrire."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            user, role, warehouse = cur.execute(
                "select current_user(), current_role(), current_warehouse()"
            ).fetchone()
            row_count = cur.execute(f"select count(*) from {TABLE_NAME}").fetchone()[0]
    finally:
        conn.close()
    logger.info(
        "Connexion Snowflake OK (user=%s, role=%s, warehouse=%s) — %s : %d ligne(s).",
        user,
        role,
        warehouse,
        TABLE_NAME,
        row_count,
    )


def run(dry_run: bool = False) -> None:
    load_dotenv()

    username = os.environ.get("X_OWNED_USERNAME")
    if not username:
        raise RuntimeError("La variable d'environnement X_OWNED_USERNAME n'est pas définie.")

    df = fetch_weekly_tweets(username)

    if dry_run:
        check_snowflake_connection()
        logger.info("--dry-run : %d ligne(s) auraient été chargées, aucune écriture.", len(df))
        if not df.empty:
            logger.info(
                "Tweets du %s au %s.", df["created_at"].min(), df["created_at"].max()
            )
        return

    load_to_snowflake(df)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline hebdomadaire : API X -> Snowflake.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="extraire depuis l'API X sans rien charger dans Snowflake",
    )
    run(dry_run=parser.parse_args().dry_run)
