"""Chargement des métriques de tweets dans Snowflake (table tweet_metrics_raw).

Phase 2 du pipeline : insertion append-only uniquement, jamais d'overwrite —
chaque run ajoute un instantané des métriques (historique semaine après semaine).

Authentification par paire de clés uniquement (utilisateur de service, voir
sql/snowflake_setup.sql) : jamais de mot de passe.
"""

from __future__ import annotations

import base64
import logging
import os
from datetime import datetime, timezone

import pandas as pd
import snowflake.connector
from cryptography.hazmat.primitives import serialization
from snowflake.connector.pandas_tools import write_pandas

logger = logging.getLogger(__name__)

TABLE_NAME = "tweet_metrics_raw"
COLUMNS = ["tweet_id", "created_at", "text", "likes", "retweets", "replies", "impressions", "extracted_at"]

REQUIRED_ENV_VARS = [
    "SNOWFLAKE_ACCOUNT",
    "SNOWFLAKE_USER",
    "SNOWFLAKE_ROLE",
    "SNOWFLAKE_WAREHOUSE",
    "SNOWFLAKE_DATABASE",
    "SNOWFLAKE_SCHEMA",
    "SNOWFLAKE_PRIVATE_KEY",
]


def private_key_der(private_key: str, passphrase: str | None = None) -> bytes:
    """Convertit la clé privée (variable d'environnement) en DER PKCS#8 non chiffré.

    Même convention que dbt-snowflake, pour qu'une seule valeur serve partout :
    PEM complet si la valeur commence par "-", sinon base64 du DER (= corps du
    PEM sur une ligne). Les "\\n" littéraux (.env sur une ligne) sont tolérés.
    """
    value = private_key.strip().replace("\\n", "\n")
    password = passphrase.encode() if passphrase else None
    if value.startswith("-"):
        key = serialization.load_pem_private_key(value.encode(), password=password)
    else:
        key = serialization.load_der_private_key(base64.b64decode(value), password=password)
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _env(name: str) -> str:
    """Variable d'environnement sans espaces ni retour à la ligne parasites.

    Un secret GitHub collé avec un saut de ligne final suffit à invalider le JWT
    de l'auth par paire de clés (le nom d'utilisateur fait partie du jeton).
    """
    return os.environ.get(name, "").strip()


def get_connection() -> snowflake.connector.SnowflakeConnection:
    """Ouvre une connexion Snowflake à partir des variables d'environnement."""
    missing = [name for name in REQUIRED_ENV_VARS if not _env(name)]
    if missing:
        raise RuntimeError(f"Variables d'environnement Snowflake manquantes : {', '.join(missing)}.")

    return snowflake.connector.connect(
        account=_env("SNOWFLAKE_ACCOUNT"),
        user=_env("SNOWFLAKE_USER"),
        role=_env("SNOWFLAKE_ROLE"),
        warehouse=_env("SNOWFLAKE_WAREHOUSE"),
        database=_env("SNOWFLAKE_DATABASE"),
        schema=_env("SNOWFLAKE_SCHEMA"),
        private_key=private_key_der(
            _env("SNOWFLAKE_PRIVATE_KEY"),
            _env("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"),
        ),
        application="twitter-analytics",
    )


def write_rows(conn: snowflake.connector.SnowflakeConnection, df: pd.DataFrame) -> int:
    """Ajoute `df` (colonnes COLUMNS, extracted_at inclus) à tweet_metrics_raw via write_pandas.

    Partagé avec scripts/migrate_supabase_to_snowflake.py, qui doit conserver
    les extracted_at d'origine. Retourne le nombre de lignes insérées.
    """
    records = df[COLUMNS].copy()
    # Timestamps tz-aware explicites : sans use_logical_type, le Parquet généré
    # par write_pandas fait dériver les TIMESTAMP_TZ.
    for column in ("created_at", "extracted_at"):
        records[column] = pd.to_datetime(records[column], utc=True)

    # quote_identifiers=False : les colonnes en minuscules du DataFrame résolvent
    # vers les identifiants non quotés (donc MAJUSCULES) de la table Snowflake.
    success, _, inserted_count, _ = write_pandas(
        conn,
        records,
        table_name=TABLE_NAME,
        quote_identifiers=False,
        use_logical_type=True,
        auto_create_table=False,
        overwrite=False,
    )
    if not success:
        raise RuntimeError(f"Échec du COPY INTO {TABLE_NAME} (write_pandas).")
    return inserted_count


def load_to_snowflake(df: pd.DataFrame) -> int:
    """Insère les lignes de `df` dans la table tweet_metrics_raw (append uniquement).

    Ajoute une colonne extracted_at (timestamp UTC du run) avant insertion.
    Ne fait aucun appel si le DataFrame est vide. Retourne le nombre de lignes insérées.
    """
    if df.empty:
        logger.info("DataFrame vide, rien à charger dans %s.", TABLE_NAME)
        return 0

    extracted_at = datetime.now(timezone.utc)

    records = df.copy()
    records["extracted_at"] = extracted_at

    conn = get_connection()
    try:
        inserted_count = write_rows(conn, records)
    finally:
        conn.close()

    logger.info(
        "%d ligne(s) insérée(s) dans %s (extracted_at=%s).",
        inserted_count,
        TABLE_NAME,
        extracted_at.isoformat(),
    )

    return inserted_count
