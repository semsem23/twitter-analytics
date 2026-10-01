-- Agrégation hebdomadaire (lundi -> dimanche) des métriques du compte.
-- Même découpage que extraction_week dans tweet_engagement_weekly, pour pouvoir
-- rapprocher les deux marts semaine par semaine.
with daily as (
    select * from {{ ref('stg_account_daily_metrics') }}
)

select
    metric_week,
    count(*) as days_covered,
    sum(impressions) as total_impressions,
    sum(engagements) as total_engagements,
    sum(likes) as total_likes,
    sum(replies) as total_replies,
    sum(reposts) as total_reposts,
    sum(profile_visits) as total_profile_visits,
    sum(new_follows) as total_new_follows,
    sum(unfollows) as total_unfollows,
    sum(net_follows) as net_follows,
    sum(posts_created) as total_posts_created,
    round(sum(engagements)::number(38, 10) / nullif(sum(impressions), 0) * 100, 2) as engagement_rate
from daily
group by metric_week
order by metric_week
