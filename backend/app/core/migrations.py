"""Alembic migrations with retries for Railway/Postgres."""

from __future__ import annotations

import os
import random
import time
from pathlib import Path
from urllib.parse import urlparse

from alembic import command
from alembic.config import Config
from loguru import logger
from sqlalchemy.exc import DBAPIError, OperationalError

from app.core.config import get_settings
from app.core.database import get_migration_database_urls


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(f"Invalid {name}={raw!r}; using default {default}")
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning(f"Invalid {name}={raw!r}; using default {default}")
        return default


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, OperationalError):
        return True
    if isinstance(exc, DBAPIError) and exc.connection_invalidated:
        return True
    return False


def _migration_config() -> Config:
    backend_dir = Path(__file__).resolve().parents[2]
    return Config(str(backend_dir / "alembic.ini"))


def _host_label(url: str) -> str:
    try:
        return urlparse(url).hostname or "unknown"
    except ValueError:
        return "unknown"


def _use_database_url(url: str) -> None:
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()


def run_migrations() -> bool:
    """
    Run Alembic upgrade head. Returns True on success, False if all URLs fail.
    Does not raise — safe to call from FastAPI lifespan.
    """
    if os.getenv("SKIP_DB_MIGRATIONS", "").strip().lower() in {"1", "true", "yes"}:
        logger.warning("SKIP_DB_MIGRATIONS=1 — omitiendo migraciones Alembic")
        return True

    attempts_per_url = max(1, _int_env("MIGRATION_MAX_ATTEMPTS", 5))
    base_delay = max(0.1, _float_env("MIGRATION_RETRY_BASE_SECONDS", 2.0))
    max_delay = max(base_delay, _float_env("MIGRATION_RETRY_MAX_SECONDS", 20.0))
    initial_delay = max(0.0, _float_env("MIGRATION_INITIAL_DELAY_SECONDS", 3.0))

    if initial_delay > 0:
        logger.info(f"Waiting {initial_delay:.0f}s before migrations (Postgres warm-up)")
        time.sleep(initial_delay)

    urls = get_migration_database_urls()
    if not urls:
        logger.error("No database URL configured for migrations")
        return False

    logger.info(
        "Migration targets (in order): " + ", ".join(_host_label(url) for url in urls)
    )

    config = _migration_config()
    last_exc: Exception | None = None

    for url_index, db_url in enumerate(urls, start=1):
        _use_database_url(db_url)
        host = _host_label(db_url)
        for attempt in range(1, attempts_per_url + 1):
            try:
                logger.info(
                    f"Running Alembic on {host} "
                    f"(url {url_index}/{len(urls)}, attempt {attempt}/{attempts_per_url})"
                )
                command.upgrade(config, "head")
                logger.info(f"Alembic migrations completed via {host}")
                return True
            except Exception as exc:
                last_exc = exc
                if not _is_retryable(exc):
                    logger.exception(f"Alembic failed on {host} (non-retryable): {exc}")
                    break

                if attempt >= attempts_per_url:
                    logger.warning(f"All attempts failed for {host}: {exc}")
                    break

                delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                delay += random.uniform(0, min(1.0, delay * 0.25))
                logger.warning(
                    f"Migration failed on {host}; retry in {delay:.1f}s "
                    f"({attempt}/{attempts_per_url}): {exc}"
                )
                time.sleep(delay)

    logger.error(f"Alembic failed on all URLs. Last error: {last_exc}")
    return False
