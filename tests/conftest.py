from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture():
    def _load(name: str) -> str:
        return (FIXTURES / name).read_bytes().decode("cp1252", errors="replace")

    return _load


import os  # noqa: E402

import psycopg  # noqa: E402

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://mtgmeta:mtgmeta@localhost:5432/mtgmeta_test"
)


@pytest.fixture(scope="session")
def _test_db_url():
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    info = conninfo_to_dict(TEST_DATABASE_URL)
    dbname = info["dbname"]
    admin_url = make_conninfo(TEST_DATABASE_URL, dbname="postgres")
    try:
        with psycopg.connect(admin_url, autocommit=True, connect_timeout=3) as conn:
            exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)).fetchone()
            if not exists:
                conn.execute(f'CREATE DATABASE "{dbname}"')
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB di test non raggiungibile ({exc}); avvia `docker compose up -d db`")
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
def _session_repo(_test_db_url):
    from mtgmeta.db.repo import Repo

    repo = Repo.connect(_test_db_url)
    repo.init_schema()
    yield repo
    repo.conn.close()


@pytest.fixture
def repo(_session_repo):
    _session_repo.conn.execute("TRUNCATE event, archetype, deck, deck_card, scan_state CASCADE")
    _session_repo.conn.commit()
    return _session_repo
