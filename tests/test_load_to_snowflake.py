"""Tests unitaires pour load_to_snowflake.py — connexion et write_pandas sont mockés."""

import base64
from datetime import timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from src.load_to_snowflake import REQUIRED_ENV_VARS, get_connection, load_to_snowflake, private_key_der

COLUMNS = ["tweet_id", "created_at", "text", "likes", "retweets", "replies", "impressions"]


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "tweet_id": "1",
                "created_at": pd.Timestamp("2026-09-08T10:00:00Z"),
                "text": "Breaking news update",
                "likes": 10,
                "retweets": 2,
                "replies": 1,
                "impressions": 500,
            }
        ],
        columns=COLUMNS,
    )


@patch("src.load_to_snowflake.write_pandas")
@patch("src.load_to_snowflake.get_connection")
def test_load_to_snowflake_inserts_rows_with_extracted_at(mock_get_connection, mock_write_pandas):
    mock_conn = MagicMock()
    mock_get_connection.return_value = mock_conn
    mock_write_pandas.return_value = (True, 1, 1, [])

    inserted_count = load_to_snowflake(_sample_df())

    assert inserted_count == 1
    mock_conn.close.assert_called_once()

    args, kwargs = mock_write_pandas.call_args
    assert args[0] is mock_conn
    assert kwargs["table_name"] == "tweet_metrics_raw"
    assert kwargs["quote_identifiers"] is False
    assert kwargs["use_logical_type"] is True
    assert kwargs["overwrite"] is False  # append-only

    written = args[1]
    assert len(written) == 1
    row = written.iloc[0]
    assert row["tweet_id"] == "1"
    assert row["likes"] == 10
    assert "extracted_at" in written.columns
    assert row["extracted_at"].tzinfo is not None
    assert row["extracted_at"].utcoffset() == timezone.utc.utcoffset(None)
    assert row["created_at"] == pd.Timestamp("2026-09-08T10:00:00Z")


@patch("src.load_to_snowflake.write_pandas")
@patch("src.load_to_snowflake.get_connection")
def test_load_to_snowflake_closes_connection_and_raises_on_copy_failure(mock_get_connection, mock_write_pandas):
    mock_conn = MagicMock()
    mock_get_connection.return_value = mock_conn
    mock_write_pandas.return_value = (False, 1, 0, [])

    with pytest.raises(RuntimeError):
        load_to_snowflake(_sample_df())
    mock_conn.close.assert_called_once()


@patch("src.load_to_snowflake.get_connection")
def test_load_to_snowflake_empty_dataframe_skips_insert(mock_get_connection):
    empty_df = pd.DataFrame(columns=COLUMNS)

    inserted_count = load_to_snowflake(empty_df)

    assert inserted_count == 0
    mock_get_connection.assert_not_called()


def test_get_connection_raises_without_env_vars(monkeypatch):
    for name in REQUIRED_ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError):
        get_connection()


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _expected_der(key) -> bytes:
    return key.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def test_private_key_der_accepts_pem_one_line_base64_and_escaped_newlines(rsa_key):
    pem = rsa_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    one_line = base64.b64encode(_expected_der(rsa_key)).decode()

    assert private_key_der(pem) == _expected_der(rsa_key)
    assert private_key_der(one_line) == _expected_der(rsa_key)
    assert private_key_der(pem.replace("\n", "\\n")) == _expected_der(rsa_key)


def test_private_key_der_decrypts_with_passphrase(rsa_key):
    encrypted_pem = rsa_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(b"s3cret"),
    ).decode()

    assert private_key_der(encrypted_pem, "s3cret") == _expected_der(rsa_key)


@patch("src.load_to_snowflake.snowflake.connector.connect")
def test_get_connection_strips_trailing_newlines_from_secrets(mock_connect, monkeypatch, rsa_key):
    # Secret GitHub collé avec un saut de ligne final : invaliderait le JWT.
    pem = rsa_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    for name in REQUIRED_ENV_VARS:
        monkeypatch.setenv(name, f"VALUE_{name}\n")
    monkeypatch.setenv("SNOWFLAKE_PRIVATE_KEY", pem + "\n")
    monkeypatch.setenv("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE", "\n")

    get_connection()

    kwargs = mock_connect.call_args.kwargs
    assert kwargs["user"] == "VALUE_SNOWFLAKE_USER"
    assert kwargs["database"] == "VALUE_SNOWFLAKE_DATABASE"
    assert kwargs["private_key"] == _expected_der(rsa_key)
