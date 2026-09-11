# Prompt pour Claude Code — Twitter Analytics Pipeline

Copier-coller ce prompt dans Claude Code. Travailler phase par phase : valider chaque phase avant de passer à la suivante plutôt que de tout demander d'un coup.

---

## Contexte du projet

Compte X suivi : `@ElCambur442953` (bot de publication d'actualités, nom affiché "Geostratfor").

Objectif : pipeline hebdomadaire qui récupère les métriques publiques des tweets des 7 derniers jours, les stocke dans Supabase (Postgres), les transforme avec dbt, et les visualise dans Streamlit.

Stack :
- Extraction : Python + Tweepy (API X, Bearer Token)
- Stockage : Supabase (Postgres managé)
- Transformation : dbt-postgres
- Automatisation : GitHub Actions (cron hebdomadaire)
- Visualisation : Streamlit

Structure de repo cible :
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
│       └── marts/
├── app/
│   └── streamlit_app.py
├── .env.example
├── requirements.txt
└── .gitignore
```

---

## PHASE 1 — Extraction API X

Crée `src/x_api_client.py` :

1. Client `tweepy.Client` authentifié par Bearer Token, lu depuis la variable d'environnement `X_BEARER_TOKEN` (jamais en dur dans le code).
2. Fonction `fetch_weekly_tweets(username: str) -> pd.DataFrame` :
   - Récupère l'ID utilisateur via `get_user(username=...)`
   - Récupère les tweets des 7 derniers jours via `get_users_tweets(user_id, start_time=<J-7>, tweet_fields=["public_metrics", "created_at"])`
   - Gère la pagination si plus de 100 tweets sur la période
   - Retourne un DataFrame avec les colonnes : `tweet_id`, `created_at`, `likes`, `retweets`, `replies`, `impressions`
3. Gestion d'erreurs :
   - Utilisateur introuvable → exception explicite
   - Aucun tweet sur la période → DataFrame vide (pas une erreur)
   - Rate limit → retry avec backoff exponentiel (max 3 tentatives)
4. Log du nombre de tweets récupérés et du coût estimé (60 tweets ≈ Owned Read à 0,001 $/tweet + 0,010 $ pour le lookup utilisateur).

Contrainte : vérifier que `user_id` correspond bien au compte authentifié par le token, pour rester en tarif "Owned Read".

Écris `tests/test_x_api_client.py` avec un mock de la réponse Tweepy pour valider le parsing, sans appel API réel.

---

## PHASE 2 — Chargement Supabase

Crée `src/load_to_supabase.py` :

- Client `supabase-py`, credentials via `SUPABASE_URL` et `SUPABASE_KEY` (service_role key)
- Fonction `load_to_supabase(df: pd.DataFrame)` qui insère les lignes dans la table `tweet_metrics_raw`
- Ajoute une colonne `extracted_at` (timestamp UTC du run) avant insertion
- Mode append uniquement (jamais d'overwrite — on veut un historique semaine après semaine)

Fournis le SQL de création de la table `tweet_metrics_raw` (colonnes : `tweet_id`, `created_at`, `likes`, `retweets`, `replies`, `impressions`, `extracted_at`).

Crée `src/run_pipeline.py` qui enchaîne Phase 1 → Phase 2.

---

## PHASE 3 — Transformation dbt

Dans `dbt_project/` :

- `models/staging/stg_tweet_metrics.sql` : nettoyage depuis la source raw, ajoute `extraction_week` (`date_trunc('week', extracted_at)`)
- `models/staging/sources.yml` : déclare la source `tweet_metrics_raw`
- `models/marts/tweet_engagement_weekly.sql` : agrégation par semaine — nb tweets, total likes/retweets/replies/impressions, taux d'engagement (`(likes + retweets) / impressions * 100`)
- Tests dbt : unicité sur (`tweet_id`, `extraction_week`), test de fraîcheur sur la source

`profiles.yml` : connexion Postgres via le **Session Pooler Supabase** (port 5432, IPv4 — pas la connexion directe qui est IPv6 et incompatible avec GitHub Actions). Host, user et password lus depuis variables d'environnement.

---

## PHASE 4 — Automatisation GitHub Actions

Crée `.github/workflows/weekly_pipeline.yml` :

- Déclencheurs : `schedule` (cron lundi 9h UTC) + `workflow_dispatch` (déclenchement manuel)
- Étapes : checkout → setup Python 3.11 → install requirements → `python src/run_pipeline.py` → `dbt run --project-dir dbt_project`
- Secrets utilisés : `X_BEARER_TOKEN`, `SUPABASE_URL`, `SUPABASE_KEY`, `SUPABASE_DB_PASSWORD`

---

## PHASE 5 — Visualisation Streamlit

Crée `app/streamlit_app.py` :

- Connexion Supabase via SQLAlchemy (secrets Streamlit)
- Requête sur `tweet_engagement_weekly`
- Affichage : graphique d'évolution likes/retweets par semaine, métrique du taux d'engagement moyen, tableau détaillé
- Simple, une seule page, pas de multi-onglets pour l'instant

---

## Consignes générales

- Jamais de credentials en dur dans le code — toujours variables d'environnement
- Code en anglais, commentaires en français
- Crée aussi `.env.example` (variables sans valeurs), `requirements.txt`, et `.gitignore` (exclure `.env`, `__pycache__/`, `.venv/`, `dbt_project/target/`, `dbt_project/dbt_packages/`)
- Valide chaque phase (test manuel ou `dbt debug`) avant de passer à la suivante
