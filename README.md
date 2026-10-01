# Twitter Analytics Pipeline

Suivi hebdomadaire des métriques publiques du compte X **@ElCambur442953** (Geostratfor, bot de publication d'actualités).

## Architecture

```
API X ──> GitHub Actions ──> Snowflake ──> dbt (dbt-snowflake) ──> Streamlit
          (lundi, 2 crons)   TWITTER_ANALYTICS.ANALYTICS
                             warehouse X-SMALL, auto-suspend 60 s
```

| Étape | Outil | Rôle |
|---|---|---|
| Extraction | Python (Tweepy) | Récupère les métriques publiques des tweets de la semaine civile précédente |
| Chargement | Python (`snowflake-connector-python`, `write_pandas`) | Append-only dans `TWEET_METRICS_RAW`, un `extracted_at` UTC par run |
| Stockage | Snowflake (édition Standard) | Table brute + modèles dbt dans le schéma `ANALYTICS` |
| Transformation | dbt (`dbt-snowflake`) | Staging → marts, tests de qualité |
| Automatisation | GitHub Actions | Cron hebdomadaire (lundi matin, + rattrapage l'après-midi) |
| Visualisation | Streamlit (`snowflake-sqlalchemy`) | Dashboard de suivi de l'engagement |

Toutes les connexions (loader, dbt, Streamlit) utilisent le même utilisateur de service Snowflake avec **authentification par paire de clés** — aucun mot de passe.

## Structure du repo

```
twitter-analytics-pipeline/
├── .github/workflows/weekly_pipeline.yml
├── src/
│   ├── x_api_client.py
│   ├── load_to_snowflake.py
│   └── run_pipeline.py
├── scripts/
│   ├── migrate_supabase_to_snowflake.py   # one-off, à supprimer après le cutover
│   └── load_x_account_analytics.py        # exports CSV "Account overview" de X
├── sql/
│   └── snowflake_setup.sql
├── tests/
│   ├── test_x_api_client.py
│   ├── test_load_to_snowflake.py
│   └── test_migrate_supabase_to_snowflake.py
├── dbt_project/
│   ├── dbt_project.yml
│   ├── profiles.yml
│   ├── macros/
│   │   └── week_start_monday.sql
│   ├── tests/
│   │   └── assert_unique_tweet_extraction_week.sql
│   └── models/
│       ├── staging/
│       │   ├── stg_tweet_metrics.sql
│       │   └── sources.yml
│       └── marts/
│           └── tweet_engagement_weekly.sql
├── app/
│   └── streamlit_app.py
├── .streamlit/config.toml
├── .env.example
├── requirements.txt
└── .gitignore
```

## Setup

### 1. Paire de clés RSA (utilisateur de service)

```bash
openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out rsa_key.p8 -nocrypt
openssl rsa -in rsa_key.p8 -pubout -out rsa_key.pub
```

`rsa_key.p8` est la clé **privée** : ne jamais la committer ni la coller ailleurs que dans `.env`, les secrets GitHub et les secrets Streamlit. Pour une clé chiffrée, retirer `-nocrypt` et renseigner `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE`.

### 2. Objets Snowflake

Dans une worksheet Snowsight (utilisateur avec `SYSADMIN` et `SECURITYADMIN`), exécuter [`sql/snowflake_setup.sql`](sql/snowflake_setup.sql) après avoir remplacé `<RSA_PUBLIC_KEY>` par le contenu de `rsa_key.pub` **sans** les lignes `BEGIN/END`. Le script, idempotent, crée :

- le warehouse `TWITTER_ANALYTICS_WH` (X-SMALL, `AUTO_SUSPEND = 60`, `AUTO_RESUME = TRUE`) ;
- la base `TWITTER_ANALYTICS` et le schéma `ANALYTICS` ;
- la table brute `TWEET_METRICS_RAW` ;
- le rôle `TWITTER_ANALYTICS_ROLE` et l'utilisateur de service `TWITTER_ANALYTICS_SVC` (`TYPE = SERVICE` : connexion par mot de passe impossible) ;
- les droits minimaux (select/insert sur la table brute, création de vues/tables pour dbt).

Un bloc optionnel en fin de script crée un *resource monitor* qui plafonne la dépense mensuelle.

### 3. Variables d'environnement

Copier `.env.example` en `.env` et remplir les `SNOWFLAKE_*`. `SNOWFLAKE_ACCOUNT` est l'identifiant `<orgname>-<account_name>` (Snowsight → menu du compte → *Account details*).

`SNOWFLAKE_PRIVATE_KEY` accepte deux formats (même convention que dbt-snowflake) :

- le corps de `rsa_key.p8` sur une ligne — le plus simple pour `.env` :
  `grep -v "PRIVATE KEY" rsa_key.p8 | tr -d '\n'`
- le PEM complet, `-----BEGIN ...` inclus (secrets GitHub, ou `.env` entre guillemets doubles).

`X_OWNED_USERNAME` doit correspondre au compte associé au Bearer Token (vérifié avant tout appel API pour rester au tarif Owned Read — `GET /2/users/me` n'est pas utilisable ici car il nécessite une auth user-context, incompatible avec un Bearer Token seul).

Ne jamais committer `.env` (déjà exclu via `.gitignore`).

### 4. Reprise de l'historique Supabase (une seule fois)

Avec `SUPABASE_URL` / `SUPABASE_KEY` et les `SNOWFLAKE_*` dans `.env` :

```bash
python scripts/migrate_supabase_to_snowflake.py --dry-run   # compte sans écrire
python scripts/migrate_supabase_to_snowflake.py
```

Le script est idempotent : il saute les lignes déjà présentes (même `tweet_id` + même `extracted_at`) et peut être relancé sans risque de doublon. Les `extracted_at` d'origine sont conservés, donc `extraction_week` est identique des deux côtés.

### 4 bis. Métriques quotidiennes du compte (exports CSV X)

Impressions du jour, visites de profil, nouveaux abonnés / désabonnements… sont des métriques **au niveau du compte** que l'API X ne fournit pas : elles viennent de l'export manuel **x.com → Analytics → Overview → Export** (`account_overview_analytics.csv`).

```bash
python scripts/load_x_account_analytics.py ~/Downloads/account_overview_analytics.csv --dry-run
python scripts/load_x_account_analytics.py ~/Downloads/account_overview_analytics.csv
```

Le script crée `ACCOUNT_DAILY_METRICS_RAW` si besoin et fait un `MERGE` sur la date : une ligne par jour, les exports peuvent se chevaucher, et pour un même jour la ligne de l'export le plus récent l'emporte (X révise les derniers jours). Recharger un vieux fichier n'écrase jamais des chiffres plus frais.

Modèles dbt : `stg_account_daily_metrics` (jour + semaine du lundi, `net_follows`) et `account_metrics_weekly` (même découpage lundi → dimanche que `tweet_engagement_weekly`, `days_covered < 7` = semaine partielle).

Le fichier peut être le CSV d'origine de X ou un `.xlsx` enregistré depuis Excel. Ne pas ré-enregistrer le CSV depuis Excel (il réécrit les dates, ex. `27/09/2026`) : le script le refuse avec un message explicite.

Pour garder la série à jour : refaire un export (X propose jusqu'à 90 jours) et relancer le script, par exemple une fois par mois.

### 5. Connexion dbt

`dbt_project/profiles.yml` est versionné dans le repo (pas dans `~/.dbt/`) et lit `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_ROLE`, `SNOWFLAKE_WAREHOUSE`, `SNOWFLAKE_DATABASE`, `SNOWFLAKE_SCHEMA`, `SNOWFLAKE_PRIVATE_KEY` et `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE` (optionnel). Comme il est à côté de `dbt_project.yml`, `--profiles-dir dbt_project` est requis à chaque commande dbt.

**Attention** : contrairement à `run_pipeline.py`, dbt ne charge pas `.env` automatiquement (`env_var()` lit uniquement les vraies variables d'environnement du shell). En local, préfixer les commandes dbt avec `dotenv run --` (fourni par `python-dotenv`) depuis la racine du repo :

```bash
dotenv run -- dbt debug --project-dir dbt_project --profiles-dir dbt_project
```

`extraction_week` est le lundi (UTC) de la semaine couverte, calculé avec `dayofweekiso()` (macro `week_start_monday`) plutôt qu'avec `date_trunc('week', ...)`, dont le résultat dépend du paramètre Snowflake `WEEK_START`.

### 6. Secrets GitHub Actions

Dans **Settings → Secrets and variables → Actions**, ajouter `X_BEARER_TOKEN`, `X_OWNED_USERNAME` et tous les `SNOWFLAKE_*` de `.env.example` (`SNOWFLAKE_PRIVATE_KEY` : coller le PEM complet, le multi-ligne est accepté). Les `SUPABASE_*` ne sont plus utilisés par ce workflow.

### 7. Secrets Streamlit (Community Cloud)

Dans **App settings → Secrets**, mêmes clés au format TOML :

```toml
SNOWFLAKE_ACCOUNT = "<orgname>-<account_name>"
SNOWFLAKE_USER = "TWITTER_ANALYTICS_SVC"
SNOWFLAKE_ROLE = "TWITTER_ANALYTICS_ROLE"
SNOWFLAKE_WAREHOUSE = "TWITTER_ANALYTICS_WH"
SNOWFLAKE_DATABASE = "TWITTER_ANALYTICS"
SNOWFLAKE_SCHEMA = "ANALYTICS"
SNOWFLAKE_PRIVATE_KEY = """-----BEGIN PRIVATE KEY-----
...
-----END PRIVATE KEY-----"""
```

### 8. Lancer manuellement (test)

```bash
pip install -r requirements.txt
python src/run_pipeline.py
dotenv run -- dbt run --project-dir dbt_project --profiles-dir dbt_project
dotenv run -- dbt test --project-dir dbt_project --profiles-dir dbt_project
streamlit run app/streamlit_app.py
```

## Automatisation

Le workflow `.github/workflows/weekly_pipeline.yml` tourne chaque lundi à 6h17 Paris (4h17 UTC), avec un second cron de rattrapage à 13h17 UTC, et peut être déclenché manuellement via l'onglet **Actions** (`workflow_dispatch`).

**Important** : GitHub n'exécute les `schedule` que sur la branche par défaut. Tant que cette version vit sur la branche `snowflake-migration`, elle ne tourne que sur déclenchement manuel :

```bash
gh workflow run weekly_pipeline.yml --ref snowflake-migration
```

## Coûts estimés

Le volume (~60 tweets/semaine, quelques centaines de lignes) est négligeable : le coût Snowflake vient uniquement du temps d'allumage du warehouse. X-SMALL = 1 crédit/heure, facturé à la seconde avec **60 s minimum par reprise**, plus 60 s d'inactivité avant l'auto-suspend. Le crédit Standard coûte ≈ 2 $ (AWS US) à ~2,6 $ (régions EU).

| Poste | Coût mensuel estimé |
|---|---|
| API X (60 tweets/semaine, Owned Reads) | ~0,30 $ |
| GitHub Actions (repo privé, quelques minutes/mois) | 0 $ (largement sous le quota gratuit) |
| Snowflake — pipeline (2 runs/semaine × ~2-4 min de warehouse) | ~1-2 $ |
| Snowflake — dashboard (chaque visite hors cache réveille le warehouse ≥ 2 min) | ~2-10 $ selon le trafic |
| Snowflake — stockage (< 1 Go) et cloud services (< 10 % du compute) | ~0 $ |
| Streamlit Community Cloud | 0 $ |
| **Total Snowflake Standard** | **~3-12 $ / mois** |

Pour plafonner : activer le *resource monitor* optionnel de `sql/snowflake_setup.sql` et garder le cache de 10 min du dashboard (`ttl=600`).

## Notes

- Le matching avec les trends X a été écarté après test (voir historique du projet) au profit d'un suivi pur des métriques d'engagement.
- Le premier run a montré un engagement quasi nul malgré des impressions correctes — point à surveiller dans le dashboard Streamlit une fois plusieurs semaines de données accumulées.
- La version Supabase (Postgres + `dbt-postgres`) reste sur la branche `main` jusqu'à la fin du cutover.
