import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.exc import OperationalError

from mtg_meta_scraper.db.engine import sqlalchemy_url

FIXTURES = Path(__file__).parent / "fixtures"

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://mtg_meta_scraper:mtg_meta_scraper@localhost:5432/mtg_meta_scraper_test"
)


@pytest.fixture
def load_fixture():
    def _load(name: str) -> str:
        return (FIXTURES / name).read_bytes().decode("cp1252", errors="replace")

    return _load


@pytest.fixture(scope="session")
def _test_db_url():
    """Recreate the test database from scratch: the schema comes from the migrations, as in production."""
    url = make_url(sqlalchemy_url(TEST_DATABASE_URL))
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT",
                          connect_args={"connect_timeout": 3})
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    except OperationalError as exc:
        msg = f"test DB unreachable ({exc.orig}); start it with `docker compose up -d db`"
        if os.environ.get("REQUIRE_TEST_DB"):  # in CI a missing DB is an error, not a skipped test
            pytest.fail(msg)
        pytest.skip(msg)
    finally:
        admin.dispose()
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
def _session_repo(_test_db_url):
    from mtg_meta_scraper.db.repo import Repo

    # The migrations create the read-only grafana_reader role with it (roles are server-wide).
    os.environ.setdefault("MTG_META_SCRAPER_DB_READER_PASSWORD", "grafana_reader")
    repo = Repo.connect(_test_db_url)
    repo.init_schema()
    yield repo
    repo.close()


@pytest.fixture
def repo(_session_repo):
    _session_repo.rollback()
    with _session_repo.session.begin():
        _session_repo.session.execute(text("TRUNCATE event, archetype, deck, deck_card, scan_state, update_run CASCADE"))
    return _session_repo


def fetch(repo, sql: str, **params) -> list[tuple]:
    """Run verification SQL in its own transaction and return the rows as tuples."""
    with repo.session.begin():
        return [tuple(r) for r in repo.session.execute(text(sql), params).all()]


def scalar(repo, sql: str, **params):
    rows = fetch(repo, sql, **params)
    return rows[0][0] if rows else None
