# Twitter Analytics Pipeline

Suivi hebdomadaire des métriques publiques du compte X **@ElCambur442953** (Geostratfor, bot de publication d'actualités).

## Architecture

```
API X ──> GitHub Actions ──> Supabase (Postgres) ──> dbt ──> Streamlit
```

| Étape | Outil | Rôle |
|---|---|---|
| Extraction | Python (Tweepy) | Récupère les métriques publiques des tweets des 7 derniers jours |
| Stockage | Supabase (Postgres) | Base de données brute + transformée |
| Transformation | dbt (`dbt-postgres`) | Staging → marts, tests de qualité |
| Automatisation | GitHub Actions | Cron hebdomadaire (lundi 9h) |
| Visualisation | Streamlit | Dashboard de suivi de l'engagement |

## Structure du repo

```
twitter-analytics-pipeline/
├── .github/workflows/weekly_pipeline.yml
├── src/
│   ├── x_api_client.py
│   ├── load_to_supabase.py
│   └── run_pipeline.py
├── tests/
│   └── test_x_api_client.py
├── dbt_project/
│   ├── dbt_project.yml
│   ├── profiles.yml
│   └── models/
│       ├── staging/
│       │   ├── stg_tweet_metrics.sql
│       │   └── sources.yml
│       └── marts/
│           └── tweet_engagement_weekly.sql
├── app/
│   └── streamlit_app.py
├── .env.example
├── requirements.txt
└── .gitignore
```

## Setup

### 1. Variables d'environnement

Copier `.env.example` en `.env` et remplir :

```
X_BEARER_TOKEN=
X_OWNED_USERNAME=
SUPABASE_URL=
SUPABASE_KEY=
SUPABASE_DB_PASSWORD=
```

`X_OWNED_USERNAME` doit correspondre au compte associé au Bearer Token (vérifié avant tout appel API pour rester au tarif Owned Read — `GET /2/users/me` n'est pas utilisable ici car il nécessite une auth user-context, incompatible avec un Bearer Token seul).

Ne jamais committer `.env` (déjà exclu via `.gitignore`).

### 2. Base Supabase

Créer la table brute dans l'éditeur SQL Supabase :

```sql
create table tweet_metrics_raw (
    tweet_id text,
    created_at timestamptz,
    text text,
    likes int,
    retweets int,
    replies int,
    impressions int,
    extracted_at timestamptz default now()
);
```

### 3. Connexion dbt

**Important** : utiliser le **Session Pooler** de Supabase (port 5432, IPv4) et non la connexion directe (IPv6) — GitHub Actions ne route pas l'IPv6.

Host format : `aws-0-{region}.pooler.supabase.com`
User format : `postgres.{project_ref}`

`dbt_project/profiles.yml` est versionné dans le repo (pas dans `~/.dbt/`) et lit ses credentials via `SUPABASE_DB_HOST`, `SUPABASE_DB_USER`, `SUPABASE_DB_PASSWORD`, `SUPABASE_DB_PORT` (défaut 5432), `SUPABASE_DB_NAME` (défaut `postgres`). Comme il est à côté de `dbt_project.yml`, `--profiles-dir dbt_project` est requis à chaque commande dbt.

**Attention** : contrairement à `run_pipeline.py`, dbt ne charge pas `.env` automatiquement (`env_var()` lit uniquement les vraies variables d'environnement du shell). En local, préfixer les commandes dbt avec `dotenv run --` (fourni par `python-dotenv`) depuis la racine du repo :

```bash
dotenv run -- dbt debug --project-dir dbt_project --profiles-dir dbt_project
```

### 4. Secrets GitHub Actions

Dans **Settings → Secrets and variables → Actions**, ajouter les variables listées dans `.env.example`.

### 5. Lancer manuellement (test)

```bash
pip install -r requirements.txt
python src/run_pipeline.py
dotenv run -- dbt run --project-dir dbt_project --profiles-dir dbt_project
dotenv run -- dbt test --project-dir dbt_project --profiles-dir dbt_project
streamlit run app/streamlit_app.py
```

## Automatisation

Le workflow `.github/workflows/weekly_pipeline.yml` tourne chaque lundi à 9h (UTC), et peut être déclenché manuellement via l'onglet **Actions** (`workflow_dispatch`).

## Coûts estimés

| Poste | Coût mensuel estimé |
|---|---|
| API X (60 tweets/semaine, Owned Reads) | ~0,30 $ |
| GitHub Actions (repo privé, quelques minutes/mois) | 0 $ (largement sous le quota gratuit) |
| Supabase (plan gratuit) | 0 $ |
| Streamlit Community Cloud | 0 $ |

## Notes

- Le matching avec les trends X a été écarté après test (voir historique du projet) au profit d'un suivi pur des métriques d'engagement.
- Le premier run a montré un engagement quasi nul malgré des impressions correctes — point à surveiller dans le dashboard Streamlit une fois plusieurs semaines de données accumulées.
