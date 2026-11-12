"""Chargement des exports CSV "Account overview" de X dans Snowflake.

Ces métriques quotidiennes au niveau du compte (impressions du jour, profils
visités, nouveaux abonnés / désabonnements…) ne sont PAS disponibles via l'API
X : elles viennent uniquement de l'export manuel x.com → Analytics → Overview
→ Export (account_overview_analytics.csv).

Idempotent : MERGE sur metric_date. Les exports se chevauchent et X révise les
derniers jours ; pour un même jour, la ligne de l'export le plus récent (date
max du fichier la plus tardive) l'emporte — recharger un ancien fichier
n'écrase jamais des chiffres plus frais.

Usage (depuis la racine du repo, SNOWFLAKE_* dans .env) :
    python scripts/load_x_account_analytics.py chemin/account_overview_analytics.csv [...] [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from snowflake.connector.pandas_tools import write_pandas

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.load_to_snowflake import get_connection  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

TABLE_NAME = "account_daily_metrics_raw"

# Colonnes de l'export X -> colonnes Snowflake.
COLUMN_MAP = {
    "Date": "metric_date",
    "Impressions": "impressions",
    "Likes": "likes",
    "Engagements": "engagements",
    "Bookmarks": "bookmarks",
    "Shares": "shares",
    "New follows": "new_follows",
    "Unfollows": "unfollows",
    "Replies": "replies",
    "Reposts": "reposts",
    "Profile visits": "profile_visits",
    "Create Post": "posts_created",
    "Video views": "video_views",
    "Media views": "media_views",
}
METRIC_COLUMNS = [c for c in COLUMN_MAP.values() if c != "metric_date"]

CREATE_TABLE = f"""
create table if not exists {TABLE_NAME} (
    metric_date      date,
    impressions      number,
    likes            number,
    engagements      number,
    bookmarks        number,
    shares           number,
    new_follows      number,
    unfollows        number,
    replies          number,
    reposts          number,
    profile_visits   number,
    posts_created    number,
    video_views      number,
    media_views      number,
    export_max_date  date,          -- dernier jour couvert par l'export source (fraîcheur)
    source_file      string,
    loaded_at        timestamp_tz default current_timestamp()
)
"""


def parse_export(path: Path) -> pd.DataFrame:
    """Lit un export "Account overview" (.csv brut de X, ou .xlsx enregistré depuis Excel)."""
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        raw = pd.read_excel(path)
    else:
        raw = pd.read_csv(path)
    missing = set(COLUMN_MAP) - set(raw.columns)
    if missing:
        raise ValueError(f"{path.name} : colonnes absentes {sorted(missing)} — pas un export Account overview ?")

    df = raw[list(COLUMN_MAP)].rename(columns=COLUMN_MAP)
    if pd.api.types.is_datetime64_any_dtype(df["metric_date"]):
        # .xlsx : Excel stocke de vraies dates, aucune ambiguïté jour/mois.
        df["metric_date"] = df["metric_date"].dt.date
    else:
        try:
            df["metric_date"] = pd.to_datetime(df["metric_date"], format="%a, %b %d, %Y").dt.date
        except ValueError as exc:
            raise ValueError(
                f"{path.name} : dates au format inattendu ({df['metric_date'].iloc[0]!r}). Utiliser le CSV "
                "d'origine de X, ou l'enregistrer en .xlsx — un CSV ré-enregistré par Excel change le format des dates."
            ) from exc
    df[METRIC_COLUMNS] = df[METRIC_COLUMNS].fillna(0).astype("int64")
    if df["metric_date"].duplicated().any():
        raise ValueError(f"{path.name} : plusieurs lignes pour un même jour.")

    df["export_max_date"] = df["metric_date"].max()
    df["source_file"] = path.name
    return df.sort_values("metric_date").reset_index(drop=True)


def merge_export(conn, df: pd.DataFrame) -> tuple[int, int]:
    """Upsert de `df` via une table temporaire + MERGE. Retourne (insérées, mises à jour)."""
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE)
        cur.execute(f"create or replace temporary table {TABLE_NAME}_stage like {TABLE_NAME}")

    stage = df.copy()
    success, *_ = write_pandas(
        conn,
        stage,
        table_name=f"{TABLE_NAME}_stage",
        quote_identifiers=False,
        use_logical_type=True,
    )
    if not success:
        raise RuntimeError("Échec du chargement dans la table temporaire.")

    columns = ["metric_date", *METRIC_COLUMNS, "export_max_date", "source_file"]
    updates = ",\n            ".join(f"t.{c} = s.{c}" for c in columns[1:])
    merge = f"""
        merge into {TABLE_NAME} t
        using {TABLE_NAME}_stage s
            on t.metric_date = s.metric_date
        when matched and s.export_max_date >= t.export_max_date then update set
            {updates},
            t.loaded_at = current_timestamp()
        when not matched then insert ({", ".join(columns)})
            values ({", ".join("s." + c for c in columns)})
    """
    with conn.cursor() as cur:
        cur.execute(merge)
        inserted, updated = cur.fetchone()[:2]
    return inserted, updated


def main(paths: list[Path], dry_run: bool) -> None:
    exports = [parse_export(p) for p in paths]
    for path, df in zip(paths, exports):
        logger.info("%s : %d jour(s), du %s au %s", path.name, len(df), df["metric_date"].min(), df["metric_date"].max())

    if dry_run:
        logger.info("--dry-run : aucune écriture.")
        return

    conn = get_connection()
    try:
        # Du plus ancien au plus récent : le plus frais passe en dernier.
        for path, df in sorted(zip(paths, exports), key=lambda pair: pair[1]["export_max_date"].iloc[0]):
            inserted, updated = merge_export(conn, df)
            logger.info("%s : %d jour(s) ajouté(s), %d mis à jour.", path.name, inserted, updated)
        with conn.cursor() as cur:
            days, first, last = cur.execute(
                f"select count(*), min(metric_date), max(metric_date) from {TABLE_NAME}"
            ).fetchone()
    finally:
        conn.close()
    logger.info("%s : %d jour(s) au total, du %s au %s.", TABLE_NAME, days, first, last)


if __name__ == "__main__":
    load_dotenv()
    parser = argparse.ArgumentParser(description="Charge des exports X 'Account overview' dans Snowflake.")
    parser.add_argument("paths", nargs="+", type=Path, help="fichier(s) account_overview_analytics*.csv")
    parser.add_argument("--dry-run", action="store_true", help="lire et valider sans rien écrire")
    args = parser.parse_args()
    main(args.paths, args.dry_run)
