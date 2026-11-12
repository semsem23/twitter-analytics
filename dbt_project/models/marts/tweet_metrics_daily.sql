-- Métriques des tweets agrégées par jour de PUBLICATION (UTC).
-- Grain le plus fin du dashboard : la vue semaine / mois de Streamlit est
-- recalculée à partir de ce mart, sans autre modèle.
--
-- Un tweet peut avoir plusieurs instantanés (re-runs, cron de rattrapage) :
-- on ne garde que le plus récent, qui porte les métriques les plus à jour.
with latest_snapshot as (
    select *
    from {{ ref('stg_tweet_metrics') }}
    qualify row_number() over (partition by tweet_id order by extracted_at desc) = 1
)

select
    to_date(convert_timezone('UTC', created_at)) as publication_date,
    count(*) as tweet_count,
    sum(impressions) as total_impressions,
    sum(likes) as total_likes,
    sum(retweets) as total_retweets,
    sum(replies) as total_replies
from latest_snapshot
group by publication_date
order by publication_date
