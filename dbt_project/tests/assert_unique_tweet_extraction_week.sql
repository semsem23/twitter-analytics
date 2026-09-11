-- Test d'unicité composite (tweet_id, extraction_week) : passe si la requête ne retourne aucune ligne.
select
    tweet_id,
    extraction_week,
    count(*) as row_count
from {{ ref('stg_tweet_metrics') }}
group by tweet_id, extraction_week
having count(*) > 1
