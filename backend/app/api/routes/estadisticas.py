"""Estadísticas por tablero con soporte dual SQLite/PostgreSQL."""
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Path
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.core.cache import DEFAULT_TTL, get_cached, set_cached
from app.core.database import get_db
from app.models.warranty_card import WarrantyCard

router = APIRouter(prefix="/api/boards/{board_id}/estadisticas", tags=["estadisticas"])


def _safe_avg_days(db, date_start, date_end, *filters) -> float:
    dialect = db.get_bind().dialect.name
    try:
        if dialect == "sqlite":
            expr = func.avg(func.julianday(date_end) - func.julianday(date_start))
        else:
            expr = func.avg(func.extract("epoch", date_end - date_start) / 86400)
        result = db.query(expr).filter(*filters).scalar()
        return round(float(result or 0), 1)
    except Exception:
        return 0.0


def _compute_estadisticas(db, board_id: int) -> dict:
    not_deleted = WarrantyCard.deleted_at.is_(None)
    is_board = WarrantyCard.board_id == board_id
    dialect = db.get_bind().dialect.name
    hace_un_mes = datetime.now(UTC) - timedelta(days=30)

    agg = db.query(
        WarrantyCard.status,
        WarrantyCard.priority,
        func.count(WarrantyCard.id).label("cnt"),
        func.sum(case(((WarrantyCard.status == "entregado") & (WarrantyCard.entregado_date >= hace_un_mes), 1), else_=0)).label("completadas_mes"),
        func.sum(case((WarrantyCard.status != "entregado", 1), else_=0)).label("pendientes"),
        func.sum(case((WarrantyCard.technical_notes.isnot(None) & (WarrantyCard.technical_notes != ""), 1), else_=0)).label("con_notas"),
    ).filter(not_deleted, is_board).group_by(WarrantyCard.status, WarrantyCard.priority).all()

    totales_por_estado: dict = {}
    dist_prioridad: dict = {}
    completadas_mes = 0
    pendientes = 0
    con_notas = 0
    total_tarjetas = 0

    for row in agg:
        totales_por_estado[row.status] = totales_por_estado.get(row.status, 0) + row.cnt
        dist_prioridad[row.priority or "media"] = dist_prioridad.get(row.priority or "media", 0) + row.cnt
        completadas_mes += row.completadas_mes or 0
        pendientes += row.pendientes or 0
        con_notas += row.con_notas or 0
        total_tarjetas += row.cnt

    tiempos_promedio = {
        "recibido_a_en_gestion": _safe_avg_days(db, WarrantyCard.recibido_date, WarrantyCard.en_gestion_date, WarrantyCard.en_gestion_date.isnot(None), not_deleted, is_board),
        "en_gestion_a_resuelto": _safe_avg_days(db, WarrantyCard.en_gestion_date, WarrantyCard.resuelto_date, WarrantyCard.resuelto_date.isnot(None), WarrantyCard.en_gestion_date.isnot(None), not_deleted, is_board),
        "resuelto_a_entregado": _safe_avg_days(db, WarrantyCard.resuelto_date, WarrantyCard.entregado_date, WarrantyCard.entregado_date.isnot(None), WarrantyCard.resuelto_date.isnot(None), not_deleted, is_board),
    }

    top_problemas = [{"problema": p, "cantidad": c} for p, c in
        db.query(WarrantyCard.problem, func.count(WarrantyCard.id)).filter(not_deleted, is_board).group_by(WarrantyCard.problem).order_by(func.count(WarrantyCard.id).desc()).limit(5).all()]

    financials = db.query(func.sum(WarrantyCard.estimated_cost).label("est"), func.sum(WarrantyCard.final_cost).label("fin")).filter(not_deleted, is_board).first()

    seis_meses = datetime.now(UTC) - timedelta(days=180)
    mes_expr = func.strftime("%Y-%m", WarrantyCard.start_date) if dialect == "sqlite" else func.date_trunc("month", WarrantyCard.start_date)
    tendencia = db.query(mes_expr.label("mes"), func.count(WarrantyCard.id).label("total")).filter(WarrantyCard.start_date >= seis_meses, not_deleted, is_board).group_by(mes_expr).order_by(mes_expr).all()
    tendencia_meses = [{"mes": m.strftime("%Y-%m") if hasattr(m, "strftime") else str(m)[:7] if m else None, "total": tot} for m, tot in tendencia]

    return {
        "board_id": board_id,
        "totales_por_estado": totales_por_estado,
        "tiempos_promedio_dias": tiempos_promedio,
        "completadas_ultimo_mes": completadas_mes,
        "pendientes": pendientes,
        "top_problemas": top_problemas,
        "tendencia_6_meses": tendencia_meses,
        "total_garantias": total_tarjetas,
        "con_notas_tecnicas": con_notas,
        "distribucion_prioridad": dist_prioridad,
        "resumen_financiero": {"total_estimado": round(float(financials.est or 0), 2), "total_cobrado": round(float(financials.fin or 0), 2)},
        "generado_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
    }


@router.get("")
def get_estadisticas(board_id: int = Path(...), db: Session = Depends(get_db)):
    cache_key = f"estadisticas_board_{board_id}"
    cached_val = get_cached(cache_key, DEFAULT_TTL)
    if cached_val is not None:
        return cached_val
    result = _compute_estadisticas(db, board_id)
    set_cached(cache_key, result, DEFAULT_TTL)
    return result
