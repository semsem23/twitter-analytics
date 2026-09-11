-- Nettoyage de la source brute + ajout de la semaine d'extraction pour l'agrégation en mart.
-- Si le pipeline tourne plusieurs fois dans la même semaine (re-run manuel, retry),
-- on ne garde que le dernier instantané par tweet et par semaine pour éviter le
-- double comptage dans le mart.
with source as (
    select * from {{ source('raw', 'tweet_metrics_raw') }}
    where tweet_id is not null
),

deduplicated as (
    select
        *,
        date_trunc('week', extracted_at) as extraction_week,
        row_number() over (
            partition by tweet_id, date_trunc('week', extracted_at)
            order by extracted_at desc
        ) as row_num
    from source
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
    extraction_week
from deduplicated
where row_num = 1
