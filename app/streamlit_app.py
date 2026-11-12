"""Dashboard Streamlit de suivi de l'engagement X.

Phase 5 du pipeline : lecture seule sur les modèles dbt (tweet_metrics_daily,
stg_tweet_metrics, stg_account_daily_metrics). Aucune écriture, aucun appel à l'API X.
Un filtre Jour / Semaine / Mois regroupe les séries quotidiennes (voir periods.py).
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

from periods import GRANULARITIES, aggregate_by_period, rate

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
    le cache de 10 min sur les fonctions load_* limite les reprises.
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
def load_tweet_daily() -> pd.DataFrame:
    """Tweets agrégés par jour de publication (mart tweet_metrics_daily)."""
    with get_engine().connect() as conn:
        df = pd.read_sql(text("select * from tweet_metrics_daily order by publication_date"), conn)
    # Snowflake renvoie les identifiants non quotés en MAJUSCULES.
    df.columns = df.columns.str.lower()
    return df


@st.cache_data(ttl=600)
def load_tweets() -> pd.DataFrame:
    query = """
        select tweet_id, created_at, text, likes, retweets, replies, impressions
        from stg_tweet_metrics
        qualify row_number() over (partition by tweet_id order by extracted_at desc) = 1
        order by impressions desc
    """
    with get_engine().connect() as conn:
        df = pd.read_sql(text(query), conn)
    df.columns = df.columns.str.lower()
    return df


@st.cache_data(ttl=600)
def load_account_daily() -> pd.DataFrame:
    """Métriques quotidiennes du compte (exports CSV X Analytics)."""
    query = """
        select metric_date, impressions, engagements, profile_visits,
               new_follows, unfollows, net_follows, posts_created
        from stg_account_daily_metrics
        order by metric_date
    """
    with get_engine().connect() as conn:
        df = pd.read_sql(text(query), conn)
    df.columns = df.columns.str.lower()
    return df


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


# Période -> largeur des barres sur l'axe date, format des ticks, libellé du survol.
PERIOD_AXIS = {
    "Jour": dict(xperiod=86_400_000, tickformat="%d/%m"),
    "Semaine": dict(xperiod=7 * 86_400_000, tickformat="%d/%m"),
    "Mois": dict(xperiod="M1", tickformat="%m/%Y"),
}
PER_PERIOD = {"Jour": "par jour", "Semaine": "par semaine", "Mois": "par mois"}


def period_labels(periods: pd.DataFrame, granularity: str) -> list[str]:
    """Libellé de survol : jour, « sem. du … » ou mois, + mention des périodes incomplètes."""
    labels = []
    for start, partial in zip(periods["period_start"], periods["partial"]):
        if granularity == "Jour":
            label = f"{start:%d/%m/%Y}"
        elif granularity == "Semaine":
            label = f"sem. du {start:%d/%m/%Y}"
        else:
            label = f"{start:%m/%Y}"
        labels.append(label + (" (incomplète)" if partial else ""))
    return labels


def period_bar(periods: pd.DataFrame, column: str, unit: str, granularity: str, *, signed: bool = False) -> go.Figure:
    """Barres par période, série unique. Périodes incomplètes estompées."""
    fig = go.Figure(
        go.Bar(
            x=periods["period_start"],
            y=periods[column],
            xperiod=PERIOD_AXIS[granularity]["xperiod"],
            xperiodalignment="middle",
            marker=dict(
                color=SERIES_BLUE,
                opacity=[0.4 if p else 1.0 for p in periods["partial"]],
                cornerradius=4,
                line=dict(color=SURFACE, width=2),
            ),
            customdata=period_labels(periods, granularity),
            hovertemplate="%{customdata}<br>%{y:" + ("+," if signed else ",") + "} " + unit + "<extra></extra>",
        )
    )
    # Série unique : pas de légende, le titre nomme la mesure.
    fig.update_layout(showlegend=False, height=260, bargap=0.2)
    fig = style_axes(fig)
    fig.update_xaxes(tickformat=PERIOD_AXIS[granularity]["tickformat"])
    if signed:
        # Abonnés nets : la ligne zéro sépare gains et pertes.
        fig.update_yaxes(zeroline=True, zerolinecolor=BASELINE, zerolinewidth=1)
    return fig


def period_rate_line(periods: pd.DataFrame, rates: pd.Series, granularity: str) -> go.Figure:
    """Taux d'engagement par période : ligne 2 px, marqueurs 8 px cerclés de la surface."""
    fig = go.Figure(
        go.Scatter(
            x=periods["period_start"],
            y=rates,
            xperiod=PERIOD_AXIS[granularity]["xperiod"],
            xperiodalignment="middle",
            mode="lines+markers",
            line=dict(color=SERIES_BLUE, width=2),
            marker=dict(size=8, color=SERIES_BLUE, line=dict(color=SURFACE, width=2)),
            customdata=period_labels(periods, granularity),
            hovertemplate="%{customdata}<br>%{y:.2f} %<extra></extra>",
            connectgaps=False,
        )
    )
    fig.update_layout(showlegend=False, height=260)
    fig = style_axes(fig)
    fig.update_xaxes(tickformat=PERIOD_AXIS[granularity]["tickformat"])
    fig.update_yaxes(ticksuffix=" %", rangemode="tozero")
    return fig


def show(fig: go.Figure) -> None:
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def fmt_int(value: float) -> str:
    return f"{int(value):,}".replace(",", " ")


# ---------------------------------------------------------------- page

st.title("Geostratfor — engagement X")
st.caption("Compte suivi : @ElCambur442953 · tweets rafraîchis chaque lundi par le pipeline")

# Filtre unique, au-dessus de tous les graphiques : il s'applique aux deux sections.
granularity = st.radio("Granularité", GRANULARITIES, index=0, horizontal=True)
per = PER_PERIOD[granularity]

# --- Compte : exports X Analytics -----------------------------------------
st.header("Compte")

try:
    account_daily = load_account_daily()
except Exception as exc:  # vue absente (dbt pas encore lancé) ou connexion
    account_daily = None
    st.error(f"Lecture des métriques du compte impossible (Snowflake) : {exc}")

if account_daily is not None and account_daily.empty:
    st.info(
        "Aucune métrique de compte — charger un export X Analytics avec "
        "scripts/load_x_account_analytics.py puis lancer dbt build."
    )
elif account_daily is not None:
    first_day, last_day = pd.to_datetime(account_daily["metric_date"]).agg(["min", "max"])
    st.caption(
        f"Exports manuels x.com → Analytics → Overview · du {first_day:%d/%m/%Y} au {last_day:%d/%m/%Y} "
        f"({len(account_daily)} jours) · barres estompées = période incomplète"
    )

    totals = account_daily.sum(numeric_only=True)
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Impressions (compte)", fmt_int(totals["impressions"]))
    k2.metric("Visites de profil", fmt_int(totals["profile_visits"]))
    k3.metric(
        "Abonnés nets",
        f"{int(totals['net_follows']):+d}",
        help=f"{int(totals['new_follows'])} nouveaux, {int(totals['unfollows'])} désabonnements",
    )
    k4.metric(
        "Taux d'engagement",
        f"{totals['engagements'] / totals['impressions'] * 100:.2f} %" if totals["impressions"] else "—",
        help="Engagements / impressions sur toute la période",
    )

    account = aggregate_by_period(
        account_daily,
        "metric_date",
        granularity,
        ["impressions", "engagements", "profile_visits", "new_follows", "unfollows", "net_follows", "posts_created"],
    )
    account["engagement_rate"] = rate(account["engagements"], account["impressions"])

    # Quatre graphiques à une seule série plutôt qu'un double axe : les échelles
    # n'ont rien à voir (milliers d'impressions vs quelques abonnés).
    left, right = st.columns(2)
    with left:
        st.subheader(f"Impressions {per}")
        show(period_bar(account, "impressions", "impressions", granularity))
    with right:
        st.subheader(f"Visites de profil {per}")
        show(period_bar(account, "profile_visits", "visites", granularity))

    left, right = st.columns(2)
    with left:
        st.subheader(f"Abonnés nets {per}")
        show(period_bar(account, "net_follows", "abonnés", granularity, signed=True))
    with right:
        st.subheader(f"Taux d'engagement {per}")
        show(period_rate_line(account, account["engagement_rate"], granularity))

    with st.expander(f"Tableau du compte {per}"):
        st.dataframe(
            account,
            use_container_width=True,
            hide_index=True,
            column_order=[
                "period_start", "impressions", "engagements", "profile_visits", "new_follows",
                "unfollows", "net_follows", "posts_created", "engagement_rate", "partial",
            ],
            column_config={
                "period_start": st.column_config.DateColumn("Période (début)", format="DD/MM/YYYY"),
                "impressions": st.column_config.NumberColumn("Impressions"),
                "engagements": st.column_config.NumberColumn("Engagements"),
                "profile_visits": st.column_config.NumberColumn("Visites profil"),
                "new_follows": st.column_config.NumberColumn("Nouveaux abonnés"),
                "unfollows": st.column_config.NumberColumn("Désabonnements"),
                "net_follows": st.column_config.NumberColumn("Abonnés nets"),
                "posts_created": st.column_config.NumberColumn("Posts publiés"),
                "engagement_rate": st.column_config.NumberColumn("Engagement (%)", format="%.2f"),
                "partial": st.column_config.CheckboxColumn("Incomplète"),
            },
        )

st.divider()

# --- Tweets : pipeline hebdomadaire (API X) ---------------------------------
st.header("Tweets")

try:
    tweet_daily = load_tweet_daily()
    tweets = load_tweets()
except Exception as exc:  # connexion/credentials : message lisible plutôt qu'une stack trace
    st.error(f"Connexion à Snowflake impossible : {exc}")
    st.stop()

if tweet_daily.empty:
    # La section compte, au-dessus, reste visible : on n'arrête que la partie tweets.
    st.info("Aucun tweet chargé — lancer le pipeline hebdomadaire d'abord.")
    st.stop()

first_day, last_day = pd.to_datetime(tweet_daily["publication_date"]).agg(["min", "max"])
st.caption(
    f"Tweets publiés du {first_day:%d/%m/%Y} au {last_day:%d/%m/%Y} (jour de publication, UTC) · "
    "métriques du dernier relevé du pipeline"
)

totals = tweet_daily.sum(numeric_only=True)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Tweets suivis", fmt_int(totals["tweet_count"]))
c2.metric("Impressions totales", fmt_int(totals["total_impressions"]))
c3.metric(
    "Impressions / tweet",
    f"{totals['total_impressions'] / totals['tweet_count']:.1f}" if totals["tweet_count"] else "—",
)
c4.metric(
    "Taux d'engagement",
    f"{(totals['total_likes'] + totals['total_retweets']) / totals['total_impressions'] * 100:.2f} %"
    if totals["total_impressions"]
    else "—",
    help="(Likes + retweets) / impressions sur toute la période",
)

tweet_periods = aggregate_by_period(
    tweet_daily,
    "publication_date",
    granularity,
    ["tweet_count", "total_impressions", "total_likes", "total_retweets", "total_replies"],
)

# Graphiques séparés plutôt qu'un double axe : impressions et interactions
# ne sont pas sur la même échelle.
left, right = st.columns(2)
with left:
    st.subheader(f"Impressions {per}")
    show(period_bar(tweet_periods, "total_impressions", "impressions", granularity))
with right:
    st.subheader(f"Tweets publiés {per}")
    show(period_bar(tweet_periods, "tweet_count", "tweets", granularity))

st.subheader(f"Interactions {per}")
fig = go.Figure()
for name, column, color in [
    ("Likes", "total_likes", SERIES_BLUE),
    ("Retweets", "total_retweets", SERIES_ORANGE),
    ("Réponses", "total_replies", SERIES_AQUA),
]:
    fig.add_bar(
        x=tweet_periods["period_start"],
        y=tweet_periods[column],
        xperiod=PERIOD_AXIS[granularity]["xperiod"],
        xperiodalignment="middle",
        name=name,
        marker=dict(color=color, cornerradius=4, line=dict(color=SURFACE, width=2)),
        customdata=period_labels(tweet_periods, granularity),
        hovertemplate="%{customdata}<br>" + name + " : %{y}<extra></extra>",
    )
fig.update_layout(barmode="group", height=300, bargap=0.3, bargroupgap=0.05)
fig = style_axes(fig)
fig.update_xaxes(tickformat=PERIOD_AXIS[granularity]["tickformat"])
show(fig)

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
show(fig)

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
