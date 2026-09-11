-- Nettoyage de la source brute + ajout de la semaine d'extraction pour l'agrégation en mart.
with source as (
    select * from {{ source('raw', 'tweet_metrics_raw') }}
)

select
    tweet_id,
    created_at,
    text,
    likes,
    retweets,
    replies,
    impressions,
    extracted_at,
    date_trunc('week', extracted_at) as extraction_week
from source
where tweet_id is not null
