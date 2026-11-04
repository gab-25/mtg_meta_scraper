from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from mtg_meta_scraper.db import sql_objects  # noqa: F401 - also compares the view and SQL functions
from mtg_meta_scraper.db.tables import Base


def test_models_match_migrations(repo):
    """If this fails: you changed tables.py or sql_objects.py without a migration. `uv run alembic revision --autogenerate -m ...`"""
    with repo.session.begin():
        ctx = MigrationContext.configure(repo.session.connection(), opts={"compare_server_default": True, "include_schemas": False})
        diff = compare_metadata(ctx, Base.metadata)
    assert diff == []


def test_formats_seeded_by_migration(repo):
    from conftest import fetch

    codes = {c for (c,) in fetch(repo, "SELECT code FROM format")}
    assert codes == {"ST", "PI", "MO", "LE", "VI", "PAU", "EDH", "cEDH"}


def _reader_migration():
    import importlib.util
    from pathlib import Path

    from mtg_meta_scraper.db.engine import MIGRATIONS_DIR

    [path] = Path(MIGRATIONS_DIR / "versions").glob("*_grafana_reader.py")
    spec = importlib.util.spec_from_file_location("reader_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _can(repo, relation: str, privilege: str) -> bool:
    from conftest import scalar

    return scalar(repo, f"SELECT has_table_privilege('grafana_reader', 'public.{relation}', '{privilege}')")


def test_reader_can_only_read(repo):
    from conftest import fetch

    tables = [t for (t,) in fetch(repo, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")]
    assert tables
    for relation in [*tables, "deck_facts"]:
        assert _can(repo, relation, "SELECT"), relation
        for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            assert not _can(repo, relation, privilege), (relation, privilege)
    from conftest import scalar

    assert scalar(repo, "SELECT rolsuper OR rolcreaterole OR rolcreatedb FROM pg_roles WHERE rolname = 'grafana_reader'") is False


def test_reader_can_read_tables_added_later(repo):
    from sqlalchemy import text

    with repo.session.begin():
        repo.session.execute(text("CREATE TABLE added_later (id int)"))
        assert repo.session.execute(
            text("SELECT has_table_privilege('grafana_reader', 'public.added_later', 'SELECT')")).scalar()
        repo.session.rollback()


def test_reader_role_is_created_with_the_password(repo):
    import uuid

    from sqlalchemy import text

    name = f"reader_{uuid.uuid4().hex[:8]}"
    with repo.session.begin():
        _reader_migration().ensure_role(repo.session.connection(), name, "pa'ss%")
        row = repo.session.execute(
            text("SELECT rolcanlogin, rolsuper FROM pg_roles WHERE rolname = :n"), {"n": name}).one()
        assert tuple(row) == (True, False)
        repo.session.rollback()


def test_reader_role_is_not_created_without_a_password(repo):
    import uuid

    from conftest import scalar

    name = f"reader_{uuid.uuid4().hex[:8]}"
    with repo.session.begin():
        _reader_migration().ensure_role(repo.session.connection(), name, None)
    assert scalar(repo, "SELECT count(*) FROM pg_roles WHERE rolname = :n", n=name) == 0
