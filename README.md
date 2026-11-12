# Twitter Analytics Pipeline

Weekly tracking of the public metrics of the X account **@ElCambur442953** (Geostratfor, a news-publishing bot).

## Architecture

```
X API ──> GitHub Actions ──> Snowflake ──> dbt (dbt-snowflake) ──> Streamlit
          (Monday, 2 crons)  TWITTER_ANALYTICS.ANALYTICS
                             X-SMALL warehouse, auto-suspend 60 s
```

| Step | Tool | Role |
|---|---|---|
| Extraction | Python (Tweepy) | Fetches the public metrics of the previous calendar week's tweets |
| Loading | Python (`snowflake-connector-python`, `write_pandas`) | Append-only into `TWEET_METRICS_RAW`, one UTC `extracted_at` per run |
| Storage | Snowflake (Standard edition) | Raw table + dbt models in the `ANALYTICS` schema |
| Transformation | dbt (`dbt-snowflake`) | Staging → marts, data quality tests |
| Automation | GitHub Actions | Weekly cron (Monday morning, + afternoon catch-up) |
| Visualization | Streamlit (`snowflake-sqlalchemy`) | Engagement tracking dashboard |

All connections (loader, dbt, Streamlit) use the same Snowflake service user with **key-pair authentication** — no password.

## Repo structure

```
twitter-analytics-pipeline/
├── .github/workflows/weekly_pipeline.yml
├── src/
│   ├── x_api_client.py
│   ├── load_to_snowflake.py
│   └── run_pipeline.py
├── scripts/
│   ├── migrate_supabase_to_snowflake.py   # one-off, to delete after the cutover
│   └── load_x_account_analytics.py        # X "Account overview" CSV exports
├── sql/
│   └── snowflake_setup.sql
├── tests/
│   ├── test_x_api_client.py
│   ├── test_load_to_snowflake.py
│   └── test_migrate_supabase_to_snowflake.py
├── dbt_project/
│   ├── dbt_project.yml
│   ├── profiles.yml
│   ├── macros/
│   │   └── week_start_monday.sql
│   ├── tests/
│   │   └── assert_unique_tweet_extraction_week.sql
│   └── models/
│       ├── staging/
│       │   ├── stg_tweet_metrics.sql
│       │   └── sources.yml
│       └── marts/
│           └── tweet_engagement_weekly.sql
├── app/
│   ├── streamlit_app.py
│   └── periods.py                       # day / week / month grouping
├── .streamlit/config.toml
├── .env.example
├── requirements.txt
└── .gitignore
```

## Setup

### 1. RSA key pair (service user)

```bash
openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out rsa_key.p8 -nocrypt
openssl rsa -in rsa_key.p8 -pubout -out rsa_key.pub
```

`rsa_key.p8` is the **private** key: never commit it or paste it anywhere other than `.env`, GitHub secrets and Streamlit secrets. For an encrypted key, drop `-nocrypt` and set `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE`.

### 2. Snowflake objects

In a Snowsight worksheet (user with `SYSADMIN` and `SECURITYADMIN`), run [`sql/snowflake_setup.sql`](sql/snowflake_setup.sql) after replacing `<RSA_PUBLIC_KEY>` with the contents of `rsa_key.pub` **without** the `BEGIN/END` lines. The script is idempotent and creates:

- the `TWITTER_ANALYTICS_WH` warehouse (X-SMALL, `AUTO_SUSPEND = 60`, `AUTO_RESUME = TRUE`);
- the `TWITTER_ANALYTICS` database and the `ANALYTICS` schema;
- the `TWEET_METRICS_RAW` raw table;
- the `TWITTER_ANALYTICS_ROLE` role and the `TWITTER_ANALYTICS_SVC` service user (`TYPE = SERVICE`: password login is impossible);
- minimal grants (select/insert on the raw table, view/table creation for dbt).

An optional block at the end of the script creates a *resource monitor* that caps monthly spend.

### 3. Environment variables

Copy `.env.example` to `.env` and fill in the `SNOWFLAKE_*` variables. `SNOWFLAKE_ACCOUNT` is the `<orgname>-<account_name>` identifier (Snowsight → account menu → *Account details*).

`SNOWFLAKE_PRIVATE_KEY` accepts two formats (same convention as dbt-snowflake):

- the body of `rsa_key.p8` on a single line — the simplest for `.env`:
  `grep -v "PRIVATE KEY" rsa_key.p8 | tr -d '\n'`
- the full PEM, `-----BEGIN ...` included (GitHub secrets, or `.env` inside double quotes).

`X_OWNED_USERNAME` must match the account tied to the Bearer Token (checked before any API call to stay on the Owned Read rate — `GET /2/users/me` can't be used here because it requires user-context auth, which is incompatible with a Bearer Token alone).

Never commit `.env` (already excluded via `.gitignore`).

### 4. Importing the Supabase history (one time only)

With `SUPABASE_URL` / `SUPABASE_KEY` and the `SNOWFLAKE_*` variables in `.env`:

```bash
python scripts/migrate_supabase_to_snowflake.py --dry-run   # counts without writing
python scripts/migrate_supabase_to_snowflake.py
```

The script is idempotent: it skips rows already present (same `tweet_id` + same `extracted_at`) and can be re-run without risk of duplicates. The original `extracted_at` values are preserved, so `extraction_week` is identical on both sides.

### 4 bis. Daily account metrics (X CSV exports)

Daily impressions, profile visits, new follows / unfollows… are **account-level** metrics that the X API doesn't provide: they come from the manual export **x.com → Analytics → Overview → Export** (`account_overview_analytics.csv`).

```bash
python scripts/load_x_account_analytics.py ~/Downloads/account_overview_analytics.csv --dry-run
python scripts/load_x_account_analytics.py ~/Downloads/account_overview_analytics.csv
```

The script creates `ACCOUNT_DAILY_METRICS_RAW` if needed and does a `MERGE` on the date: one row per day, exports may overlap, and for a given day the row from the most recent export wins (X revises the latest days). Reloading an old file never overwrites fresher figures.

dbt models: `stg_account_daily_metrics` (day + Monday-based week, `net_follows`) and `account_metrics_weekly` (same Monday → Sunday split as `tweet_engagement_weekly`, `days_covered < 7` = partial week).

The file can be the original X CSV or an `.xlsx` saved from Excel. Do not re-save the CSV from Excel (it rewrites the dates, e.g. `27/09/2026`): the script rejects it with an explicit message.

### 4 ter. Dashboard: day / week / month

The dashboard reads **daily** series and a *Granularity* filter (Day / Week / Month) groups them at display time, for both sections:

- **Account**: `stg_account_daily_metrics` (one row per day of X export);
- **Tweets**: `tweet_metrics_daily`, tweets aggregated by **publication day** (UTC), keeping the latest snapshot of each tweet.

The pipeline runs once a week, but each tweet is stored individually with its publication time: a Monday run fills the 7 days of the past week. Weeks run Monday to Sunday, months are calendar months; a period the data doesn't fully cover is dimmed ("incomplete" on hover). Grouping lives in `app/periods.py` (tested in `tests/test_periods.py`).

To keep the series up to date: redo an export (X offers up to 90 days) and re-run the script, for example once a month.

### 5. dbt connection

`dbt_project/profiles.yml` is versioned in the repo (not in `~/.dbt/`) and reads `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_ROLE`, `SNOWFLAKE_WAREHOUSE`, `SNOWFLAKE_DATABASE`, `SNOWFLAKE_SCHEMA`, `SNOWFLAKE_PRIVATE_KEY` and `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE` (optional). Since it sits next to `dbt_project.yml`, `--profiles-dir dbt_project` is required on every dbt command.

**Warning**: unlike `run_pipeline.py`, dbt does not load `.env` automatically (`env_var()` only reads real shell environment variables). Locally, prefix dbt commands with `dotenv run --` (provided by `python-dotenv`) from the repo root:

```bash
dotenv run -- dbt debug --project-dir dbt_project --profiles-dir dbt_project
```

`extraction_week` is the Monday (UTC) of the covered week, computed with `dayofweekiso()` (`week_start_monday` macro) rather than `date_trunc('week', ...)`, whose result depends on the Snowflake `WEEK_START` parameter.

### 6. GitHub Actions secrets

Under **Settings → Secrets and variables → Actions**, add `X_BEARER_TOKEN`, `X_OWNED_USERNAME` and every `SNOWFLAKE_*` variable from `.env.example` (`SNOWFLAKE_PRIVATE_KEY`: paste the full PEM, multi-line is accepted). The `SUPABASE_*` variables are no longer used by this workflow.

### 7. Streamlit secrets (Community Cloud)

Under **App settings → Secrets**, the same keys in TOML format:

```toml
SNOWFLAKE_ACCOUNT = "<orgname>-<account_name>"
SNOWFLAKE_USER = "TWITTER_ANALYTICS_SVC"
SNOWFLAKE_ROLE = "TWITTER_ANALYTICS_ROLE"
SNOWFLAKE_WAREHOUSE = "TWITTER_ANALYTICS_WH"
SNOWFLAKE_DATABASE = "TWITTER_ANALYTICS"
SNOWFLAKE_SCHEMA = "ANALYTICS"
SNOWFLAKE_PRIVATE_KEY = """-----BEGIN PRIVATE KEY-----
...
-----END PRIVATE KEY-----"""
```

### 8. Running manually (testing)

```bash
pip install -r requirements.txt
python src/run_pipeline.py
dotenv run -- dbt run --project-dir dbt_project --profiles-dir dbt_project
dotenv run -- dbt test --project-dir dbt_project --profiles-dir dbt_project
streamlit run app/streamlit_app.py
```

## Automation

The `.github/workflows/weekly_pipeline.yml` workflow runs every Monday at 01:10 UTC (3:10 Paris in summer time, 2:10 in winter), with a second catch-up cron at 13:17 UTC, and can be triggered manually from the **Actions** tab (`workflow_dispatch`).

**Important**: GitHub only runs `schedule` triggers on the default branch. As long as this version lives on the `snowflake-migration` branch, it only runs when triggered manually:

```bash
gh workflow run weekly_pipeline.yml --ref snowflake-migration
```

## Estimated costs

The volume (~60 tweets/week, a few hundred rows) is negligible: the Snowflake cost comes only from warehouse uptime. X-SMALL = 1 credit/hour, billed per second with a **60 s minimum per resume**, plus 60 s of idle time before auto-suspend. A Standard credit costs ≈ $2 (AWS US) to ~$2.60 (EU regions).

| Item | Estimated monthly cost |
|---|---|
| X API (60 tweets/week, Owned Reads) | ~$0.30 |
| GitHub Actions (private repo, a few minutes/month) | $0 (well under the free quota) |
| Snowflake — pipeline (2 runs/week × ~2-4 min of warehouse) | ~$1-2 |
| Snowflake — dashboard (each uncached visit wakes the warehouse for ≥ 2 min) | ~$2-10 depending on traffic |
| Snowflake — storage (< 1 GB) and cloud services (< 10% of compute) | ~$0 |
| Streamlit Community Cloud | $0 |
| **Total Snowflake Standard** | **~$3-12 / month** |

To cap spending: enable the optional *resource monitor* in `sql/snowflake_setup.sql` and keep the dashboard's 10-minute cache (`ttl=600`).

## Notes

- Matching against X trends was dropped after testing (see project history) in favor of pure engagement-metric tracking.
- The first run showed almost no engagement despite decent impressions — something to watch in the Streamlit dashboard once several weeks of data have accumulated.
- The Supabase version (Postgres + `dbt-postgres`) stays on the `main` branch until the cutover is complete.
