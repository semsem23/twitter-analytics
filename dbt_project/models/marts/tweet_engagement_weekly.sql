-- Agrégation hebdomadaire des métriques d'engagement des tweets.
-- public_engagement_rate = (likes + retweets) / impressions : seules les actions publiques
-- exposées par l'API X. Différent de engagement_rate dans account_metrics_weekly
-- (engagements X / impressions, export Analytics, qui inclut clics, visites de profil, etc.).
with weekly as (
    select * from {{ ref('stg_tweet_metrics') }}
)

select
    extraction_week,
    count(distinct tweet_id) as tweet_count,
    sum(likes) as total_likes,
    sum(retweets) as total_retweets,
    sum(replies) as total_replies,
    sum(impressions) as total_impressions,
    -- ::numeric seul vaudrait NUMBER(38, 0) sur Snowflake : échelle explicite.
    round(
        (sum(likes) + sum(retweets))::number(38, 10) / nullif(sum(impressions), 0) * 100,
        2
    ) as public_engagement_rate
from weekly
group by extraction_week
order by extraction_week
