-- Table brute des métriques de tweets, alimentée en mode append uniquement.
-- Pas de contrainte d'unicité : chaque run hebdomadaire ajoute un nouvel
-- instantané des métriques par tweet (l'unicité par semaine est testée côté dbt).
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
