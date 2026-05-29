import os

import pytest

from app.core.config import get_settings
from app.core.database import _is_railway_public_proxy, get_database_url


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


def test_normalizes_postgres_scheme(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://user:pass@localhost/db")
    monkeypatch.delenv("DATABASE_PRIVATE_URL", raising=False)
    assert get_database_url() == "postgresql://user:pass@localhost/db"


def test_detects_railway_public_proxy():
    assert _is_railway_public_proxy("postgresql://u:p@trolley.proxy.rlwy.net:19817/railway")
    assert not _is_railway_public_proxy("postgresql://u:p@postgres.railway.internal:5432/railway")
