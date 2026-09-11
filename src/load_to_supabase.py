"""Chargement des métriques de tweets dans Supabase (table tweet_metrics_raw).

Phase 2 du pipeline : insertion append-only uniquement, jamais d'overwrite —
chaque run ajoute un instantané des métriques (historique semaine après semaine).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import pandas as pd
from supabase import Client, create_client

logger = logging.getLogger(__name__)

TABLE_NAME = "tweet_metrics_raw"


def _get_client() -> Client:
    """Construit le client Supabase à partir des credentials en variables d'environnement."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError("Les variables d'environnement SUPABASE_URL et SUPABASE_KEY sont requises.")
    return create_client(url, key)


def load_to_supabase(df: pd.DataFrame) -> int:
    """Insère les lignes de `df` dans la table tweet_metrics_raw (append uniquement).

    Ajoute une colonne extracted_at (timestamp UTC du run) avant insertion.
    Ne fait aucun appel si le DataFrame est vide. Retourne le nombre de lignes insérées.
    """
    if df.empty:
        logger.info("DataFrame vide, rien à charger dans %s.", TABLE_NAME)
        return 0

    extracted_at = datetime.now(timezone.utc).isoformat()

    records = df.copy()
    records["extracted_at"] = extracted_at
    # created_at contient des datetime (venant de Tweepy) : sérialisation JSON explicite requise.
    records["created_at"] = records["created_at"].apply(
        lambda value: value.isoformat() if hasattr(value, "isoformat") else value
    )

    payload = records.to_dict(orient="records")

    client = _get_client()
    response = client.table(TABLE_NAME).insert(payload).execute()

    inserted_count = len(response.data) if response.data else 0
    logger.info(
        "%d ligne(s) insérée(s) dans %s (extracted_at=%s).",
        inserted_count,
        TABLE_NAME,
        extracted_at,
    )

    return inserted_count
