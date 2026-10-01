-- Nettoyage de la source brute + ajout de la semaine couverte, pour l'agrégation en mart.
-- Si le pipeline tourne plusieurs fois dans la même semaine (re-run manuel, retry,
-- cron de rattrapage), on ne garde que le dernier instantané par tweet et par
-- semaine pour éviter le double comptage dans le mart.
--
-- extraction_week = semaine COUVERTE par les données, pas semaine du run : le
-- pipeline tourne le lundi matin et extrait la semaine civile précédente
-- (run du 14/09 -> tweets du 07/09 au 13/09), d'où le décalage de 7 jours
-- appliqué au lundi de la semaine d'extracted_at.
with source as (
    select * from {{ source('raw', 'tweet_metrics_raw') }}
    where tweet_id is not null
),

with_week as (
    select
        *,
        dateadd('day', -7, {{ week_start_monday("convert_timezone('UTC', extracted_at)") }}) as extraction_week
    from source
),

deduplicated as (
    select
        *,
        row_number() over (
            partition by tweet_id, extraction_week
            order by extracted_at desc
        ) as row_num
    from with_week
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
