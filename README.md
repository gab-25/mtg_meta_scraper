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
cp .env.example .env        # optional: change the Grafana password, formats, request rate
docker compose up -d --build
```

- Grafana: <http://localhost:3000> (viewable without login; to edit, use admin / `GF_ADMIN_PASSWORD`).
- On first start the scraper backfills the **last 30 days**. It makes roughly one request every 1.5 s and needs one per deck, so the backfill takes **a few hours** (about 3-4). It goes from the most recent events to the oldest, so the dashboard fills up with recent data right away. If interrupted, it resumes where it left off. For a longer history raise `MTG_META_SCRAPER_BACKFILL_DAYS` (e.g. `183` for 6 months: about a day). The daily update never deletes old data, so the history grows on its own.
- After the initial backfill, the update runs every day at `MTG_META_SCRAPER_UPDATE_HOUR_UTC` (UTC). It collects new tournaments and rechecks those from the last `MTG_META_SCRAPER_RECHECK_DAYS` days.

Logs: `docker compose logs -f scraper`.

## Commands

```bash
docker compose exec scraper python -m mtg_meta_scraper validate   # compare our shares with the site's
```

The `scraper` container already runs the backfill and the daily update on its own: do **not** run `python -m mtg_meta_scraper backfill` or `python -m mtg_meta_scraper update` while it is running, or requests to the site will double. To run them by hand: `docker compose stop scraper`, then `docker compose run --rm scraper python -m mtg_meta_scraper update` (or `backfill`), then `docker compose start scraper`.

If the scraper hits 100 missing or failing pages in a row (site down, blocked, HTML changed) it stops by itself to avoid hammering the site, and retries on the next run.

Postgres (`5432`) and Grafana (`3000`) are exposed on `127.0.0.1` only.

For each format, `validate` compares the top 10 archetypes of the site's "Metagame Breakdown – Last 2 Months" with our percentages over the last 60 days. It flags a `MISMATCH` beyond ±2 points and exits with code 1. Until the backfill is complete, or if it covers fewer than 60 days (as with the default of 30), mismatches are expected.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `MTG_META_SCRAPER_FORMATS` | `ST,PI,MO,LE,VI,PAU,EDH,cEDH` | Formats to collect |
| `MTG_META_SCRAPER_BACKFILL_DAYS` | `30` | Backfill depth |
| `MTG_META_SCRAPER_REQUEST_INTERVAL` | `1.5` | Seconds between two requests (don't lower it too much) |
| `MTG_META_SCRAPER_RECHECK_DAYS` | `3` | Days of tournaments to recheck on each update |
| `MTG_META_SCRAPER_UPDATE_HOUR_UTC` | `6` | Hour of the daily update |
| `GF_ADMIN_PASSWORD` | `admin` | Grafana admin password |

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
