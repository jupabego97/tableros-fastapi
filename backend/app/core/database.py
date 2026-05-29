import os
import socket
from urllib.parse import parse_qs, quote_plus, urlencode, urlparse, urlunparse

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
    host = _hostname(url)
    return host.endswith(".proxy.rlwy.net") or "rlwy.net" in host


def _is_railway_internal(url: str) -> bool:
    host = _hostname(url)
    return host.endswith(".railway.internal") or host.endswith(".internal")


def _prefer_private_url() -> bool:
    return os.getenv("PREFER_DATABASE_PRIVATE_URL", "0").strip().lower() in {"1", "true", "yes"}


def _query_has_sslmode(url: str) -> bool:
    return "sslmode" in parse_qs(urlparse(url).query, keep_blank_values=True)


def _with_sslmode(url: str, mode: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["sslmode"] = [mode]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


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


def _build_url_from_railway_tcp_proxy() -> str:
    """URL TCP pública ensamblada desde variables de Railway (más fiable que copiar a mano)."""
    domain = (os.getenv("RAILWAY_TCP_PROXY_DOMAIN") or "").strip()
    port = (os.getenv("RAILWAY_TCP_PROXY_PORT") or "").strip()
    if not domain or not port:
        return ""
    user = (os.getenv("PGUSER") or "postgres").strip()
    password = os.getenv("PGPASSWORD") or os.getenv("POSTGRES_PASSWORD") or ""
    database = (os.getenv("PGDATABASE") or "railway").strip()
    creds = f"{quote_plus(user)}:{quote_plus(password)}@" if password else f"{quote_plus(user)}@"
    return _normalize_database_url(f"postgresql://{creds}{domain}:{port}/{database}")


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


def _default_sslmode(url: str) -> str | None:
    explicit = os.getenv("DB_SSLMODE", "").strip()
    if explicit:
        return explicit
    if _query_has_sslmode(url):
        return None
    if _is_railway_internal(url):
        return "disable"
    if _is_railway_public_proxy(url):
        return "require"
    if get_settings().is_production:
        return "require"
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
    sslmode = _default_sslmode(url)
    if sslmode:
        args["sslmode"] = sslmode
    if _is_railway_internal(url):
        hostaddr = _ipv4_hostaddr(_hostname(url))
        if hostaddr:
            args["hostaddr"] = hostaddr
    return args


def _url_connection_variants(url: str) -> list[str]:
    """Genera variantes SSL para probar la URL que realmente conecta en Railway."""
    if not url or url.startswith("sqlite"):
        return [url] if url else []

    variants: list[str] = []
    seen: set[str] = set()

    def add(candidate: str) -> None:
        normalized = _normalize_database_url(candidate)
        if normalized and normalized not in seen:
            seen.add(normalized)
            variants.append(normalized)

    add(url)
    if not _query_has_sslmode(url):
        if _is_railway_internal(url):
            add(_with_sslmode(url, "disable"))
        elif _is_railway_public_proxy(url):
            for mode in ("require", "prefer", "disable"):
                add(_with_sslmode(url, mode))
        elif get_settings().is_production:
            add(_with_sslmode(url, "require"))
            add(_with_sslmode(url, "prefer"))

    return variants


def get_database_url() -> str:
    settings = get_settings()
    url = _normalize_database_url(settings.database_url)

    if url != "sqlite:///./tableros.db" and not _prefer_private_url():
        return url

    private_url = _normalize_database_url(settings.database_private_url.strip())
    pg_url = _build_url_from_pg_vars()

    if _is_railway_internal(url):
        return url

    if private_url and (url == "sqlite:///./tableros.db" or _is_railway_public_proxy(url)):
        return private_url

    if pg_url and (url == "sqlite:///./tableros.db" or _is_railway_public_proxy(url)):
        return pg_url

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
    """URLs base + variantes SSL, priorizando proxy TCP pública de Railway."""
    settings = get_settings()
    bases = _unique_urls(
        _normalize_database_url(settings.database_url),
        _build_url_from_railway_tcp_proxy(),
        get_database_url(),
        _normalize_database_url(settings.database_private_url.strip()),
        _build_url_from_pg_vars(),
    )

    expanded: list[str] = []
    seen: set[str] = set()
    for base in bases:
        for variant in _url_connection_variants(base):
            if variant not in seen:
                seen.add(variant)
                expanded.append(variant)
    return expanded


def get_migration_database_urls() -> list[str]:
    return get_runtime_database_urls()


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
        logger.info(f"DB probe failed [{_hostname(url)} ssl={_default_sslmode(url)}]: {exc}")
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
    logger.info(f"Database engine -> {_hostname(target)} (sslmode={_default_sslmode(target) or 'url'})")

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
    for url in get_runtime_database_urls():
        if test_database_url(url):
            if url != _active_database_url:
                reinit_database_engine(url)
            elif _engine is None:
                reinit_database_engine(url)
            return True

    hosts = ", ".join(_hostname(u) for u in get_runtime_database_urls()[:6])
    logger.error(
        "No Postgres connection succeeded. Check DATABASE_URL=${{Postgres.DATABASE_URL}} "
        f"and that Postgres is Running. Tried hosts: {hosts}"
    )
    if _engine is None:
        reinit_database_engine()
    return False


def get_active_database_host() -> str | None:
    if _active_database_url:
        return _hostname(_active_database_url)
    return None


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


class _SessionLocalCallable:
    def __call__(self):
        return _session_factory()()


SessionLocal = _SessionLocalCallable()


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
