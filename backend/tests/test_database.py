import os

import pytest

from app.core.config import get_settings
from app.core.database import (
    _is_railway_internal,
    _is_railway_public_proxy,
    get_database_url,
    get_migration_database_urls,
)


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_prefers_private_url_over_public_proxy(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://user:pass@trolley.proxy.rlwy.net:19817/railway",
    )
    monkeypatch.setenv(
        "DATABASE_PRIVATE_URL",
        "postgresql://user:pass@postgres.railway.internal:5432/railway",
    )
    assert get_database_url() == "postgresql://user:pass@postgres.railway.internal:5432/railway"


def test_keeps_internal_database_url(monkeypatch):
    internal = "postgresql://user:pass@my-db.railway.internal:5432/railway"
    monkeypatch.setenv("DATABASE_URL", internal)
    monkeypatch.setenv(
        "DATABASE_PRIVATE_URL",
        "postgresql://user:pass@postgres.railway.internal:5432/railway",
    )
    assert get_database_url() == internal


def test_builds_from_pghost_when_linked(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@trolley.proxy.rlwy.net:19817/railway")
    monkeypatch.delenv("DATABASE_PRIVATE_URL", raising=False)
    monkeypatch.setenv("PGHOST", "postgres.railway.internal")
    monkeypatch.setenv("PGPORT", "5432")
    monkeypatch.setenv("PGUSER", "postgres")
    monkeypatch.setenv("PGPASSWORD", "secret")
    monkeypatch.setenv("PGDATABASE", "railway")
    url = get_database_url()
    assert "postgres.railway.internal" in url
    assert "secret" in url


def test_migration_urls_include_public_fallback(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://user:pass@trolley.proxy.rlwy.net:19817/railway",
    )
    monkeypatch.setenv(
        "DATABASE_PRIVATE_URL",
        "postgresql://user:pass@postgres.railway.internal:5432/railway",
    )
    monkeypatch.setenv("MIGRATION_FALLBACK_PUBLIC", "1")
    urls = get_migration_database_urls()
    hosts = [u.split("@")[1].split("/")[0] for u in urls]
    assert any("railway.internal" in h for h in hosts)
    assert any("proxy.rlwy.net" in h for h in hosts)


def test_normalizes_postgres_scheme(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://user:pass@localhost/db")
    monkeypatch.delenv("DATABASE_PRIVATE_URL", raising=False)
    assert get_database_url() == "postgresql://user:pass@localhost/db"


def test_detects_railway_hosts():
    assert _is_railway_public_proxy("postgresql://u:p@trolley.proxy.rlwy.net:19817/railway")
    assert _is_railway_internal("postgresql://u:p@postgres.railway.internal:5432/railway")
    assert not _is_railway_internal("postgresql://u:p@localhost:5432/db")
