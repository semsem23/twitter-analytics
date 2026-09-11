-- Agrégation hebdomadaire des métriques d'engagement.
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
    round(
        (sum(likes) + sum(retweets))::numeric / nullif(sum(impressions), 0) * 100,
        2
    ) as engagement_rate
from weekly
group by extraction_week
order by extraction_week
