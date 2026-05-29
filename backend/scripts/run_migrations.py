"""CLI entrypoint for Alembic migrations."""

from __future__ import annotations

import sys

from app.core.migrations import run_migrations


def main() -> None:
    if not run_migrations():
        sys.exit(1)


if __name__ == "__main__":
    main()
