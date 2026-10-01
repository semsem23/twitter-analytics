"""Dashboard Streamlit de suivi de l'engagement X.

Phase 5 du pipeline : lecture seule sur les modèles dbt (tweet_engagement_weekly
et stg_tweet_metrics). Aucune écriture, aucun appel à l'API X.
"""

from __future__ import annotations

import base64
import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from cryptography.hazmat.primitives import serialization
from snowflake.sqlalchemy import URL
from sqlalchemy import create_engine, text

# Palette validée (mode clair) — voir la skill dataviz :
# lightness band / chroma floor / séparation CVD / plancher vision normale OK.
SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"
SERIES_BLUE = "#2a78d6"
SERIES_ORANGE = "#eb6834"
SERIES_AQUA = "#1baf7a"

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'

st.set_page_config(page_title="Geostratfor — Engagement X", page_icon="📊", layout="wide")


def _setting(key: str, default: str | None = None) -> str:
    """Lit un paramètre : variables d'environnement (.env en local) puis secrets Streamlit.

    L'environnement est consulté en premier à dessein — toucher `st.secrets` sans
    fichier secrets.toml fait afficher une erreur par Streamlit lui-même.
    """
    # strip() : un saut de ligne final dans un secret invalide le JWT Snowflake.
    value = os.environ.get(key, "").strip()
    if value:
        return value
    try:
        # load_if_toml_exists() n'affiche rien sans secrets.toml (cas local avec
        # .env et paramètre optionnel absent, ex. SNOWFLAKE_PRIVATE_KEY_PASSPHRASE).
        if st.secrets.load_if_toml_exists() and key in st.secrets:
            return str(st.secrets[key]).strip()
    except Exception:
        pass
    if default is not None:
        return default
    raise RuntimeError(f"Paramètre manquant : {key} (variable d'environnement ou secret Streamlit).")


def _private_key_der(private_key: str, passphrase: str | None) -> bytes:
    """Clé privée -> DER PKCS#8, même convention que src/load_to_snowflake.py et dbt.

    PEM complet si la valeur commence par "-", sinon base64 du DER sur une ligne.
    """
    value = private_key.strip().replace("\\n", "\n")
    password = passphrase.encode() if passphrase else None
    if value.startswith("-"):
        key = serialization.load_pem_private_key(value.encode(), password=password)
    else:
        key = serialization.load_der_private_key(base64.b64decode(value), password=password)
    return key.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


@st.cache_resource
def get_engine():
    """Connexion SQLAlchemy à Snowflake (utilisateur de service, paire de clés).

    Chaque requête non mise en cache réveille le warehouse (facturé 60 s minimum) :
    le cache de 10 min sur load_weekly / load_tweets limite les reprises.
    """
    url = URL(
        account=_setting("SNOWFLAKE_ACCOUNT"),
        user=_setting("SNOWFLAKE_USER"),
        role=_setting("SNOWFLAKE_ROLE"),
        warehouse=_setting("SNOWFLAKE_WAREHOUSE"),
        database=_setting("SNOWFLAKE_DATABASE"),
        schema=_setting("SNOWFLAKE_SCHEMA"),
    )
    private_key = _private_key_der(
        _setting("SNOWFLAKE_PRIVATE_KEY"),
        _setting("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE", ""),
    )
    return create_engine(url, connect_args={"private_key": private_key}, pool_pre_ping=True)


@st.cache_data(ttl=600)
def load_weekly() -> pd.DataFrame:
    with get_engine().connect() as conn:
        df = pd.read_sql(text("select * from tweet_engagement_weekly order by extraction_week"), conn)
    # Snowflake renvoie les identifiants non quotés en MAJUSCULES.
    df.columns = df.columns.str.lower()
    return df


@st.cache_data(ttl=600)
def load_tweets() -> pd.DataFrame:
    query = """
        select tweet_id, created_at, text, likes, retweets, replies, impressions, extraction_week
        from stg_tweet_metrics
        order by impressions desc
    """
    with get_engine().connect() as conn:
        df = pd.read_sql(text(query), conn)
    df.columns = df.columns.str.lower()
    return df


@st.cache_data(ttl=600)
def load_account_weekly() -> tuple[pd.DataFrame, pd.Series]:
    """Mart hebdomadaire du compte (exports CSV X) + période couverte par les données."""
    with get_engine().connect() as conn:
        weekly_df = pd.read_sql(text("select * from account_metrics_weekly order by metric_week"), conn)
        coverage = pd.read_sql(
            text(
                "select min(metric_date) as first_day, max(metric_date) as last_day, count(*) as days "
                "from stg_account_daily_metrics"
            ),
            conn,
        )
    # Snowflake renvoie les identifiants non quotés en MAJUSCULES.
    weekly_df.columns = weekly_df.columns.str.lower()
    coverage.columns = coverage.columns.str.lower()
    return weekly_df, coverage.iloc[0]


def style_axes(fig: go.Figure, *, show_grid_y: bool = True) -> go.Figure:
    """Chrome récessif : grille fine, axes discrets, texte en encre neutre."""
    fig.update_layout(
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(family=FONT, color=INK_SECONDARY, size=13),
        margin=dict(l=8, r=8, t=8, b=8),
        hoverlabel=dict(font_family=FONT, font_size=13),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, title=None),
    )
    fig.update_xaxes(showgrid=False, linecolor=BASELINE, tickcolor=BASELINE, tickfont=dict(color=INK_MUTED))
    fig.update_yaxes(
        showgrid=show_grid_y,
        gridcolor=GRIDLINE,
        zeroline=False,
        linecolor=BASELINE,
        tickfont=dict(color=INK_MUTED),
    )
    return fig


def weekly_label(series: pd.Series) -> list[str]:
    return [pd.Timestamp(v).strftime("sem. %d %b") for v in series]


def account_weekly_bar(df: pd.DataFrame, column: str, unit: str, *, signed: bool = False) -> go.Figure:
    """Barres hebdomadaires, série unique. Semaines partielles (< 7 jours) estompées."""
    partial = df["days_covered"] < 7
    fig = go.Figure(
        go.Bar(
            x=weekly_label(df["metric_week"]),
            y=df[column],
            marker=dict(
                color=SERIES_BLUE,
                opacity=[0.4 if p else 1.0 for p in partial],
                cornerradius=4,
                line=dict(color=SURFACE, width=2),
            ),
            customdata=[
                f"semaine partielle : {d} j" if p else "semaine complète"
                for d, p in zip(df["days_covered"], partial)
            ],
            hovertemplate="%{x}<br>%{y:" + ("+," if signed else ",") + "} " + unit
            + "<br>%{customdata}<extra></extra>",
        )
    )
    # Série unique : pas de légende, le titre nomme la mesure.
    fig.update_layout(showlegend=False, height=260, bargap=0.35)
    fig = style_axes(fig)
    if signed:
        # Abonnés nets : la ligne zéro sépare gains et pertes.
        fig.update_yaxes(zeroline=True, zerolinecolor=BASELINE, zerolinewidth=1)
    return fig


# ---------------------------------------------------------------- page

st.title("Geostratfor — engagement X")
st.caption("Compte suivi : @ElCambur442953 · données rafraîchies chaque lundi 9h UTC")

# --- Compte : exports X Analytics -----------------------------------------
st.header("Compte")

try:
    account_weekly, coverage = load_account_weekly()
except Exception as exc:  # table absente (dbt pas encore lancé) ou connexion
    account_weekly, coverage = pd.DataFrame(), None
    st.error(f"Lecture des métriques du compte impossible (Snowflake) : {exc}")

if coverage is not None and account_weekly.empty:
    st.info(
        "Aucune métrique de compte — charger un export X Analytics avec "
        "scripts/load_x_account_analytics.py puis lancer dbt build."
    )
elif not account_weekly.empty:
    st.caption(
        f"Exports manuels x.com → Analytics → Overview · du {pd.Timestamp(coverage['first_day']):%d/%m/%Y} "
        f"au {pd.Timestamp(coverage['last_day']):%d/%m/%Y} ({int(coverage['days'])} jours) · "
        "barres estompées = semaine incomplète"
    )

    acc_impressions = int(account_weekly["total_impressions"].sum())
    acc_engagements = int(account_weekly["total_engagements"].sum())
    acc_net_follows = int(account_weekly["net_follows"].sum())

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Impressions (compte)", f"{acc_impressions:,}".replace(",", " "))
    k2.metric("Visites de profil", f"{int(account_weekly['total_profile_visits'].sum()):,}".replace(",", " "))
    k3.metric(
        "Abonnés nets",
        f"{acc_net_follows:+d}",
        help=f"{int(account_weekly['total_new_follows'].sum())} nouveaux, "
        f"{int(account_weekly['total_unfollows'].sum())} désabonnements",
    )
    k4.metric(
        "Taux d'engagement",
        f"{acc_engagements / acc_impressions * 100:.2f} %" if acc_impressions else "—",
        help="Engagements / impressions sur toute la période",
    )

    # Quatre graphiques à une seule série plutôt qu'un double axe : les échelles
    # n'ont rien à voir (milliers d'impressions vs quelques abonnés).
    row1_left, row1_right = st.columns(2)
    with row1_left:
        st.subheader("Impressions par semaine")
        st.plotly_chart(
            account_weekly_bar(account_weekly, "total_impressions", "impressions"),
            use_container_width=True,
            config={"displayModeBar": False},
        )
    with row1_right:
        st.subheader("Visites de profil par semaine")
        st.plotly_chart(
            account_weekly_bar(account_weekly, "total_profile_visits", "visites"),
            use_container_width=True,
            config={"displayModeBar": False},
        )

    row2_left, row2_right = st.columns(2)
    with row2_left:
        st.subheader("Abonnés nets par semaine")
        st.plotly_chart(
            account_weekly_bar(account_weekly, "net_follows", "abonnés", signed=True),
            use_container_width=True,
            config={"displayModeBar": False},
        )
    with row2_right:
        st.subheader("Taux d'engagement par semaine")
        fig = go.Figure(
            go.Scatter(
                x=weekly_label(account_weekly["metric_week"]),
                y=account_weekly["engagement_rate"].astype(float),
                mode="lines+markers",
                line=dict(color=SERIES_BLUE, width=2),
                marker=dict(size=8, color=SERIES_BLUE, line=dict(color=SURFACE, width=2)),
                hovertemplate="%{x}<br>%{y:.2f} %<extra></extra>",
            )
        )
        fig.update_layout(showlegend=False, height=260, hovermode="x")
        fig = style_axes(fig)
        fig.update_yaxes(ticksuffix=" %", rangemode="tozero")
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    with st.expander("Tableau hebdomadaire du compte"):
        st.dataframe(
            account_weekly[
                [
                    "metric_week", "days_covered", "total_impressions", "total_engagements",
                    "total_profile_visits", "total_new_follows", "total_unfollows", "net_follows",
                    "total_posts_created", "engagement_rate",
                ]
            ],
            use_container_width=True,
            hide_index=True,
            column_config={
                "metric_week": st.column_config.DateColumn("Semaine du", format="DD/MM/YYYY"),
                "days_covered": st.column_config.NumberColumn("Jours"),
                "total_impressions": st.column_config.NumberColumn("Impressions"),
                "total_engagements": st.column_config.NumberColumn("Engagements"),
                "total_profile_visits": st.column_config.NumberColumn("Visites profil"),
                "total_new_follows": st.column_config.NumberColumn("Nouveaux abonnés"),
                "total_unfollows": st.column_config.NumberColumn("Désabonnements"),
                "net_follows": st.column_config.NumberColumn("Abonnés nets"),
                "total_posts_created": st.column_config.NumberColumn("Posts publiés"),
                "engagement_rate": st.column_config.NumberColumn("Engagement (%)", format="%.2f"),
            },
        )

st.divider()

# --- Tweets : pipeline hebdomadaire (API X) ---------------------------------
st.header("Tweets")

try:
    weekly = load_weekly()
    tweets = load_tweets()
except Exception as exc:  # connexion/credentials : message lisible plutôt qu'une stack trace
    st.error(f"Connexion à Snowflake impossible : {exc}")
    st.stop()

if weekly.empty:
    # La section compte, au-dessus, reste visible : on n'arrête que la partie tweets.
    st.info("Aucune donnée dans tweet_engagement_weekly — lancer le pipeline hebdomadaire d'abord.")
    st.stop()

# --- Indicateurs clés ------------------------------------------------------
latest = weekly.iloc[-1]
total_impressions = int(weekly["total_impressions"].sum())
total_tweets = int(weekly["tweet_count"].sum())
avg_rate = float(weekly["engagement_rate"].astype(float).mean())

c1, c2, c3, c4 = st.columns(4)
c1.metric("Tweets suivis", f"{total_tweets:,}".replace(",", " "))
c2.metric("Impressions totales", f"{total_impressions:,}".replace(",", " "))
c3.metric("Impressions / tweet", f"{total_impressions / total_tweets:.1f}" if total_tweets else "—")
c4.metric("Taux d'engagement moyen", f"{avg_rate:.2f} %")

if len(weekly) == 1:
    st.caption("Une seule semaine de données pour l'instant — les tendances apparaîtront après plusieurs runs hebdomadaires.")

st.divider()

# --- Évolution hebdomadaire ------------------------------------------------
# Deux graphiques séparés plutôt qu'un double axe : impressions et interactions
# ne sont pas sur la même échelle (1718 vs 1).
labels = weekly_label(weekly["extraction_week"])

left, right = st.columns(2)

with left:
    st.subheader("Impressions par semaine")
    fig = go.Figure(
        go.Bar(
            x=labels,
            y=weekly["total_impressions"],
            marker=dict(color=SERIES_BLUE, cornerradius=4),
            text=weekly["total_impressions"],
            textposition="outside",
            textfont=dict(color=INK_SECONDARY),
            hovertemplate="%{x}<br>%{y:,} impressions<extra></extra>",
            width=0.5,
        )
    )
    # Série unique : pas de légende, le titre nomme la mesure.
    fig.update_layout(showlegend=False, height=300)
    st.plotly_chart(style_axes(fig), use_container_width=True, config={"displayModeBar": False})

with right:
    st.subheader("Interactions par semaine")
    fig = go.Figure()
    for name, column, color in [
        ("Likes", "total_likes", SERIES_BLUE),
        ("Retweets", "total_retweets", SERIES_ORANGE),
        ("Réponses", "total_replies", SERIES_AQUA),
    ]:
        fig.add_bar(
            x=labels,
            y=weekly[column],
            name=name,
            marker=dict(color=color, cornerradius=4, line=dict(color=SURFACE, width=2)),
            hovertemplate="%{x}<br>" + name + " : %{y}<extra></extra>",
        )
    fig.update_layout(barmode="group", height=300, bargap=0.4, bargroupgap=0.05)
    st.plotly_chart(style_axes(fig), use_container_width=True, config={"displayModeBar": False})

st.divider()

# --- Tweets les plus vus ---------------------------------------------------
st.subheader("Tweets les plus vus")
top = tweets.head(10).iloc[::-1]  # inversé : le plus vu en haut du graphe horizontal
short = [t if len(t) <= 60 else t[:57] + "…" for t in top["text"]]

fig = go.Figure(
    go.Bar(
        x=top["impressions"],
        y=short,
        orientation="h",
        marker=dict(color=SERIES_BLUE, cornerradius=4),
        text=top["impressions"],
        textposition="outside",
        textfont=dict(color=INK_SECONDARY),
        hovertemplate="%{y}<br>%{x:,} impressions<extra></extra>",
    )
)
fig.update_layout(showlegend=False, height=380)
fig = style_axes(fig, show_grid_y=False)
fig.update_xaxes(showgrid=True, gridcolor=GRIDLINE)
st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

# --- Tableau détaillé ------------------------------------------------------
st.subheader("Détail par tweet")
detail = tweets.copy()
detail["lien"] = "https://x.com/ElCambur442953/status/" + detail["tweet_id"]
detail = detail[["created_at", "text", "impressions", "likes", "retweets", "replies", "lien"]]

st.dataframe(
    detail,
    use_container_width=True,
    hide_index=True,
    column_config={
        "created_at": st.column_config.DatetimeColumn("Publié le", format="DD/MM/YYYY HH:mm"),
        "text": st.column_config.TextColumn("Tweet", width="large"),
        "impressions": st.column_config.NumberColumn("Impressions"),
        "likes": st.column_config.NumberColumn("Likes"),
        "retweets": st.column_config.NumberColumn("RT"),
        "replies": st.column_config.NumberColumn("Réponses"),
        "lien": st.column_config.LinkColumn("Voir", display_text="ouvrir"),
    },
)
