-- Setup Snowflake du pipeline twitter-analytics (à exécuter une seule fois, dans
-- une worksheet Snowsight, par un utilisateur ayant SYSADMIN et SECURITYADMIN).
--
-- Idempotent : tous les objets sont créés en "if not exists", le script peut
-- être rejoué sans casse.
--
-- Avant d'exécuter : remplacer <RSA_PUBLIC_KEY> par le contenu de rsa_key.pub
-- SANS les lignes -----BEGIN/END PUBLIC KEY----- (voir README, section Snowflake).
-- La clé privée ne doit jamais apparaître ici.

-- ---------------------------------------------------------------- compute
use role sysadmin;

-- X-SMALL = 1 crédit/heure, facturé à la seconde (minimum 60 s par reprise).
-- Avec ~60 tweets/semaine, le coût dépend uniquement du temps où le warehouse
-- est allumé : AUTO_SUSPEND = 60 le coupe 1 minute après la dernière requête.
create warehouse if not exists TWITTER_ANALYTICS_WH
    warehouse_size = 'XSMALL'
    auto_suspend = 60
    auto_resume = true
    initially_suspended = true
    statement_timeout_in_seconds = 600  -- garde-fou contre une requête qui tourne en boucle
    comment = 'Pipeline hebdomadaire twitter-analytics + dashboard Streamlit';

-- ---------------------------------------------------------------- stockage
create database if not exists TWITTER_ANALYTICS;

-- Un seul schéma, comme "public" côté Supabase : la table brute et les modèles
-- dbt (stg_tweet_metrics, tweet_engagement_weekly) y cohabitent.
create schema if not exists TWITTER_ANALYTICS.ANALYTICS;

-- Table brute append-only : chaque run ajoute un instantané des métriques par
-- tweet, pas de contrainte d'unicité (l'unicité par semaine est testée côté dbt).
create table if not exists TWITTER_ANALYTICS.ANALYTICS.TWEET_METRICS_RAW (
    tweet_id     string,
    created_at   timestamp_tz,
    text         string,
    likes        number,
    retweets     number,
    replies      number,
    impressions  number,
    extracted_at timestamp_tz default current_timestamp()
);

-- ---------------------------------------------------------------- accès
use role securityadmin;

create role if not exists TWITTER_ANALYTICS_ROLE
    comment = 'Pipeline twitter-analytics : chargement, dbt, lecture Streamlit';

-- Rattacher le rôle à SYSADMIN pour que les admins voient les objets créés par dbt.
grant role TWITTER_ANALYTICS_ROLE to role sysadmin;

-- Utilisateur de service : TYPE = SERVICE interdit toute connexion par mot de
-- passe, seule l'authentification par paire de clés est possible.
create user if not exists TWITTER_ANALYTICS_SVC
    type = service
    default_role = TWITTER_ANALYTICS_ROLE
    default_warehouse = TWITTER_ANALYTICS_WH
    default_namespace = TWITTER_ANALYTICS.ANALYTICS
    rsa_public_key = '<RSA_PUBLIC_KEY>'
    comment = 'GitHub Actions + Streamlit (key-pair auth)';

grant role TWITTER_ANALYTICS_ROLE to user TWITTER_ANALYTICS_SVC;

-- ---------------------------------------------------------------- droits
grant usage, operate on warehouse TWITTER_ANALYTICS_WH to role TWITTER_ANALYTICS_ROLE;
grant usage on database TWITTER_ANALYTICS to role TWITTER_ANALYTICS_ROLE;

-- dbt crée des vues (staging) et des tables (marts) dans le schéma.
grant usage, create table, create view on schema TWITTER_ANALYTICS.ANALYTICS
    to role TWITTER_ANALYTICS_ROLE;

-- Chargeur : append uniquement (pas de UPDATE / DELETE / TRUNCATE).
grant select, insert on table TWITTER_ANALYTICS.ANALYTICS.TWEET_METRICS_RAW
    to role TWITTER_ANALYTICS_ROLE;

-- ---------------------------------------------------------------- vérification
-- describe user TWITTER_ANALYTICS_SVC;   -- RSA_PUBLIC_KEY_FP doit être renseigné
-- show grants to role TWITTER_ANALYTICS_ROLE;

-- ---------------------------------------------------------------- optionnel
-- Plafond de dépense mensuel (nécessite ACCOUNTADMIN). 5 crédits ≈ 10-15 $ en
-- Standard : largement au-dessus de l'usage attendu, coupe tout en cas d'emballement.
--
-- use role accountadmin;
-- create resource monitor if not exists TWITTER_ANALYTICS_RM
--     with credit_quota = 5
--     frequency = monthly
--     start_timestamp = immediately
--     triggers on 80 percent do notify
--              on 100 percent do suspend;
-- alter warehouse TWITTER_ANALYTICS_WH set resource_monitor = TWITTER_ANALYTICS_RM;
