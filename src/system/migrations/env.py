from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from system_api.database import database_url
from system_api.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", database_url())
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with engine.connect() as connection:

        def migrate(sync_connection) -> None:
            context.configure(
                connection=sync_connection,
                target_metadata=target_metadata,
                version_table_schema="system",
                include_schemas=True,
            )
            with context.begin_transaction():
                context.run_migrations()

        await connection.run_sync(migrate)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    import asyncio

    asyncio.run(run_async_migrations())
