"""Alembic migration environment for ThyTrader operational schema."""

from __future__ import annotations

from logging.config import fileConfig
import os

from sqlalchemy import engine_from_config, pool

from alembic import context
from thytrader.persistence.schema import metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata

_database_url_env = os.environ.get("THYTRADER_DATABASE_URL", "")
if _database_url_env.strip():
    config.set_main_option("sqlalchemy.url", _database_url_env)


def run_migrations_offline() -> None:
    """Render migrations as SQL without a live database connection."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
