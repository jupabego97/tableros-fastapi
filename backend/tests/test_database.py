import os

import pytest

from app.core.config import get_settings
from app.core.database import (
    _is_railway_internal,
    _is_railway_public_proxy,
    ensure_database_engine,
    get_database_url,
    get_runtime_database_urls,
    reinit_database_engine,
)


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_uses_database_url_by_default_without_private_preference(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://user:pass@trolley.proxy.rlwy.net:19817/railway",
    )
    monkeypatch.setenv(
        "DATABASE_PRIVATE_URL",
        "postgresql://user:pass@postgres.railway.internal:5432/railway",
    )
    monkeypatch.delenv("PREFER_DATABASE_PRIVATE_URL", raising=False)
    assert get_database_url() == "postgresql://user:pass@trolley.proxy.rlwy.net:19817/railway"


def test_prefers_private_url_when_flag_set(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://user:pass@trolley.proxy.rlwy.net:19817/railway",
    )
    monkeypatch.setenv(
        "DATABASE_PRIVATE_URL",
        "postgresql://user:pass@postgres.railway.internal:5432/railway",
    )
    monkeypatch.setenv("PREFER_DATABASE_PRIVATE_URL", "1")
    assert get_database_url() == "postgresql://user:pass@postgres.railway.internal:5432/railway"


def test_runtime_urls_put_public_first(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://user:pass@trolley.proxy.rlwy.net:19817/railway",
    )
    monkeypatch.setenv(
        "DATABASE_PRIVATE_URL",
        "postgresql://user:pass@postgres.railway.internal:5432/railway",
    )
    urls = get_runtime_database_urls()
    assert "proxy.rlwy.net" in urls[0]


def test_normalizes_postgres_scheme(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://user:pass@localhost/db")
    monkeypatch.delenv("DATABASE_PRIVATE_URL", raising=False)
    assert get_database_url() == "postgresql://user:pass@localhost/db"


def test_detects_railway_hosts():
    assert _is_railway_public_proxy("postgresql://u:p@trolley.proxy.rlwy.net:19817/railway")
    assert _is_railway_internal("postgresql://u:p@postgres.railway.internal:5432/railway")
    assert not _is_railway_internal("postgresql://u:p@localhost:5432/db")


def test_ensure_database_engine_uses_sqlite(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    get_settings.cache_clear()
    reinit_database_engine()
    assert ensure_database_engine() is True
