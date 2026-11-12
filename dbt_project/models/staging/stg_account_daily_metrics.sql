-- Métriques quotidiennes du compte (exports CSV X), une ligne par jour.
select
    metric_date,
    {{ week_start_monday('metric_date') }}::date as metric_week,
    impressions,
    likes,
    engagements,
    bookmarks,
    shares,
    new_follows,
    unfollows,
    new_follows - unfollows as net_follows,
    replies,
    reposts,
    profile_visits,
    posts_created,
    video_views,
    media_views,
    source_file,
    loaded_at
from {{ source('raw', 'account_daily_metrics_raw') }}
where metric_date is not null
