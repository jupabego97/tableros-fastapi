"""Rutas CRUD de tarjetas de garantía por tablero (board).

Cada endpoint está bajo /api/boards/{board_id}/tarjetas para aislamiento total.
"""
import base64
import time
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, File, HTTPException, Path, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import and_, delete, exists, func, insert, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, defer

from app.core.cache import invalidate_stats
from app.core.config import get_settings
from app.core.database import get_db
from app.core.limiter import limiter
from app.models.board import Board
from app.models.kanban import Comment, KanbanColumn, SubTask, Tag, warranty_card_tags
from app.models.user import User
from app.models.warranty_card import StatusHistory, WarrantyCard, WarrantyCardMedia
from app.schemas.tarjeta import (
    BatchOperationRequest,
    BatchPosicionUpdate,
    BlockRequest,
    MediaReorderRequest,
    TarjetaCreate,
    TarjetaUpdate,
)
from app.services.auth_service import get_current_user, get_current_user_optional, require_role
from app.services.notification_service import notificar_cambio_estado
from app.services.storage_service import get_storage_service
from app.socket_events import sio

router = APIRouter(prefix="/api/boards/{board_id}/tarjetas", tags=["tarjetas"])

CACHE_HEADERS = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}
MAX_MEDIA_PER_CARD = 10
MAX_MEDIA_SIZE_BYTES = 8 * 1024 * 1024
ALLOWED_MEDIA_MIME = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}

# Map status to the date field that tracks when the card entered that status
_STATUS_DATE_FIELDS = {
    "recibido":   "recibido_date",
    "en_gestion": "en_gestion_date",
    "resuelto":   "resuelto_date",
    "entregado":  "entregado_date",
}


def _get_board_or_404(board_id: int, db: Session) -> Board:
    board = db.query(Board).filter(Board.id == board_id).first()
    if not board:
        raise HTTPException(status_code=404, detail={"code": "board_not_found", "message": "Tablero no encontrado"})
    return board


def _calcular_dias_en_columna(card: WarrantyCard) -> int:
    """Calculate days a card has been in its current column."""
    now = datetime.now(UTC)
    field = _STATUS_DATE_FIELDS.get(card.status)
    if field:
        dt = getattr(card, field, None)
        if dt:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return (now - dt).days
    return 0


def _apply_status_transition(card: WarrantyCard, new_status: str) -> None:
    """Apply date fields for a status transition."""
    if new_status == "en_gestion" and not card.en_gestion_date:
        card.en_gestion_date = datetime.now(UTC)
    elif new_status == "resuelto" and not card.resuelto_date:
        card.resuelto_date = datetime.now(UTC)
    elif new_status == "entregado" and not card.entregado_date:
        card.entregado_date = datetime.now(UTC)


def _get_valid_statuses(db: Session, board_id: int) -> list[str]:
    """Obtiene estados válidos de las columnas configuradas para este tablero."""
    cols = db.query(KanbanColumn.key).filter(
        KanbanColumn.board_id == board_id
    ).order_by(KanbanColumn.position).all()
    if cols:
        return [c[0] for c in cols]
    return ["recibido", "en_gestion", "resuelto", "entregado"]


def _check_wip_limit(db: Session, board_id: int, column_key: str, exclude_card_id: int | None = None) -> None:
    """Check WIP limit for a column. Raises HTTPException if exceeded."""
    col = db.query(KanbanColumn).filter(
        KanbanColumn.key == column_key,
        KanbanColumn.board_id == board_id,
    ).first()
    if col and col.wip_limit:
        q = db.query(WarrantyCard).filter(
            WarrantyCard.board_id == board_id,
            WarrantyCard.status == column_key,
            WarrantyCard.deleted_at.is_(None),
        )
        if exclude_card_id:
            q = q.filter(WarrantyCard.id != exclude_card_id)
        if q.count() >= col.wip_limit:
            raise HTTPException(
                status_code=400,
                detail=f"Límite WIP alcanzado en '{col.title}' ({col.wip_limit} máximo)"
            )


def _media_rows_for_card(db: Session, tarjeta_id: int) -> list[WarrantyCardMedia]:
    return db.query(WarrantyCardMedia).filter(
        WarrantyCardMedia.tarjeta_id == tarjeta_id,
        WarrantyCardMedia.deleted_at.is_(None),
    ).order_by(WarrantyCardMedia.position.asc(), WarrantyCardMedia.id.asc()).all()


def _resolve_media_url(raw_url: str | None, storage_key: str | None) -> str | None:
    if not raw_url and not storage_key:
        return None
    settings = get_settings()
    public_base = (settings.s3_public_base_url or "").rstrip("/")
    if public_base and storage_key:
        return f"{public_base}/{storage_key}"
    return raw_url


def _media_cover_map(db: Session, card_ids: list[int]) -> tuple[dict[int, str | None], dict[int, int]]:
    if not card_ids:
        return {}, {}
    rows = db.query(WarrantyCardMedia).filter(
        WarrantyCardMedia.tarjeta_id.in_(card_ids),
        WarrantyCardMedia.deleted_at.is_(None),
    ).order_by(
        WarrantyCardMedia.tarjeta_id.asc(),
        WarrantyCardMedia.is_cover.desc(),
        WarrantyCardMedia.position.asc(),
        WarrantyCardMedia.id.asc(),
    ).all()
    cover_map: dict[int, str | None] = {}
    count_map: dict[int, int] = {}
    for row in rows:
        count_map[row.tarjeta_id] = count_map.get(row.tarjeta_id, 0) + 1
        if row.tarjeta_id not in cover_map:
            cover_map[row.tarjeta_id] = _resolve_media_url(row.thumb_url or row.url, row.storage_key)
    return cover_map, count_map


def _enrich_tarjeta(t: WarrantyCard, db: Session, include_image: bool = True) -> dict:
    """Enriquece una sola tarjeta (para endpoints de detalle)."""
    d = t.to_dict(include_image=include_image)
    tag_ids = db.execute(
        select(warranty_card_tags.c.tag_id).where(warranty_card_tags.c.warranty_card_id == t.id)
    ).scalars().all()
    d["tags"] = [tg.to_dict() for tg in db.query(Tag).filter(Tag.id.in_(tag_ids)).all()] if tag_ids else []
    subtasks = db.query(SubTask).filter(SubTask.tarjeta_id == t.id).all()
    d["subtasks_total"] = len(subtasks)
    d["subtasks_done"] = sum(1 for s in subtasks if s.completed)
    d["comments_count"] = db.query(Comment).filter(Comment.tarjeta_id == t.id).count()
    media_rows = _media_rows_for_card(db, t.id)
    d["media_count"] = len(media_rows)
    d["cover_thumb_url"] = (
        _resolve_media_url(media_rows[0].thumb_url or media_rows[0].url, media_rows[0].storage_key)
        if media_rows else (t.image_url if include_image else None)
    )
    d["has_media"] = len(media_rows) > 0
    d["media_preview"] = [
        {
            "id": m.id,
            "url": _resolve_media_url(m.url, m.storage_key),
            "thumb_url": _resolve_media_url(m.thumb_url or m.url, m.storage_key),
            "position": m.position,
            "is_cover": m.is_cover,
        }
        for m in media_rows[:3]
    ]
    d["dias_en_columna"] = _calcular_dias_en_columna(t)
    return d


def _enrich_batch(items: list[WarrantyCard], db: Session, include_image: bool = True) -> list[dict]:
    """Enriquece múltiples tarjetas con queries batch O(1)."""
    if not items:
        return []

    card_ids = [t.id for t in items]
    cover_map, media_count_map = _media_cover_map(db, card_ids)
    legacy_http_cover_map: dict[int, str] = {}
    missing_cover_ids = [cid for cid in card_ids if cid not in cover_map]
    if missing_cover_ids:
        legacy_rows = db.query(WarrantyCard.id, WarrantyCard.image_url).filter(
            WarrantyCard.id.in_(missing_cover_ids),
            WarrantyCard.image_url.isnot(None),
            or_(
                WarrantyCard.image_url.like("http://%"),
                WarrantyCard.image_url.like("https://%"),
            ),
        ).all()
        for rid, image_url in legacy_rows:
            if image_url:
                legacy_http_cover_map[rid] = image_url

    # Bulk tags
    tag_links = db.execute(
        select(warranty_card_tags.c.warranty_card_id, warranty_card_tags.c.tag_id)
        .where(warranty_card_tags.c.warranty_card_id.in_(card_ids))
    ).all()
    tag_ids_needed = list({link.tag_id for link in tag_links})
    tags_by_id: dict[int, dict] = {}
    if tag_ids_needed:
        for tg in db.query(Tag).filter(Tag.id.in_(tag_ids_needed)).all():
            tags_by_id[tg.id] = tg.to_dict()
    card_tags: dict[int, list[dict]] = {cid: [] for cid in card_ids}
    for link in tag_links:
        if link.tag_id in tags_by_id:
            card_tags[link.warranty_card_id].append(tags_by_id[link.tag_id])

    # Bulk subtask counts
    subtask_total: dict[int, int] = {}
    for row in db.query(SubTask.tarjeta_id, func.count(SubTask.id)).filter(
        SubTask.tarjeta_id.in_(card_ids)
    ).group_by(SubTask.tarjeta_id).all():
        subtask_total[row[0]] = row[1]

    subtask_done: dict[int, int] = {}
    for row in db.query(SubTask.tarjeta_id, func.count(SubTask.id)).filter(
        SubTask.tarjeta_id.in_(card_ids), SubTask.completed == True  # noqa: E712
    ).group_by(SubTask.tarjeta_id).all():
        subtask_done[row[0]] = row[1]

    # Bulk comment counts
    comment_counts: dict[int, int] = {}
    for row in db.query(Comment.tarjeta_id, func.count(Comment.id)).filter(
        Comment.tarjeta_id.in_(card_ids)
    ).group_by(Comment.tarjeta_id).all():
        comment_counts[row[0]] = row[1]

    result = []
    for t in items:
        d = t.to_dict(include_image=include_image)
        d["tags"] = card_tags.get(t.id, [])
        d["subtasks_total"] = subtask_total.get(t.id, 0)
        d["subtasks_done"] = subtask_done.get(t.id, 0)
        d["comments_count"] = comment_counts.get(t.id, 0)
        d["cover_thumb_url"] = cover_map.get(t.id) or legacy_http_cover_map.get(t.id) or (t.image_url if include_image else None)
        d["media_count"] = media_count_map.get(t.id, 0)
        d["dias_en_columna"] = _calcular_dias_en_columna(t)
        result.append(d)
    return result


def _serialize_board_items(items: list[WarrantyCard], db: Session, include_image: bool) -> list[dict]:
    """Serializa tarjetas para vista tablero optimizada."""
    data = _enrich_batch(items, db, include_image=include_image)
    compact: list[dict] = []
    for item in data:
        problema = (item.get("problema") or "").strip()
        notas = (item.get("notas_tecnicas") or "").strip()
        cover_thumb = item.get("cover_thumb_url")
        if isinstance(cover_thumb, str) and cover_thumb.startswith("data:"):
            cover_thumb = None
        compact.append({
            "id": item.get("id"),
            "board_id": item.get("board_id"),
            "nombre_cliente": item.get("nombre_cliente"),
            "producto": item.get("producto"),
            "numero_factura": item.get("numero_factura"),
            "problema_resumen": (problema[:90] + "...") if len(problema) > 90 else problema,
            "columna": item.get("columna"),
            "prioridad": item.get("prioridad"),
            "posicion": item.get("posicion"),
            "asignado_nombre": item.get("asignado_nombre"),
            "asignado_a": item.get("asignado_a"),
            "whatsapp": item.get("whatsapp"),
            "fecha_limite": item.get("fecha_limite"),
            "fecha_compra": item.get("fecha_compra"),
            "notas_tecnicas_resumen": (notas[:120] + "...") if len(notas) > 120 else notas,
            "dias_en_columna": item.get("dias_en_columna", 0),
            "subtasks_total": item.get("subtasks_total", 0),
            "subtasks_done": item.get("subtasks_done", 0),
            "comments_count": item.get("comments_count", 0),
            "bloqueada": item.get("bloqueada"),
            "motivo_bloqueo": item.get("motivo_bloqueo"),
            "tags": item.get("tags", []),
            "cover_thumb_url": cover_thumb,
            "media_count": item.get("media_count", 0),
            "imagen_url": cover_thumb,
        })
    return compact


def _decode_legacy_data_image(image_url: str) -> tuple[str, bytes]:
    if not image_url.startswith("data:image/"):
        raise ValueError("Formato legacy invalido")
    header, encoded = image_url.split(",", 1)
    mime = header.split(";", 1)[0].split(":", 1)[1].lower()
    if mime not in ALLOWED_MEDIA_MIME:
        raise ValueError(f"MIME no soportado: {mime}")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception:
        raw = base64.b64decode(encoded)
    if not raw:
        raise ValueError("Imagen vacia")
    if len(raw) > MAX_MEDIA_SIZE_BYTES:
        raise ValueError(f"Archivo excede {MAX_MEDIA_SIZE_BYTES // (1024 * 1024)}MB")
    return mime, raw


def _migrate_legacy_image_for_card(
    db: Session,
    card: WarrantyCard,
    storage,
) -> WarrantyCardMedia | None:
    if not card.image_url or not card.image_url.startswith("data:image/"):
        return None
    existing = db.query(WarrantyCardMedia).filter(
        WarrantyCardMedia.tarjeta_id == card.id,
        WarrantyCardMedia.deleted_at.is_(None),
    ).first()
    if existing:
        return None
    mime, raw = _decode_legacy_data_image(card.image_url)
    upload = storage.upload_bytes_required(raw, mime, ALLOWED_MEDIA_MIME[mime])
    item = WarrantyCardMedia(
        tarjeta_id=card.id,
        storage_key=upload.get("storage_key"),
        url=upload["url"],
        thumb_url=upload["url"],
        position=0,
        is_cover=True,
        mime_type=mime,
        size_bytes=len(raw),
    )
    db.add(item)
    card.image_url = upload["url"]
    db.flush()
    return item


def _auto_migrate_legacy_for_cards(db: Session, cards: list[WarrantyCard], max_cards: int = 25) -> int:
    settings = get_settings()
    if not settings.use_s3_storage:
        return 0
    storage = get_storage_service()
    if not storage.use_s3:
        return 0

    migrated = 0
    for card in cards:
        if migrated >= max_cards:
            break
        try:
            item = _migrate_legacy_image_for_card(db, card, storage)
            if item is not None:
                db.commit()
                migrated += 1
        except Exception:
            db.rollback()
    if migrated > 0:
        invalidate_stats()
    return migrated


# ──────────────────────────────────────────────────────────────
# CRUD Endpoints
# ──────────────────────────────────────────────────────────────

@router.get("")
def get_tarjetas(
    board_id: int = Path(...),
    db: Session = Depends(get_db),
    page: int | None = Query(None),
    per_page: int | None = Query(None),
    light: int | None = Query(None),
    search: str | None = Query(None),
    estado: str | None = Query(None),
    prioridad: str | None = Query(None),
    asignado_a: int | None = Query(None),
    tag: int | None = Query(None),
    fecha_desde: str | None = Query(None),
    fecha_hasta: str | None = Query(None),
    include_deleted: bool = Query(False),
    view: str | None = Query(None),
    mode: str | None = Query(None),
    cursor: str | None = Query(None),
    include: str | None = Query(None),
):
    _get_board_or_404(board_id, db)
    include_image = light != 1
    board_mode = (view or "").lower() == "board"
    include_opts = {opt.strip().lower() for opt in (include or "").split(",") if opt.strip()}
    if board_mode:
        include_image = "image_thumb" in include_opts or "image" in include_opts

    q = db.query(WarrantyCard).filter(WarrantyCard.board_id == board_id)

    if not include_deleted:
        q = q.filter(WarrantyCard.deleted_at.is_(None))

    if not include_image:
        q = q.options(defer(WarrantyCard.image_url))

    if search:
        search_term = f"%{search}%"
        q = q.filter(or_(
            WarrantyCard.client_name.ilike(search_term),
            WarrantyCard.problem.ilike(search_term),
            WarrantyCard.whatsapp_number.ilike(search_term),
            WarrantyCard.product.ilike(search_term),
            WarrantyCard.invoice_number.ilike(search_term),
            WarrantyCard.technical_notes.ilike(search_term),
        ))
    if estado:
        q = q.filter(WarrantyCard.status == estado)
    if prioridad:
        q = q.filter(WarrantyCard.priority == prioridad)
    if asignado_a is not None:
        q = q.filter(WarrantyCard.assigned_to == asignado_a)
    if fecha_desde:
        q = q.filter(WarrantyCard.start_date >= datetime.strptime(fecha_desde, "%Y-%m-%d"))
    if fecha_hasta:
        q = q.filter(WarrantyCard.start_date <= datetime.strptime(fecha_hasta, "%Y-%m-%d"))
    if tag is not None:
        q = q.filter(
            exists(
                select(warranty_card_tags.c.warranty_card_id).where(
                    and_(
                        warranty_card_tags.c.warranty_card_id == WarrantyCard.id,
                        warranty_card_tags.c.tag_id == tag,
                    )
                )
            )
        )

    q = q.order_by(WarrantyCard.position.asc(), WarrantyCard.start_date.desc())

    if board_mode:
        per_page = min(per_page or 120, 200)
        include_totals = "totals" in include_opts
        fast_mode = (mode or "").lower() == "fast"

        if fast_mode:
            fast_q = q.options(defer(WarrantyCard.image_url)).order_by(None).order_by(WarrantyCard.id.asc())
            cursor_id: int | None = None
            if cursor:
                try:
                    cursor_id = int(cursor)
                except ValueError as err:
                    raise HTTPException(status_code=400, detail="Cursor invalido") from err
                fast_q = fast_q.filter(WarrantyCard.id > cursor_id)

            page_items = fast_q.limit(per_page + 1).all()
            has_next = len(page_items) > per_page
            items = page_items[:per_page]
            _auto_migrate_legacy_for_cards(db, items, max_cards=20)
            next_cursor = str(items[-1].id) if has_next and items else None
            total = q.order_by(None).count() if include_totals else None
            pages = ((total + per_page - 1) // per_page) if (include_totals and total is not None) else None
            data = {
                "tarjetas": _serialize_board_items(items, db, include_image=False),
                "pagination": {
                    "page": None,
                    "per_page": per_page,
                    "total": total,
                    "pages": pages,
                    "has_next": has_next,
                    "has_prev": cursor_id is not None,
                },
                "next_cursor": next_cursor,
                "view": "board",
                "mode": "fast",
            }
            return JSONResponse(content=data, headers=CACHE_HEADERS)

        page = page or 1
        page_items = q.offset((page - 1) * per_page).limit(per_page + 1).all()
        has_next = len(page_items) > per_page
        items = page_items[:per_page]
        _auto_migrate_legacy_for_cards(db, items, max_cards=20)
        total = q.order_by(None).count() if include_totals else None
        pages = ((total + per_page - 1) // per_page) if (include_totals and total is not None) else None
        data = {
            "tarjetas": _serialize_board_items(items, db, include_image=include_image),
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": pages,
                "has_next": has_next,
                "has_prev": page > 1,
            },
            "view": "board",
        }
        return JSONResponse(content=data, headers=CACHE_HEADERS)

    if page is None and per_page is None:
        items = q.limit(500).all()
        all_data = _enrich_batch(items, db, include_image=include_image)
        return JSONResponse(content=all_data, headers=CACHE_HEADERS)

    per_page = min(per_page or 50, 100)
    page = page or 1
    total = q.order_by(None).count()
    items = q.offset((page - 1) * per_page).limit(per_page).all()
    data = {
        "tarjetas": _enrich_batch(items, db, include_image=include_image),
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": (total + per_page - 1) // per_page if per_page else 0,
            "has_next": page * per_page < total,
            "has_prev": page > 1,
        },
    }
    return JSONResponse(content=data, headers=CACHE_HEADERS)


@router.get("/trash/list")
def get_trash(
    board_id: int = Path(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _get_board_or_404(board_id, db)
    items = db.query(WarrantyCard).filter(
        WarrantyCard.board_id == board_id,
        WarrantyCard.deleted_at.isnot(None),
    ).order_by(WarrantyCard.deleted_at.desc()).all()
    return [t.to_dict() for t in items]


@router.get("/{id}")
def get_tarjeta_by_id(
    id: int,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
):
    t = db.query(WarrantyCard).filter(
        WarrantyCard.id == id,
        WarrantyCard.board_id == board_id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
    enriched = _enrich_batch([t], db, include_image=True)
    if enriched:
        result = enriched[0]
        media_rows = _media_rows_for_card(db, t.id)
        result["media_preview"] = [
            {
                "id": m.id,
                "url": _resolve_media_url(m.url, m.storage_key),
                "thumb_url": _resolve_media_url(m.thumb_url or m.url, m.storage_key),
                "position": m.position,
                "is_cover": m.is_cover,
            }
            for m in media_rows[:6]
        ]
        return result
    return _enrich_tarjeta(t, db, include_image=True)


@router.post("", status_code=201)
@limiter.limit("10 per minute")
async def create_tarjeta(
    request: Request,
    data: TarjetaCreate,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    _get_board_or_404(board_id, db)
    settings = get_settings()
    nombre = (data.nombre_cliente or "").strip() or "Cliente"
    problema = (data.problema or "").strip() or "Sin descripción"
    whatsapp = (data.whatsapp or "").strip() or ""
    fecha_limite = data.fecha_limite
    if not fecha_limite:
        due_dt = datetime.now(UTC) + timedelta(days=1)
    else:
        from datetime import time as time_mod
        due_dt = datetime.combine(fecha_limite, time_mod.min)

    # Legacy image field (compat) + optional media_v2 bootstrap
    imagen_url = data.imagen_url
    uploaded_media_bootstrap: dict | None = None
    if imagen_url and imagen_url.startswith("data:"):
        storage = get_storage_service()
        if settings.media_v2_read_write:
            uploaded_media_bootstrap = storage.upload_image_required(imagen_url)
            imagen_url = uploaded_media_bootstrap["url"]
        else:
            imagen_url = storage.upload_image(imagen_url)

    # Asignación de técnico
    assigned_name = None
    if data.asignado_a:
        tech = db.query(User).filter(User.id == data.asignado_a).first()
        assigned_name = tech.full_name if tech else None

    # Siguiente posición en la columna
    max_pos = db.query(func.max(WarrantyCard.position)).filter(
        WarrantyCard.board_id == board_id,
        WarrantyCard.status == "recibido",
        WarrantyCard.deleted_at.is_(None),
    ).scalar() or 0

    # Parse purchase_date
    purchase_date = None
    if data.fecha_compra:
        try:
            purchase_date = datetime.strptime(str(data.fecha_compra), "%Y-%m-%d")
        except (ValueError, TypeError):
            pass

    t = WarrantyCard(
        board_id=board_id,
        client_name=nombre,
        problem=problema,
        whatsapp_number=whatsapp,
        product=(data.producto or "").strip() or None,
        invoice_number=(data.numero_factura or "").strip() or None,
        purchase_date=purchase_date,
        start_date=datetime.now(UTC),
        due_date=due_dt,
        status="recibido",
        recibido_date=datetime.now(UTC),
        image_url=imagen_url,
        priority=data.prioridad or "media",
        position=max_pos + 1,
        assigned_to=data.asignado_a,
        assigned_name=assigned_name,
        estimated_cost=data.costo_estimado,
        technical_notes=(data.notas_tecnicas or "").strip() or None,
    )
    db.add(t)
    try:
        db.commit()
        db.refresh(t)
    except IntegrityError as e:
        db.rollback()
        dialect = db.get_bind().dialect.name
        if dialect == "postgresql" and ("UniqueViolation" in str(e) or "duplicate" in str(e).lower()):
            try:
                db.execute(text(
                    "SELECT setval('warranty_cards_id_seq', COALESCE((SELECT MAX(id) FROM warranty_cards), 1), true);"
                ))
                db.commit()
                db.add(t)
                db.commit()
                db.refresh(t)
            except Exception as exc:
                db.rollback()
                raise HTTPException(status_code=500, detail="Error de secuencia de IDs") from exc
        else:
            raise HTTPException(status_code=500, detail="Error de integridad al crear tarjeta") from e

    if data.tags:
        for tag_id in data.tags:
            try:
                db.execute(insert(warranty_card_tags).values(warranty_card_id=t.id, tag_id=tag_id))
            except Exception:
                pass
        db.commit()

    if uploaded_media_bootstrap:
        db.add(WarrantyCardMedia(
            tarjeta_id=t.id,
            storage_key=uploaded_media_bootstrap.get("storage_key"),
            url=uploaded_media_bootstrap["url"],
            thumb_url=uploaded_media_bootstrap["url"],
            position=0,
            is_cover=True,
            mime_type="image/jpeg",
        ))
        db.commit()

    invalidate_stats()
    result = _enrich_tarjeta(t, db)

    try:
        await sio.emit("tarjeta_creada", {"event_version": 1, "board_id": board_id, "data": result}, room=f"board_{board_id}")
    except Exception:
        pass
    return result


@router.put("/{id}")
async def update_tarjeta(
    id: int,
    data: TarjetaUpdate,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    t = db.query(WarrantyCard).filter(
        WarrantyCard.id == id,
        WarrantyCard.board_id == board_id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")

    upd = data.model_dump(exclude_unset=True)
    if "nombre_cliente" in upd:
        t.client_name = upd["nombre_cliente"]
    if "producto" in upd:
        t.product = upd["producto"]
    if "problema" in upd:
        t.problem = upd["problema"]
    if "whatsapp" in upd:
        t.whatsapp_number = upd["whatsapp"]
    if "numero_factura" in upd:
        t.invoice_number = upd["numero_factura"]
    if "fecha_compra" in upd:
        try:
            t.purchase_date = datetime.strptime(upd["fecha_compra"], "%Y-%m-%d") if upd["fecha_compra"] else None
        except (ValueError, TypeError):
            pass
    if "fecha_limite" in upd:
        try:
            t.due_date = datetime.strptime(upd["fecha_limite"], "%Y-%m-%d")
        except (ValueError, TypeError):
            pass
    if "imagen_url" in upd:
        new_img = upd["imagen_url"]
        if new_img and new_img.startswith("data:"):
            storage = get_storage_service()
            new_img = storage.upload_image(new_img)
        t.image_url = new_img or None
    if "notas_tecnicas" in upd:
        t.technical_notes = upd["notas_tecnicas"] or None
    if "prioridad" in upd:
        t.priority = upd["prioridad"]
    if "posicion" in upd:
        t.position = upd["posicion"]
    if "asignado_a" in upd:
        t.assigned_to = upd["asignado_a"]
        if upd["asignado_a"]:
            tech = db.query(User).filter(User.id == upd["asignado_a"]).first()
            t.assigned_name = tech.full_name if tech else None
        else:
            t.assigned_name = None
    if "costo_estimado" in upd:
        t.estimated_cost = upd["costo_estimado"]
    if "costo_final" in upd:
        t.final_cost = upd["costo_final"]
    if "notas_costo" in upd:
        t.cost_notes = upd["notas_costo"]

    # Tags
    if "tags" in upd and upd["tags"] is not None:
        db.execute(delete(warranty_card_tags).where(warranty_card_tags.c.warranty_card_id == t.id))
        for tag_id in upd["tags"]:
            try:
                db.execute(insert(warranty_card_tags).values(warranty_card_id=t.id, tag_id=tag_id))
            except Exception:
                pass

    # Cambio de estado
    if "columna" in upd:
        nuevo = upd["columna"]
        valid_statuses = _get_valid_statuses(db, board_id)
        if nuevo not in valid_statuses:
            raise HTTPException(status_code=400, detail=f"Estado no válido. Permitidos: {valid_statuses}")

        _check_wip_limit(db, board_id, nuevo, exclude_card_id=id)

        old_status = t.status
        if old_status != nuevo:
            db.add(StatusHistory(
                tarjeta_id=t.id,
                old_status=old_status,
                new_status=nuevo,
                changed_at=datetime.now(UTC),
                changed_by=user.id if user else None,
                changed_by_name=user.full_name if user else None,
            ))
            notificar_cambio_estado(db, t, old_status, nuevo)

        t.status = nuevo
        _apply_status_transition(t, nuevo)

    db.commit()
    db.refresh(t)
    invalidate_stats()

    result = _enrich_tarjeta(t, db)
    try:
        await sio.emit("tarjeta_actualizada", {"event_version": 1, "board_id": board_id, "data": result}, room=f"board_{board_id}")
    except Exception:
        pass
    return result


# ──────────────────────────────────────────────────────────────
# Batch & Position Endpoints
# ──────────────────────────────────────────────────────────────

@router.put("/batch/positions")
async def batch_update_positions(
    data: BatchPosicionUpdate,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    import logging
    logger = logging.getLogger(__name__)
    _get_board_or_404(board_id, db)

    try:
        item_ids = [item.id for item in data.items]
        cards_by_id = {
            t.id: t
            for t in db.query(WarrantyCard).filter(
                WarrantyCard.id.in_(item_ids),
                WarrantyCard.board_id == board_id,
            ).all()
        }

        changed: list[dict] = []
        for item in data.items:
            t = cards_by_id.get(item.id)
            if t:
                old_status = t.status
                t.position = item.posicion
                if t.status != item.columna:
                    _check_wip_limit(db, board_id, item.columna, exclude_card_id=item.id)
                    db.add(StatusHistory(
                        tarjeta_id=t.id, old_status=old_status, new_status=item.columna,
                        changed_at=datetime.now(UTC),
                        changed_by=user.id,
                        changed_by_name=user.full_name,
                    ))
                    t.status = item.columna
                    _apply_status_transition(t, item.columna)
                changed.append({"id": t.id, "columna": t.status, "posicion": t.position})

        db.commit()
        invalidate_stats()
        try:
            await sio.emit("tarjetas_reordenadas", {"event_version": 1, "board_id": board_id, "data": {"items": changed}}, room=f"board_{board_id}")
        except Exception:
            pass
        return {"ok": True}
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("batch_update_positions failed: %s", exc)
        raise HTTPException(status_code=500, detail={
            "code": "batch_positions_error",
            "message": f"Error al actualizar posiciones: {type(exc).__name__}: {exc}",
        }) from exc


@router.post("/batch")
async def batch_operations(
    data: BatchOperationRequest,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Operaciones en lote sobre múltiples tarjetas."""
    _get_board_or_404(board_id, db)
    cards = db.query(WarrantyCard).filter(
        WarrantyCard.id.in_(data.ids),
        WarrantyCard.board_id == board_id,
    ).all()
    if not cards:
        raise HTTPException(status_code=404, detail="No cards found")

    updated = []
    for t in cards:
        if data.action == "move" and data.value:
            old_status = t.status
            t.status = data.value
            _apply_status_transition(t, data.value)
            db.add(StatusHistory(
                tarjeta_id=t.id, old_status=old_status, new_status=data.value,
                changed_at=datetime.now(UTC),
                changed_by=user.id,
                changed_by_name=data.user_name or user.full_name,
            ))
        elif data.action == "assign" and data.value is not None:
            t.assigned_to = int(data.value) if data.value else None
            t.assigned_name = data.assign_name or ""
        elif data.action == "priority" and data.value:
            t.priority = data.value
        elif data.action == "delete":
            t.deleted_at = datetime.now(UTC)
        elif data.action == "tag" and data.value is not None:
            existing = db.execute(
                select(warranty_card_tags.c.tag_id).where(
                    warranty_card_tags.c.warranty_card_id == t.id,
                    warranty_card_tags.c.tag_id == int(data.value),
                )
            ).first()
            if not existing:
                db.execute(warranty_card_tags.insert().values(
                    warranty_card_id=t.id, tag_id=int(data.value),
                ))
        updated.append(t.id)

    db.commit()
    invalidate_stats()

    refreshed = db.query(WarrantyCard).filter(WarrantyCard.id.in_(updated)).all()
    result = _enrich_batch(refreshed, db)
    try:
        for r in result:
            await sio.emit("tarjeta_actualizada", {"event_version": 1, "board_id": board_id, "data": r}, room=f"board_{board_id}")
    except Exception:
        pass
    return {"ok": True, "updated": len(updated), "tarjetas": result}


# ──────────────────────────────────────────────────────────────
# Delete / Restore Endpoints
# ──────────────────────────────────────────────────────────────

@router.delete("/{id}", status_code=204)
async def delete_tarjeta(
    id: int,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    t = db.query(WarrantyCard).filter(
        WarrantyCard.id == id,
        WarrantyCard.board_id == board_id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
    t.deleted_at = datetime.now(UTC)
    db.commit()
    invalidate_stats()
    try:
        await sio.emit("tarjeta_eliminada", {"event_version": 1, "board_id": board_id, "data": {"id": id}}, room=f"board_{board_id}")
    except Exception:
        pass
    return None


@router.put("/{id}/restore")
async def restore_tarjeta(
    id: int,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    t = db.query(WarrantyCard).filter(
        WarrantyCard.id == id,
        WarrantyCard.board_id == board_id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
    t.deleted_at = None
    db.commit()
    db.refresh(t)
    invalidate_stats()
    result = _enrich_tarjeta(t, db)
    try:
        await sio.emit("tarjeta_creada", {"event_version": 1, "board_id": board_id, "data": result}, room=f"board_{board_id}")
    except Exception:
        pass
    return result


@router.delete("/{id}/permanent", status_code=204)
async def permanent_delete_tarjeta(
    id: int,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
    admin: User = Depends(require_role("admin")),
):
    t = db.query(WarrantyCard).filter(
        WarrantyCard.id == id,
        WarrantyCard.board_id == board_id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
    if t.image_url and t.image_url.startswith("http"):
        storage = get_storage_service()
        storage.delete_image(t.image_url)
    media_rows = db.query(WarrantyCardMedia).filter(WarrantyCardMedia.tarjeta_id == id).all()
    if media_rows:
        storage = get_storage_service()
        if storage.use_s3 and storage._client:
            for m in media_rows:
                if m.storage_key:
                    try:
                        storage._client.delete_object(Bucket=storage._bucket, Key=m.storage_key)
                    except Exception:
                        pass
    db.delete(t)
    db.commit()
    invalidate_stats()
    return None


# ──────────────────────────────────────────────────────────────
# Timeline & History
# ──────────────────────────────────────────────────────────────

@router.get("/{id}/historial")
def get_historial(
    id: int,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
):
    t = db.query(WarrantyCard).filter(
        WarrantyCard.id == id,
        WarrantyCard.board_id == board_id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
    hist = db.query(StatusHistory).filter(StatusHistory.tarjeta_id == id).order_by(StatusHistory.changed_at.desc()).all()
    return [h.to_dict() for h in hist]


@router.get("/{id}/timeline")
def get_timeline(
    id: int,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
    cursor: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
):
    t = db.query(WarrantyCard).filter(
        WarrantyCard.id == id,
        WarrantyCard.board_id == board_id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")

    status_events = db.query(StatusHistory).filter(
        StatusHistory.tarjeta_id == id
    ).order_by(StatusHistory.changed_at.desc()).all()
    comment_events = db.query(Comment).filter(
        Comment.tarjeta_id == id
    ).order_by(Comment.created_at.desc()).all()

    events: list[dict] = []
    for e in status_events:
        events.append({
            "event_type": "status_changed",
            "event_at": e.changed_at.strftime("%Y-%m-%d %H:%M:%S") if e.changed_at else None,
            "event_id": f"status_{e.id}",
            "data": {
                "old_status": e.old_status,
                "new_status": e.new_status,
                "changed_by": e.changed_by,
                "changed_by_name": e.changed_by_name,
            },
        })
    for c in comment_events:
        events.append({
            "event_type": "comment_added",
            "event_at": c.created_at.strftime("%Y-%m-%d %H:%M:%S") if c.created_at else None,
            "event_id": f"comment_{c.id}",
            "data": {
                "comment_id": c.id,
                "author_name": c.author_name,
                "content": c.content,
                "user_id": c.user_id,
            },
        })

    events.sort(key=lambda x: x["event_at"] or "", reverse=True)
    slice_ = events[cursor:cursor + limit]
    next_cursor = cursor + len(slice_)
    return {
        "events": slice_,
        "next_cursor": next_cursor if next_cursor < len(events) else None,
        "total": len(events),
    }


# ──────────────────────────────────────────────────────────────
# Media Endpoints
# ──────────────────────────────────────────────────────────────

@router.get("/{id}/media")
def get_tarjeta_media(
    id: int,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
):
    t = db.query(WarrantyCard).filter(
        WarrantyCard.id == id,
        WarrantyCard.board_id == board_id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
    media = _media_rows_for_card(db, id)
    if media:
        out: list[dict] = []
        for m in media:
            d = m.to_dict()
            d["url"] = _resolve_media_url(m.url, m.storage_key)
            d["thumb_url"] = _resolve_media_url(m.thumb_url or m.url, m.storage_key)
            out.append(d)
        return out
    if t.image_url and t.image_url.startswith("data:image/"):
        try:
            settings = get_settings()
            if settings.use_s3_storage:
                storage = get_storage_service()
                if storage.use_s3:
                    item = _migrate_legacy_image_for_card(db, t, storage)
                    if item is not None:
                        db.commit()
                        invalidate_stats()
                        return [item.to_dict()]
        except Exception:
            db.rollback()
    if t.image_url:
        return [{
            "id": 0,
            "tarjeta_id": id,
            "storage_key": None,
            "url": t.image_url,
            "thumb_url": t.image_url,
            "position": 0,
            "is_cover": True,
            "mime_type": None,
            "size_bytes": None,
            "created_at": None,
            "deleted_at": None,
        }]
    return []


@router.post("/media/migrate-legacy")
def migrate_legacy_media_to_r2(
    board_id: int = Path(...),
    limit: int = Query(100, ge=1, le=1000),
    dry_run: bool = Query(False),
    only_card_id: int | None = Query(None),
    db: Session = Depends(get_db),
    admin: User = Depends(require_role("admin")),
):
    _get_board_or_404(board_id, db)
    settings = get_settings()
    if not settings.use_s3_storage:
        raise HTTPException(status_code=400, detail="Storage remoto deshabilitado")
    storage = get_storage_service()
    if not storage.use_s3:
        raise HTTPException(status_code=503, detail="Storage remoto no disponible")

    q = db.query(WarrantyCard).filter(
        WarrantyCard.board_id == board_id,
        WarrantyCard.deleted_at.is_(None),
        WarrantyCard.image_url.isnot(None),
        WarrantyCard.image_url.like("data:image/%"),
    )
    if only_card_id is not None:
        q = q.filter(WarrantyCard.id == only_card_id)
    cards = q.order_by(WarrantyCard.id.asc()).limit(limit).all()

    migrated = 0
    skipped_has_media = 0
    failed = 0
    details: list[dict] = []

    for t in cards:
        has_media = db.query(WarrantyCardMedia.id).filter(
            WarrantyCardMedia.tarjeta_id == t.id,
            WarrantyCardMedia.deleted_at.is_(None),
        ).first()
        if has_media:
            skipped_has_media += 1
            details.append({"tarjeta_id": t.id, "status": "skipped_has_media"})
            continue

        try:
            if dry_run:
                mime, raw = _decode_legacy_data_image(t.image_url or "")
                details.append({"tarjeta_id": t.id, "status": "dry_run_ok", "mime_type": mime, "size_bytes": len(raw)})
                continue

            started = time.perf_counter()
            item = _migrate_legacy_image_for_card(db, t, storage)
            if item is None:
                skipped_has_media += 1
                details.append({"tarjeta_id": t.id, "status": "skipped"})
                continue
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            db.commit()
            migrated += 1
            details.append({
                "tarjeta_id": t.id,
                "status": "migrated",
                "storage_key": item.storage_key,
                "url": item.url,
                "latency_ms": elapsed_ms,
            })
        except Exception as err:
            db.rollback()
            failed += 1
            details.append({"tarjeta_id": t.id, "status": "failed", "error": str(err)})

    if migrated > 0:
        invalidate_stats()

    return {
        "ok": failed == 0,
        "requested_by": admin.username,
        "dry_run": dry_run,
        "processed": len(cards),
        "migrated": migrated,
        "skipped_has_media": skipped_has_media,
        "failed": failed,
        "details": details,
    }


@router.post("/{id}/media", status_code=201)
async def upload_tarjeta_media(
    id: int,
    board_id: int = Path(...),
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    settings = get_settings()
    if not settings.media_v2_read_write:
        raise HTTPException(status_code=400, detail="Media v2 deshabilitado")
    t = db.query(WarrantyCard).filter(
        WarrantyCard.id == id,
        WarrantyCard.board_id == board_id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
    current = _media_rows_for_card(db, id)
    if len(current) + len(files) > MAX_MEDIA_PER_CARD:
        raise HTTPException(status_code=400, detail=f"Limite de {MAX_MEDIA_PER_CARD} fotos por tarjeta")

    storage = get_storage_service()
    if not storage.use_s3:
        raise HTTPException(status_code=503, detail="Storage remoto no disponible")

    next_pos = max([m.position for m in current], default=-1) + 1
    created: list[dict] = []
    for f in files:
        mime = (f.content_type or "").lower()
        if mime not in ALLOWED_MEDIA_MIME:
            raise HTTPException(status_code=400, detail=f"Formato no soportado: {mime}")
        file_data = await f.read()
        if len(file_data) > MAX_MEDIA_SIZE_BYTES:
            raise HTTPException(status_code=400, detail=f"Archivo excede {MAX_MEDIA_SIZE_BYTES // (1024 * 1024)}MB")
        started = time.perf_counter()
        upload = storage.upload_bytes_required(file_data, mime, ALLOWED_MEDIA_MIME[mime])
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        from loguru import logger
        logger.bind(
            storage="R2", bucket=storage._bucket, key=upload.get("storage_key"),
            tarjeta_id=id, user_id=user.id, mime_type=mime, size_bytes=len(file_data),
        ).info(f"media_upload_ok latency_ms={elapsed_ms}")
        item = WarrantyCardMedia(
            tarjeta_id=id,
            storage_key=upload.get("storage_key"),
            url=upload["url"],
            thumb_url=upload["url"],
            position=next_pos,
            is_cover=(len(current) == 0 and next_pos == 0),
            mime_type=mime,
            size_bytes=len(file_data),
        )
        db.add(item)
        db.flush()
        created.append(item.to_dict())
        next_pos += 1
    db.commit()
    invalidate_stats()
    return created


@router.put("/{id}/media/reorder")
def reorder_tarjeta_media(
    id: int,
    data: MediaReorderRequest,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    t = db.query(WarrantyCard).filter(
        WarrantyCard.id == id,
        WarrantyCard.board_id == board_id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
    by_id = {m.id: m for m in _media_rows_for_card(db, id)}
    for entry in data.items:
        m = by_id.get(entry.id)
        if m:
            m.position = entry.position
    db.commit()
    return {"ok": True}


@router.patch("/{id}/media/{media_id}")
def update_tarjeta_media(
    id: int,
    media_id: int,
    body: dict,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    t = db.query(WarrantyCard).filter(
        WarrantyCard.id == id,
        WarrantyCard.board_id == board_id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
    m = db.query(WarrantyCardMedia).filter(
        WarrantyCardMedia.id == media_id,
        WarrantyCardMedia.tarjeta_id == id,
        WarrantyCardMedia.deleted_at.is_(None),
    ).first()
    if not m:
        raise HTTPException(status_code=404, detail="Media no encontrada")
    if body.get("is_cover") is True:
        db.query(WarrantyCardMedia).filter(
            WarrantyCardMedia.tarjeta_id == id,
            WarrantyCardMedia.deleted_at.is_(None),
        ).update({"is_cover": False}, synchronize_session=False)
        m.is_cover = True
        t.image_url = m.url
    db.commit()
    return m.to_dict()


@router.delete("/{id}/media/{media_id}", status_code=204)
def delete_tarjeta_media(
    id: int,
    media_id: int,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    t = db.query(WarrantyCard).filter(
        WarrantyCard.id == id,
        WarrantyCard.board_id == board_id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
    m = db.query(WarrantyCardMedia).filter(
        WarrantyCardMedia.id == media_id,
        WarrantyCardMedia.tarjeta_id == id,
        WarrantyCardMedia.deleted_at.is_(None),
    ).first()
    if not m:
        raise HTTPException(status_code=404, detail="Media no encontrada")
    m.deleted_at = datetime.now(UTC)
    if m.storage_key:
        storage = get_storage_service()
        if storage.use_s3 and storage._client:
            try:
                storage._client.delete_object(Bucket=storage._bucket, Key=m.storage_key)
            except Exception:
                pass

    active = _media_rows_for_card(db, id)
    if active and all(not it.is_cover for it in active):
        active[0].is_cover = True
        t.image_url = active[0].url
    elif not active:
        t.image_url = None
    db.commit()
    return None


# ──────────────────────────────────────────────────────────────
# Block / Unblock
# ──────────────────────────────────────────────────────────────

@router.patch("/{id}/block")
async def block_tarjeta(
    id: int,
    data: BlockRequest,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Bloquear o desbloquear una tarjeta."""
    t = db.query(WarrantyCard).filter(
        WarrantyCard.id == id,
        WarrantyCard.board_id == board_id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")

    if data.blocked:
        t.blocked_at = datetime.now(UTC)
        t.blocked_reason = data.reason or ""
        t.blocked_by = data.user_id or user.id
    else:
        t.blocked_at = None
        t.blocked_reason = None
        t.blocked_by = None

    db.commit()
    db.refresh(t)
    result = _enrich_tarjeta(t, db)
    try:
        await sio.emit("tarjeta_actualizada", {"event_version": 1, "board_id": board_id, "data": result}, room=f"board_{board_id}")
    except Exception:
        pass
    return result
