"""Let grafana_reader read the tables

Grafana queries the database as grafana_reader, a role that can only read. This migration creates it
when it is missing and MTG_META_SCRAPER_DB_READER_PASSWORD is set (creating a role needs CREATEROLE),
and lets it read every table and view of the schema, including those later migrations will add.

Roles are server-wide: the downgrade takes the grants back but never drops the role, and changing the
password of an existing role is left to an administrator.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-04 10:00:00.000000

"""
import logging
import os
from typing import Sequence, Union

from alembic import op
from psycopg import sql

# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ROLE = 'grafana_reader'
PASSWORD_ENV = 'MTG_META_SCRAPER_DB_READER_PASSWORD'
logger = logging.getLogger('alembic.runtime.migration')


def ensure_role(connection, name: str, password: str | None) -> None:
    """Create a login role that can do nothing else, unless it exists or there is no password."""
    if connection.exec_driver_sql('SELECT 1 FROM pg_roles WHERE rolname = %s', (name,)).first():
        return
    if not password:
        logger.warning('Role %s does not exist and %s is not set: create it by hand, then restart the scraper',
                       name, PASSWORD_ENV)
        return
    driver = connection.connection.driver_connection
    statement = sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(name), sql.Literal(password))
    connection.exec_driver_sql(statement.as_string(driver).replace('%', '%%'))


def grants(action: str) -> str:
    """GRANT or REVOKE everything the reader needs, on the current database and schema."""
    to_from = 'TO' if action == 'GRANT' else 'FROM'
    return f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}') THEN
                EXECUTE format('{action} CONNECT ON DATABASE %I {to_from} {ROLE}', current_database());
                EXECUTE format('{action} USAGE ON SCHEMA %I {to_from} {ROLE}', current_schema());
                EXECUTE format('{action} SELECT ON ALL TABLES IN SCHEMA %I {to_from} {ROLE}', current_schema());
                EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I {action} SELECT ON TABLES {to_from} {ROLE}',
                               current_schema());
            END IF;
        END
        $$
    """


def upgrade() -> None:
    ensure_role(op.get_bind(), ROLE, os.environ.get(PASSWORD_ENV))
    op.execute(grants('GRANT'))


def downgrade() -> None:
    op.execute(grants('REVOKE'))
