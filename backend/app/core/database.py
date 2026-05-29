import os
import socket
from urllib.parse import quote_plus, urlparse

from loguru import logger
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import get_settings


def _normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def _hostname(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except ValueError:
        return ""


def _is_railway_public_proxy(url: str) -> bool:
    return _hostname(url).endswith(".proxy.rlwy.net")


def _is_railway_internal(url: str) -> bool:
    host = _hostname(url)
    return host.endswith(".railway.internal") or host.endswith(".internal")


def _build_url_from_pg_vars() -> str:
    """Build URL from Railway-injected PG* vars when Postgres is linked to the service."""
    host = (os.getenv("PGHOST") or os.getenv("POSTGRES_HOST") or "").strip()
    if not host:
        return ""
    port = (os.getenv("PGPORT") or os.getenv("POSTGRES_PORT") or "5432").strip()
    user = (os.getenv("PGUSER") or os.getenv("POSTGRES_USER") or "postgres").strip()
    password = os.getenv("PGPASSWORD") or os.getenv("POSTGRES_PASSWORD") or ""
    database = (os.getenv("PGDATABASE") or os.getenv("POSTGRES_DB") or "railway").strip()
    if password:
        creds = f"{quote_plus(user)}:{quote_plus(password)}@"
    else:
        creds = f"{quote_plus(user)}@"
    return _normalize_database_url(f"postgresql://{creds}{host}:{port}/{database}")


def _postgresql_connect_timeout() -> int:
    raw = os.getenv("DB_CONNECT_TIMEOUT", "30").strip()
    try:
        return max(5, int(raw))
    except ValueError:
        return 30


def _ipv4_hostaddr(hostname: str) -> str | None:
    """Prefer IPv4 for Railway private DNS (IPv6 route often times out first)."""
    if not hostname:
        return None
    try:
        infos = socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_STREAM)
        if infos:
            return infos[0][4][0]
    except OSError:
        return None
    return None


def _postgresql_connect_args(url: str) -> dict:
    if not url.startswith("postgresql"):
        return {}
    args: dict = {
        "connect_timeout": _postgresql_connect_timeout(),
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
    }
    host = _hostname(url)
    if _is_railway_internal(url):
        hostaddr = _ipv4_hostaddr(host)
        if hostaddr:
            args["hostaddr"] = hostaddr
    return args


def get_database_url() -> str:
    settings = get_settings()
    url = _normalize_database_url(settings.database_url)
    private_url = _normalize_database_url(settings.database_private_url.strip())
    pg_url = _build_url_from_pg_vars()

    if _is_railway_internal(url):
        logger.info(f"Using DATABASE_URL via Railway internal host {_hostname(url)}")
        return url

    if pg_url and _is_railway_internal(pg_url):
        if url == "sqlite:///./tableros.db" or _is_railway_public_proxy(url) or not private_url:
            logger.info(f"Using Postgres PGHOST internal URL ({_hostname(pg_url)})")
            return pg_url

    if private_url and (url == "sqlite:///./tableros.db" or _is_railway_public_proxy(url)):
        logger.info(f"Using DATABASE_PRIVATE_URL ({_hostname(private_url)})")
        return private_url

    if pg_url and (url == "sqlite:///./tableros.db" or _is_railway_public_proxy(url)):
        logger.info(f"Using PG* variables URL ({_hostname(pg_url)})")
        return pg_url

    if settings.is_production and _is_railway_public_proxy(url):
        logger.warning(
            "DATABASE_URL uses Railway public TCP proxy. Link Postgres to this service and use "
            "DATABASE_URL=${{Postgres.DATABASE_URL}} or PGHOST-based private networking."
        )

    return url


def get_migration_database_urls() -> list[str]:
    """
    URLs to try for Alembic, in order.
    Includes public proxy fallback when private networking times out during deploy.
    """
    settings = get_settings()
    primary = get_database_url()
    urls: list[str] = []
    seen: set[str] = set()

    def add(url: str) -> None:
        normalized = _normalize_database_url(url.strip())
        if normalized and normalized not in seen:
            seen.add(normalized)
            urls.append(normalized)

    add(primary)

    raw_public = _normalize_database_url(settings.database_url)
    if _is_railway_public_proxy(raw_public):
        add(raw_public)

    if os.getenv("MIGRATION_FALLBACK_PUBLIC", "1").strip().lower() in {"1", "true", "yes"}:
        for key in ("DATABASE_PUBLIC_URL", "DATABASE_URL_PUBLIC"):
            fallback = os.getenv(key, "").strip()
            if fallback:
                add(fallback)

    return urls


def create_db_engine():
    url = get_database_url()
    if url.startswith("sqlite"):
        return create_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return create_engine(
        url,
        pool_size=10,
        pool_recycle=3600,
        pool_pre_ping=True,
        max_overflow=20,
        connect_args=_postgresql_connect_args(url),
    )


engine = create_db_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
