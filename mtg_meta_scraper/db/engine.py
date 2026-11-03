"""SQLAlchemy engine and Alembic migrations."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, make_url

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def sqlalchemy_url(url: str) -> str:
    """`postgresql://...` (DATABASE_URL format) -> psycopg 3 driver."""
    u = make_url(url)
    if u.drivername == "postgresql":
        u = u.set(drivername="postgresql+psycopg")
    return u.render_as_string(hide_password=False)


def make_engine(url: str) -> Engine:
    return create_engine(sqlalchemy_url(url), pool_pre_ping=True)


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    # ConfigParser interprets '%': it must be doubled (e.g. passwords with special characters).
    cfg.set_main_option("sqlalchemy.url", sqlalchemy_url(url).replace("%", "%%"))
    return cfg


def migrate(url: str) -> None:
    """Upgrade the schema to the latest migration."""
    command.upgrade(alembic_config(url), "head")
