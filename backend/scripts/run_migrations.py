"""Run Alembic migrations with retries for transient Railway/Postgres outages."""

from __future__ import annotations

import os
import random
import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from loguru import logger
from sqlalchemy.exc import DBAPIError, OperationalError


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
    return Config(str(backend_dir / "alembic.ini"))


def main() -> None:
    attempts = max(1, _int_env("MIGRATION_MAX_ATTEMPTS", 8))
    base_delay = max(0.1, _float_env("MIGRATION_RETRY_BASE_SECONDS", 2.0))
    max_delay = max(base_delay, _float_env("MIGRATION_RETRY_MAX_SECONDS", 30.0))

    config = _migration_config()
    for attempt in range(1, attempts + 1):
        try:
            logger.info(f"Running Alembic migrations (attempt {attempt}/{attempts})")
            command.upgrade(config, "head")
            logger.info("Alembic migrations completed")
            return
        except Exception as exc:
            if attempt >= attempts or not _is_retryable(exc):
                logger.exception("Alembic migrations failed")
                raise

            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay += random.uniform(0, min(1.0, delay * 0.25))
            logger.warning(
                f"Alembic migration connection failed; retrying in {delay:.1f}s "
                f"(attempt {attempt}/{attempts}): {exc}"
            )
            time.sleep(delay)


if __name__ == "__main__":
    main()
