from logging.config import fileConfig

from alembic import context
from app.core.database import get_database_url
from app.models import Base
from sqlalchemy import pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url():
    return get_database_url()


def run_migrations_offline() -> None:
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def _connect_args(url: str) -> dict:
    if url.startswith("postgresql"):
        return {
            "connect_timeout": 10,
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5,
        }
    return {}


def run_migrations_online() -> None:
    from sqlalchemy import create_engine

    url = get_url()
    connectable = create_engine(
        url,
        poolclass=pool.NullPool,
        pool_pre_ping=True,
        connect_args=_connect_args(url),
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
