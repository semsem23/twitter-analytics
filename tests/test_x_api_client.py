"""Tests unitaires pour x_api_client.py — tout Tweepy est mocké, aucun appel API réel."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import tweepy

from src.x_api_client import (
    UnauthorizedAccountError,
    UserNotFoundError,
    fetch_weekly_tweets,
)

USERNAME = "ElCambur442953"
USER_ID = 123456789


def _make_tweet(tweet_id: int, likes: int, retweets: int, replies: int, impressions: int, text: str = "sample tweet text"):
    return SimpleNamespace(
        id=tweet_id,
        created_at="2026-09-08T10:00:00Z",
        text=text,
        public_metrics={
            "like_count": likes,
            "retweet_count": retweets,
            "reply_count": replies,
            "impression_count": impressions,
        },
    )


def _make_client(get_user_data=None, tweets_pages=None):
    """Construit un mock de tweepy.Client renvoyant les réponses fournies."""
    client = MagicMock()

    client.get_user.return_value = SimpleNamespace(data=get_user_data)

    if tweets_pages is not None:
        client.get_users_tweets.side_effect = tweets_pages

    return client


@pytest.fixture(autouse=True)
def owned_username_env(monkeypatch):
    """X_OWNED_USERNAME est requis par fetch_weekly_tweets — défini par défaut sur le compte suivi."""
    monkeypatch.setenv("X_OWNED_USERNAME", USERNAME)


@patch("src.x_api_client._get_client")
def test_fetch_weekly_tweets_parses_dataframe_correctly(mock_get_client):
    tweets = [_make_tweet(1, likes=10, retweets=2, replies=1, impressions=500, text="Breaking news update")]
    client = _make_client(
        get_user_data=SimpleNamespace(id=USER_ID, username=USERNAME),
        tweets_pages=[SimpleNamespace(data=tweets, meta={})],
    )
    mock_get_client.return_value = client

    df = fetch_weekly_tweets(USERNAME)

    assert list(df.columns) == ["tweet_id", "created_at", "text", "likes", "retweets", "replies", "impressions"]
    assert len(df) == 1
    row = df.iloc[0]
    assert row["tweet_id"] == "1"
    assert row["text"] == "Breaking news update"
    assert row["likes"] == 10
    assert row["retweets"] == 2
    assert row["replies"] == 1
    assert row["impressions"] == 500


@patch("src.x_api_client._get_client")
def test_fetch_weekly_tweets_no_tweets_returns_empty_dataframe(mock_get_client):
    client = _make_client(
        get_user_data=SimpleNamespace(id=USER_ID, username=USERNAME),
        tweets_pages=[SimpleNamespace(data=None, meta={})],
    )
    mock_get_client.return_value = client

    df = fetch_weekly_tweets(USERNAME)

    assert isinstance(df, pd.DataFrame)
    assert df.empty
    assert list(df.columns) == ["tweet_id", "created_at", "text", "likes", "retweets", "replies", "impressions"]


@patch("src.x_api_client._get_client")
def test_fetch_weekly_tweets_user_not_found_raises_via_empty_data(mock_get_client):
    # Le compte demandé doit matcher X_OWNED_USERNAME pour passer la vérif d'ownership
    # et atteindre l'appel get_user (dont la réponse vide simule un compte introuvable).
    client = _make_client(get_user_data=None)
    mock_get_client.return_value = client

    with pytest.raises(UserNotFoundError):
        fetch_weekly_tweets(USERNAME)


@patch("src.x_api_client._get_client")
def test_fetch_weekly_tweets_user_not_found_raises_via_tweepy_notfound(mock_get_client):
    client = MagicMock()
    client.get_user.side_effect = tweepy.NotFound(MagicMock())
    mock_get_client.return_value = client

    with pytest.raises(UserNotFoundError):
        fetch_weekly_tweets(USERNAME)


def test_fetch_weekly_tweets_unauthorized_account_raises():
    # Compte demandé différent de X_OWNED_USERNAME (défini par la fixture) :
    # doit être rejeté avant tout appel API.
    with pytest.raises(UnauthorizedAccountError):
        fetch_weekly_tweets("un_autre_compte")


@patch("src.x_api_client._get_client")
def test_fetch_weekly_tweets_handles_pagination(mock_get_client):
    page_1_tweets = [_make_tweet(i, likes=1, retweets=0, replies=0, impressions=10) for i in range(100)]
    page_2_tweets = [_make_tweet(i, likes=1, retweets=0, replies=0, impressions=10) for i in range(100, 105)]

    client = _make_client(
        get_user_data=SimpleNamespace(id=USER_ID, username=USERNAME),
        tweets_pages=[
            SimpleNamespace(data=page_1_tweets, meta={"next_token": "token_page_2"}),
            SimpleNamespace(data=page_2_tweets, meta={}),
        ],
    )
    mock_get_client.return_value = client

    df = fetch_weekly_tweets(USERNAME)

    assert len(df) == 105
    assert client.get_users_tweets.call_count == 2


@patch("time.sleep", return_value=None)
@patch("src.x_api_client._get_client")
def test_fetch_weekly_tweets_retries_on_rate_limit_then_succeeds(mock_get_client, mock_sleep):
    client = _make_client(
        get_user_data=SimpleNamespace(id=USER_ID, username=USERNAME),
    )
    client.get_users_tweets.side_effect = [
        tweepy.TooManyRequests(MagicMock()),
        SimpleNamespace(data=[_make_tweet(1, likes=1, retweets=0, replies=0, impressions=5)], meta={}),
    ]
    mock_get_client.return_value = client

    df = fetch_weekly_tweets(USERNAME)

    assert len(df) == 1
    assert client.get_users_tweets.call_count == 2
    mock_sleep.assert_called_once()


@patch("time.sleep", return_value=None)
@patch("src.x_api_client._get_client")
def test_fetch_weekly_tweets_gives_up_after_max_retries(mock_get_client, mock_sleep):
    client = _make_client(
        get_user_data=SimpleNamespace(id=USER_ID, username=USERNAME),
    )
    client.get_users_tweets.side_effect = tweepy.TooManyRequests(MagicMock())
    mock_get_client.return_value = client

    with pytest.raises(tweepy.TooManyRequests):
        fetch_weekly_tweets(USERNAME)

    assert client.get_users_tweets.call_count == 3
    assert mock_sleep.call_count == 2
