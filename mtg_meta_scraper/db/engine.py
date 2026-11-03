"""Engine SQLAlchemy e migrazioni Alembic."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, make_url

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def sqlalchemy_url(url: str) -> str:
    """`postgresql://...` (formato di DATABASE_URL) -> driver psycopg 3."""
    u = make_url(url)
    if u.drivername == "postgresql":
        u = u.set(drivername="postgresql+psycopg")
    return u.render_as_string(hide_password=False)


def make_engine(url: str) -> Engine:
    return create_engine(sqlalchemy_url(url), pool_pre_ping=True)


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    # ConfigParser interpreta i '%': vanno raddoppiati (es. password con caratteri speciali).
    cfg.set_main_option("sqlalchemy.url", sqlalchemy_url(url).replace("%", "%%"))
    return cfg


def migrate(url: str) -> None:
    """Porta lo schema all'ultima migrazione."""
    command.upgrade(alembic_config(url), "head")
