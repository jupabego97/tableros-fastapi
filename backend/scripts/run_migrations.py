"""Run Alembic migrations with retries for transient Railway/Postgres outages."""

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
    backend_dir = Path(__file__).resolve().parents[1]
    return Config(str(backend_dir / "alembic.ini")))


def _host_label(url: str) -> str:
    try:
        parsed = urlparse(url)
        return parsed.hostname or "unknown"
    except ValueError:
        return "unknown"


def _use_database_url(url: str) -> None:
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()


def main() -> None:
    attempts_per_url = max(1, _int_env("MIGRATION_MAX_ATTEMPTS", 12))
    base_delay = max(0.1, _float_env("MIGRATION_RETRY_BASE_SECONDS", 3.0))
    max_delay = max(base_delay, _float_env("MIGRATION_RETRY_MAX_SECONDS", 45.0))
    initial_delay = max(0.0, _float_env("MIGRATION_INITIAL_DELAY_SECONDS", 15.0))

    if initial_delay > 0:
        logger.info(f"Waiting {initial_delay:.0f}s before migrations (Postgres warm-up)")
        time.sleep(initial_delay)

    urls = get_migration_database_urls()
    if not urls:
        raise RuntimeError("No database URL configured for migrations")

    logger.info(
        "Migration targets (in order): "
        + ", ".join(_host_label(url) for url in urls)
    )

    config = _migration_config()
    last_exc: Exception | None = None

    for url_index, db_url in enumerate(urls, start=1):
        _use_database_url(db_url)
        host = _host_label(db_url)
        for attempt in range(1, attempts_per_url + 1):
            try:
                logger.info(
                    f"Running Alembic migrations on {host} "
                    f"(url {url_index}/{len(urls)}, attempt {attempt}/{attempts_per_url})"
                )
                command.upgrade(config, "head")
                logger.info(f"Alembic migrations completed via {host}")
                return
            except Exception as exc:
                last_exc = exc
                if not _is_retryable(exc):
                    logger.exception(f"Alembic migrations failed on {host} (non-retryable)")
                    raise

                if attempt >= attempts_per_url:
                    logger.warning(
                        f"All {attempts_per_url} attempts failed for {host}: {exc}"
                    )
                    break

                delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                delay += random.uniform(0, min(1.0, delay * 0.25))
                logger.warning(
                    f"Migration connection failed on {host}; retrying in {delay:.1f}s "
                    f"(attempt {attempt}/{attempts_per_url}): {exc}"
                )
                time.sleep(delay)

    logger.exception("Alembic migrations failed on all configured database URLs")
    if last_exc:
        raise last_exc
    raise RuntimeError("Alembic migrations failed without a captured exception")


if __name__ == "__main__":
    main()
