"""Alembic environment configuration for AuraBook Cloud Brain.

Reads the database URL from AURABOOK_CLOUD_DATABASE_URL env var,
falling back to alembic.ini's sqlalchemy.url.
"""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine

config = context.config

database_url = os.environ.get("AURABOOK_CLOUD_DATABASE_URL", "")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — emit SQL to stdout."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=None, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database."""
    connectable = create_engine(config.get_main_option("sqlalchemy.url"))
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
