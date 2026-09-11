"""Dashboard Streamlit de suivi de l'engagement X.

Phase 5 du pipeline : lecture seule sur les modèles dbt (tweet_engagement_weekly
et stg_tweet_metrics). Aucune écriture, aucun appel à l'API X.
"""

from __future__ import annotations

import os
from urllib.parse import quote_plus

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
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
    value = os.environ.get(key)
    if value:
        return value
    try:
        if key in st.secrets:
            return str(st.secrets[key])
    except Exception:
        pass
    if default is not None:
        return default
    raise RuntimeError(f"Paramètre manquant : {key} (variable d'environnement ou secret Streamlit).")


@st.cache_resource
def get_engine():
    """Connexion SQLAlchemy au Session Pooler Supabase (port 5432, IPv4)."""
    user = quote_plus(_setting("SUPABASE_DB_USER"))
    password = quote_plus(_setting("SUPABASE_DB_PASSWORD"))
    host = _setting("SUPABASE_DB_HOST")
    port = _setting("SUPABASE_DB_PORT", "5432")
    dbname = _setting("SUPABASE_DB_NAME", "postgres")
    return create_engine(f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{dbname}", pool_pre_ping=True)


@st.cache_data(ttl=600)
def load_weekly() -> pd.DataFrame:
    with get_engine().connect() as conn:
        return pd.read_sql(text("select * from tweet_engagement_weekly order by extraction_week"), conn)


@st.cache_data(ttl=600)
def load_tweets() -> pd.DataFrame:
    query = """
        select tweet_id, created_at, text, likes, retweets, replies, impressions, extraction_week
        from stg_tweet_metrics
        order by impressions desc
    """
    with get_engine().connect() as conn:
        return pd.read_sql(text(query), conn)


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


# ---------------------------------------------------------------- page

st.title("Geostratfor — engagement X")
st.caption("Compte suivi : @ElCambur442953 · données rafraîchies chaque lundi 9h UTC")

try:
    weekly = load_weekly()
    tweets = load_tweets()
except Exception as exc:  # connexion/credentials : message lisible plutôt qu'une stack trace
    st.error(f"Connexion à Supabase impossible : {exc}")
    st.stop()

if weekly.empty:
    st.warning("Aucune donnée dans tweet_engagement_weekly — lancer le pipeline d'abord.")
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
