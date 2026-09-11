"""Point d'entrée du pipeline hebdomadaire : extraction API X -> chargement Supabase.

Enchaîne Phase 1 (extraction) et Phase 2 (chargement). Pas encore de dbt ni de Streamlit.
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

from load_to_supabase import load_to_supabase
from x_api_client import fetch_weekly_tweets

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run() -> None:
    load_dotenv()

    username = os.environ.get("X_OWNED_USERNAME")
    if not username:
        raise RuntimeError("La variable d'environnement X_OWNED_USERNAME n'est pas définie.")

    df = fetch_weekly_tweets(username)
    load_to_supabase(df)


if __name__ == "__main__":
    run()
