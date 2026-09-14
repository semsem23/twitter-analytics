"""Client d'extraction des métriques publiques de tweets via l'API X (Tweepy).

Phase 1 du pipeline : récupération uniquement, pas de stockage ni de transformation.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import tweepy

logger = logging.getLogger(__name__)

# Tarification "Owned Read" (lecture des tweets de son propre compte authentifié)
COST_PER_TWEET_READ = 0.001
COST_PER_USER_LOOKUP = 0.010

MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 2

DATAFRAME_COLUMNS = ["tweet_id", "created_at", "text", "likes", "retweets", "replies", "impressions"]


class UserNotFoundError(Exception):
    """Levée quand le compte X demandé n'existe pas."""


class UnauthorizedAccountError(Exception):
    """Levée quand le compte demandé ne correspond pas au compte authentifié par le token.

    On sortirait alors du tarif "Owned Read" pour passer sur un lookup standard.
    """


def _get_client() -> tweepy.Client:
    """Construit le client Tweepy à partir du Bearer Token en variable d'environnement."""
    bearer_token = os.environ.get("X_BEARER_TOKEN")
    if not bearer_token:
        raise RuntimeError("La variable d'environnement X_BEARER_TOKEN n'est pas définie.")
    return tweepy.Client(bearer_token=bearer_token, wait_on_rate_limit=False)


def _call_with_retry(func, *args, **kwargs):
    """Exécute un appel Tweepy avec retry + backoff exponentiel en cas de rate limit."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return func(*args, **kwargs)
        except tweepy.TooManyRequests:
            if attempt == MAX_RETRIES:
                logger.error("Rate limit atteint après %d tentative(s), abandon.", MAX_RETRIES)
                raise
            wait_seconds = BASE_BACKOFF_SECONDS**attempt
            logger.warning(
                "Rate limit atteint (tentative %d/%d), nouvelle tentative dans %ds.",
                attempt,
                MAX_RETRIES,
                wait_seconds,
            )
            time.sleep(wait_seconds)


def last_complete_week(now: datetime) -> tuple[datetime, datetime]:
    """Retourne la fenetre [lundi 00:00 UTC, lundi suivant 00:00 UTC) de la derniere semaine complete.

    Le pipeline tourne le lundi matin et doit couvrir la semaine civile ecoulee
    (ex. run du lundi 14/09 -> fenetre du lundi 07/09 au dimanche 13/09 inclus),
    et non une fenetre glissante de 7 jours se terminant a l'instant du run.
    Bornes en UTC, coherentes avec le date_trunc('week', ...) du modele dbt.
    """
    current_week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return current_week_start - timedelta(days=7), current_week_start


def fetch_weekly_tweets(username: str) -> pd.DataFrame:
    """Récupère les métriques publiques des tweets de la dernière semaine complète pour `username`.

    Vérifie que `username` correspond bien au compte associé à ce Bearer Token
    (variable X_OWNED_USERNAME), afin de rester au tarif "Owned Read" plutôt que standard.

    Retourne un DataFrame vide (colonnes définies) si aucun tweet sur la période.
    Lève UserNotFoundError si le compte n'existe pas, ou UnauthorizedAccountError
    si le compte ne correspond pas au compte configuré pour ce token.
    """
    # GET /2/users/me exige une auth "user context" (OAuth1/OAuth2 user), incompatible
    # avec un client authentifié uniquement par Bearer Token (App-only). La vérification
    # se fait donc contre une valeur de configuration plutôt qu'un appel API "whoami".
    owned_username = os.environ.get("X_OWNED_USERNAME")
    if not owned_username:
        raise RuntimeError("La variable d'environnement X_OWNED_USERNAME n'est pas définie.")

    if username.lstrip("@").lower() != owned_username.lstrip("@").lower():
        raise UnauthorizedAccountError(
            f"@{username} ne correspond pas au compte configuré pour ce token "
            f"(X_OWNED_USERNAME=@{owned_username}). Un lookup sur un autre compte "
            "sortirait du tarif Owned Read."
        )

    client = _get_client()

    try:
        user_response = _call_with_retry(client.get_user, username=username)
    except tweepy.NotFound as exc:
        raise UserNotFoundError(f"Utilisateur X introuvable : @{username}") from exc

    if user_response is None or user_response.data is None:
        raise UserNotFoundError(f"Utilisateur X introuvable : @{username}")

    user_id = user_response.data.id

    start_time, end_time = last_complete_week(datetime.now(timezone.utc))
    logger.info(
        "Fenetre d'extraction : %s -> %s (exclu).",
        start_time.date(),
        end_time.date(),
    )

    rows: list[dict] = []
    pagination_token = None
    while True:
        response = _call_with_retry(
            client.get_users_tweets,
            id=user_id,
            start_time=start_time,
            end_time=end_time,
            tweet_fields=["public_metrics", "created_at", "text"],
            max_results=100,
            pagination_token=pagination_token,
        )

        if response.data:
            for tweet in response.data:
                metrics = tweet.public_metrics or {}
                rows.append(
                    {
                        "tweet_id": str(tweet.id),
                        "created_at": tweet.created_at,
                        "text": tweet.text,
                        "likes": metrics.get("like_count", 0),
                        "retweets": metrics.get("retweet_count", 0),
                        "replies": metrics.get("reply_count", 0),
                        "impressions": metrics.get("impression_count", 0),
                    }
                )

        pagination_token = response.meta.get("next_token") if response.meta else None
        if not pagination_token:
            break

    df = pd.DataFrame(rows, columns=DATAFRAME_COLUMNS)

    tweet_count = len(df)
    estimated_cost = tweet_count * COST_PER_TWEET_READ + COST_PER_USER_LOOKUP
    logger.info(
        "%d tweet(s) récupéré(s) pour @%s — coût estimé : $%.4f",
        tweet_count,
        username,
        estimated_cost,
    )

    return df
