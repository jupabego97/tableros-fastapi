import os
import socket
from urllib.parse import quote_plus, urlparse

from loguru import logger
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool, StaticPool

from .config import get_settings

_engine = None
_SessionLocal = None
_active_database_url: str | None = None


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


def _prefer_private_url() -> bool:
    return os.getenv("PREFER_DATABASE_PRIVATE_URL", "0").strip().lower() in {"1", "true", "yes"}


def _build_url_from_pg_vars() -> str:
    host = (os.getenv("PGHOST") or os.getenv("POSTGRES_HOST") or "").strip()
    if not host:
        return ""
    port = (os.getenv("PGPORT") or os.getenv("POSTGRES_PORT") or "5432").strip()
    user = (os.getenv("PGUSER") or os.getenv("POSTGRES_USER") or "postgres").strip()
    password = os.getenv("PGPASSWORD") or os.getenv("POSTGRES_PASSWORD") or ""
    database = (os.getenv("PGDATABASE") or os.getenv("POSTGRES_DB") or "railway").strip()
    creds = f"{quote_plus(user)}:{quote_plus(password)}@" if password else f"{quote_plus(user)}@"
    return _normalize_database_url(f"postgresql://{creds}{host}:{port}/{database}")


def _postgresql_connect_timeout() -> int:
    raw = os.getenv("DB_CONNECT_TIMEOUT", "15").strip()
    try:
        return max(3, int(raw))
    except ValueError:
        return 15


def _ipv4_hostaddr(hostname: str) -> str | None:
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
    if _is_railway_internal(url):
        hostaddr = _ipv4_hostaddr(_hostname(url))
        if hostaddr:
            args["hostaddr"] = hostaddr
    return args


def get_database_url() -> str:
    """URL para el pool de SQLAlchemy. Por defecto respeta DATABASE_URL tal cual en Railway."""
    settings = get_settings()
    url = _normalize_database_url(settings.database_url)

    if url != "sqlite:///./tableros.db" and not _prefer_private_url():
        return url

    private_url = _normalize_database_url(settings.database_private_url.strip())
    pg_url = _build_url_from_pg_vars()

    if _is_railway_internal(url):
        return url

    if private_url and (url == "sqlite:///./tableros.db" or _is_railway_public_proxy(url)):
        logger.info(f"Using DATABASE_PRIVATE_URL ({_hostname(private_url)})")
        return private_url

    if pg_url and (url == "sqlite:///./tableros.db" or _is_railway_public_proxy(url)):
        logger.info(f"Using PGHOST URL ({_hostname(pg_url)})")
        return pg_url

    if settings.is_production and _is_railway_public_proxy(url):
        logger.warning(
            "DATABASE_URL uses public TCP proxy. Set PREFER_DATABASE_PRIVATE_URL=1 only if "
            "private networking is confirmed working."
        )

    return url


def _unique_urls(*candidates: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        url = _normalize_database_url(raw.strip())
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def get_runtime_database_urls() -> list[str]:
    """Orden para la API: primero DATABASE_URL (suele ser el proxy público que conecta)."""
    settings = get_settings()
    raw = _normalize_database_url(settings.database_url)
    private = _normalize_database_url(settings.database_private_url.strip())
    pg_url = _build_url_from_pg_vars()
    computed = get_database_url()

    return _unique_urls(raw, computed, private, pg_url)


def get_migration_database_urls() -> list[str]:
    """Orden para migraciones: privada primero, luego pública como fallback."""
    settings = get_settings()
    urls = _unique_urls(
        get_database_url() if _prefer_private_url() else "",
        _normalize_database_url(settings.database_url),
        _normalize_database_url(settings.database_private_url.strip()),
        _build_url_from_pg_vars(),
    )
    if not urls:
        urls = get_runtime_database_urls()

    if _is_railway_public_proxy(_normalize_database_url(settings.database_url)):
        proxy = _normalize_database_url(settings.database_url)
        if proxy not in urls:
            urls.append(proxy)

    if os.getenv("MIGRATION_FALLBACK_PUBLIC", "1").strip().lower() in {"1", "true", "yes"}:
        for key in ("DATABASE_PUBLIC_URL", "DATABASE_URL_PUBLIC"):
            fallback = os.getenv(key, "").strip()
            if fallback:
                fb = _normalize_database_url(fallback)
                if fb not in urls:
                    urls.append(fb)

    return urls


def test_database_url(url: str) -> bool:
    try:
        probe = create_engine(
            url,
            poolclass=NullPool,
            pool_pre_ping=True,
            connect_args=_postgresql_connect_args(url),
        )
        with probe.connect() as conn:
            conn.execute(text("SELECT 1"))
        probe.dispose()
        return True
    except Exception as exc:
        logger.debug(f"DB probe failed for {_hostname(url)}: {exc}")
        return False


def reinit_database_engine(url: str | None = None) -> None:
    global _engine, _SessionLocal, _active_database_url

    if _engine is not None:
        _engine.dispose()

    if url:
        os.environ["DATABASE_URL"] = _normalize_database_url(url)
        get_settings.cache_clear()
        target = _normalize_database_url(url)
    else:
        target = get_database_url()

    _active_database_url = target
    logger.info(f"Database engine -> {_hostname(target)}")

    if target.startswith("sqlite"):
        _engine = create_engine(
            target,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    else:
        _engine = create_engine(
            target,
            pool_size=10,
            pool_recycle=3600,
            pool_pre_ping=True,
            max_overflow=20,
            connect_args=_postgresql_connect_args(target),
        )
    _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


def ensure_database_engine() -> bool:
    """Prueba URLs en orden y deja el pool apuntando a la que responde."""
    for url in get_runtime_database_urls():
        if test_database_url(url):
            if url != _active_database_url:
                reinit_database_engine(url)
            elif _engine is None:
                reinit_database_engine(url)
            return True

    logger.error(
        "No se pudo conectar a Postgres con ninguna URL configurada. "
        f"Probadas: {', '.join(_hostname(u) for u in get_runtime_database_urls())}"
    )
    if _engine is None:
        reinit_database_engine()
    return False


# Inicialización al importar (tests / dev)
reinit_database_engine()


class _EngineAccessor:
    def __getattr__(self, name):
        if _engine is None:
            reinit_database_engine()
        return getattr(_engine, name)

    def dispose(self):
        if _engine is not None:
            _engine.dispose()


engine = _EngineAccessor()


def _session_factory():
    if _SessionLocal is None:
        reinit_database_engine()
    return _SessionLocal


class SessionLocal:
    """Sessionmaker actualizable tras detectar la URL de BD que funciona."""

    def __call__(self):
        return _session_factory()()


SessionLocal = SessionLocal()


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
