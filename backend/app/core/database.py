from urllib.parse import urlparse

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


def get_database_url() -> str:
    settings = get_settings()
    url = _normalize_database_url(settings.database_url)
    private_url = _normalize_database_url(settings.database_private_url.strip())

    if private_url and (url == "sqlite:///./tableros.db" or _is_railway_public_proxy(url)):
        logger.info("Using Railway private database URL instead of public TCP proxy/default SQLite")
        return private_url

    if settings.is_production and _is_railway_public_proxy(url):
        logger.warning(
            "DATABASE_URL points to Railway's public TCP proxy; set DATABASE_PRIVATE_URL "
            "or change DATABASE_URL to the private Postgres URL for backend deployments inside Railway"
        )

    return url


def _postgresql_connect_args(url: str) -> dict:
    if url.startswith("postgresql"):
        return {
            "connect_timeout": 10,
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5,
        }
    return {}


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
