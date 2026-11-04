# MTG Meta Scraper

Collects tournament results for Magic's competitive formats from [MTGTop8](https://mtgtop8.com/), stores them in TimescaleDB and shows them in a Grafana dashboard to see **which decks are climbing the meta**.

Formats: Standard, Pioneer, Modern, Legacy, Vintage, Pauper, Duel Commander, cEDH.

## What the dashboard shows

- **Meta share (%)**: percentage of a format's decks that belong to each archetype, over time (MTGO Leagues included).
- **Performance**: average points per placement (1st = 8, 2nd = 6, 3-4 = 4, 5-8 = 2, 9-16 = 1, below = 0.5), over time. MTGO Leagues excluded. It stands in for winrate, which MTGTop8 does not publish.
- **Rising decks**: last period versus the previous one, with the change in share, performance and % of tournament wins.
- **Detail**: the deck names that make up an archetype.

Archetypes are MTGTop8's categories (e.g. "Rakdos Aggro"). In Duel Commander and cEDH decks are grouped by **commander**: partner pairs count as a single entry.

Variables at the top: format, time bucket, top N archetypes, "rising" period, minimum decks. For Commander formats it helps to raise "Minimum decks" and lower "Top N", since there are many commanders with few decks each.

## Getting started

```bash
cp .env.example .env        # set MTG_META_SCRAPER_DB_READER_PASSWORD; optionally the Grafana password, formats, request rate
docker compose up -d --build db grafana
docker compose run -d --rm scraper python -m mtg_meta_scraper backfill   # once, in the background: the history
```

- Grafana: <http://localhost:3000>, login only (anonymous access and sign-up are off): admin / `GF_ADMIN_PASSWORD`. `GF_ADMIN_PASSWORD` only applies when the Grafana volume is first created.
- The backfill loads the **last 90 days**; run it **once**, at the first deploy. It makes roughly one request every 1.5 s and needs one per deck, so the backfill takes **about half a day** (roughly 9-12 hours). It goes from the most recent events to the oldest, so the dashboard fills up with recent data right away. If interrupted, it resumes where it left off. For a longer history raise `MTG_META_SCRAPER_BACKFILL_DAYS` (e.g. `183` for 6 months: about a day). The update never deletes old data, so the history grows on its own.
- The daily data comes from `python -m mtg_meta_scraper update` (the image's default command), see [Daily update](#daily-update).

Logs: `docker compose logs -f` (or the job's pod logs on Kubernetes). After the backfill, `docker compose up -d` (or the scheduler) runs the update.

Grafana reads the database as `grafana_reader`, a role that can only read (`SELECT` on every table and view, including those added by later migrations). The scraper's migrations create it with `MTG_META_SCRAPER_DB_READER_PASSWORD` when it is missing, also on a database volume that already exists. Changing that password later is done by hand (`ALTER ROLE grafana_reader PASSWORD '...'`). If the scraper's database user may not create roles (a managed database, a restricted user), create it once by hand, `CREATE ROLE grafana_reader LOGIN PASSWORD '...';`, and the migrations just grant it read access.

## Commands

```bash
docker compose run --rm scraper python -m mtg_meta_scraper validate   # compare our shares with the site's
```

Never run two scraper commands at the same time (e.g. a backfill by hand while the scheduled update is running), or requests to the site will double.

If the scraper hits 100 missing or failing pages in a row (site down, blocked, HTML changed) it stops by itself to avoid hammering the site. The update retries (see below); the backfill exits non-zero and, run again, resumes where it left off.

## Daily update

`python -m mtg_meta_scraper update` is a one-shot command meant to run **once a day** from an external scheduler (a Kubernetes `CronJob`, a host cron running `docker compose run --rm scraper`): the scraper has no internal scheduler. It collects the new tournaments, rechecks those from the last `MTG_META_SCRAPER_RECHECK_DAYS` days and refreshes the macro-categories.

- A failed update (site unreachable, network or database error, or 100 failing pages in a row) is retried up to **4 attempts, 15 minutes apart** (about 45 minutes of waiting at most, plus the runs).
- The command exits non-zero if the last attempt still failed, so the scheduler can report it.
- A retry starts over: events already saved are not downloaded again, the recent ones are rechecked again.
- On Kubernetes use `concurrencyPolicy: Forbid`, and don't schedule the update while the backfill Job is running.

Postgres (`5432`) and Grafana (`3000`) are exposed on `127.0.0.1` only.

For each format, `validate` compares the top 10 archetypes of the site's "Metagame Breakdown – Last 2 Months" with our percentages over the last 60 days. It flags a `MISMATCH` beyond ±2 points and exits with code 1. Until the backfill is complete, or if it covers fewer than 60 days, mismatches are expected.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `MTG_META_SCRAPER_FORMATS` | `ST,PI,MO,LE,VI,PAU,EDH,cEDH` | Formats to collect |
| `MTG_META_SCRAPER_BACKFILL_DAYS` | `90` | Backfill depth |
| `MTG_META_SCRAPER_REQUEST_INTERVAL` | `1.5` | Seconds between two requests (don't lower it too much) |
| `MTG_META_SCRAPER_RECHECK_DAYS` | `3` | Days of tournaments to recheck on each update |
| `GF_ADMIN_PASSWORD` | `admin` | Grafana admin password |
| `MTG_META_SCRAPER_DB_READER_PASSWORD` | (required) | Password of the read-only `grafana_reader` role (scraper and Grafana) |
| `MTG_META_SCRAPER_DB_HOST`, `MTG_META_SCRAPER_DB_NAME`, `MTG_META_SCRAPER_DB_SSLMODE` | set in `docker-compose.yml` | Grafana datasource connection (host, database, SSL mode) |

The **performance weights** live in a single place, the `placement_points` function in `mtg_meta_scraper/db/sql_objects.py`. Changing them requires a migration (see [Database and migrations](#database-and-migrations)).

## Development

```bash
uv sync
docker compose up -d db
uv run pytest
```

Parser tests use real MTGTop8 pages saved in `tests/fixtures/`. Database tests use the `mtg_meta_scraper_test` database, recreated from scratch on every run by applying the migrations, and are skipped if Postgres is unreachable.

### Database and migrations

Tables are SQLAlchemy models in `mtg_meta_scraper/db/tables.py`; the view and SQL functions used by Grafana are [alembic_utils](https://github.com/olirice/alembic_utils) objects in `mtg_meta_scraper/db/sql_objects.py`. The whole schema is managed with Alembic. The scraper applies any pending migrations by itself on every start (so does `python -m mtg_meta_scraper init-db`).

To change the schema:

```bash
# 1. edit mtg_meta_scraper/db/tables.py (tables) or sql_objects.py (view, functions, weights)
# 2. generate the migration (compares the models with the DB in DATABASE_URL, default localhost)
uv run alembic revision --autogenerate -m "add column X"
# 3. review the new file in mtg_meta_scraper/db/migrations/versions/ and apply it
uv run alembic upgrade head
```

The `test_models_match_migrations` test fails if tables, view or functions don't match the migrations.

The `deck_facts` view depends on the columns of `deck`, `event` and `archetype`: a migration that renames them or changes their type must drop the view first (`op.drop_entity(...)`) and recreate it afterwards (`op.create_entity(...)`), using the definition from `sql_objects.py`.

A database created before Alembic was introduced (with the old `schema.sql`) has different index names: if it is empty, recreate it (`docker compose down -v`, **deletes the data**); otherwise align it by hand and then mark it with `uv run alembic stamp head`.

Layout:

- `mtg_meta_scraper/http.py`: slow HTTP client with retries.
- `mtg_meta_scraper/parsing/`: parsing of the event, format and home pages.
- `mtg_meta_scraper/db/`: ORM models (`tables.py`), SQL view and functions (`sql_objects.py`), Alembic migrations (`migrations/`) and repository.
- `mtg_meta_scraper/pipeline.py`: backfill and update.
- `mtg_meta_scraper/validate.py`: comparison with the site.
- `grafana/`: automatically provisioned datasource and dashboard.
