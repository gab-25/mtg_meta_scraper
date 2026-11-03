from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from mtg_meta_scraper.db.tables import Base


def test_models_match_migrations(repo):
    """Se fallisce: hai cambiato tables.py senza migrazione. `uv run alembic revision --autogenerate -m ...`"""
    with repo.session.begin():
        ctx = MigrationContext.configure(repo.session.connection(), opts={"compare_server_default": True})
        diff = compare_metadata(ctx, Base.metadata)
    assert diff == []


def test_formats_seeded_by_migration(repo):
    from conftest import fetch

    codes = {c for (c,) in fetch(repo, "SELECT code FROM format")}
    assert codes == {"ST", "PI", "MO", "LE", "VI", "PAU", "EDH", "cEDH"}
