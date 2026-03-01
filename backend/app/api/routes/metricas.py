"""Métricas Kanban avanzadas: Cycle Time, Lead Time, Throughput, CFD.

Optimizado: queries batch en vez de O(N*M), cálculos en DB en vez de Python.
"""
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.cache import get_cached, set_cached
from app.core.database import get_db
from app.models.warranty_card import StatusHistory, WarrantyCard

router = APIRouter(prefix="/api/boards/{board_id}", tags=["metricas"])

METRICAS_TTL = 120  # 2 minutes


@router.get("/metricas/kanban")
def get_kanban_metrics(
    board_id: int = Path(...),
    db: Session = Depends(get_db),
    dias: int = Query(30, description="Días de histórico", ge=1, le=365),
):
    """Retorna métricas avanzadas de Kanban."""
    cache_key = f"metricas_board_{board_id}_{dias}"
    cached = get_cached(cache_key, METRICAS_TTL)
    if cached is not None:
        return cached

    now = datetime.now(UTC)
    desde = now - timedelta(days=dias)
    dialect = db.get_bind().dialect.name

    base = db.query(WarrantyCard).filter(
        WarrantyCard.board_id == board_id,
        WarrantyCard.deleted_at.is_(None),
    )

    # --- 1. Cycle Time (recibido → entregado) via DB aggregation ---
    if dialect == "sqlite":
        ct_expr = func.julianday(WarrantyCard.entregado_date) - func.julianday(WarrantyCard.recibido_date)
    else:
        ct_expr = func.extract("epoch", WarrantyCard.entregado_date - WarrantyCard.recibido_date) / 86400

    ct_filters = [
        WarrantyCard.board_id == board_id,
        WarrantyCard.deleted_at.is_(None),
        WarrantyCard.entregado_date.isnot(None),
        WarrantyCard.recibido_date.isnot(None),
        WarrantyCard.entregado_date >= desde,
    ]

    # Aggregated cycle time from DB
    ct_stats = db.query(
        func.avg(ct_expr).label("avg_days"),
        func.count(WarrantyCard.id).label("total"),
    ).filter(*ct_filters).first()

    avg_cycle = round(float(ct_stats.avg_days or 0), 1)
    total_completed = ct_stats.total or 0

    # Top 20 detail (only load what's needed)
    cycle_detail = db.query(
        WarrantyCard.id,
        WarrantyCard.client_name,
        ct_expr.label("dias"),
    ).filter(*ct_filters).order_by(ct_expr.desc()).limit(20).all()

    cycle_times = [{"id": r.id, "nombre": r.client_name, "dias": round(float(r.dias or 0), 1)} for r in cycle_detail]

    # --- 2. Lead Time por etapa (DB aggregation) ---
    if dialect == "sqlite":
        lead_rec_gest = func.avg(func.julianday(WarrantyCard.en_gestion_date) - func.julianday(WarrantyCard.recibido_date))
        lead_gest_res = func.avg(func.julianday(WarrantyCard.resuelto_date) - func.julianday(WarrantyCard.en_gestion_date))
        lead_res_ent = func.avg(func.julianday(WarrantyCard.entregado_date) - func.julianday(WarrantyCard.resuelto_date))
    else:
        lead_rec_gest = func.avg(func.extract("epoch", WarrantyCard.en_gestion_date - WarrantyCard.recibido_date) / 86400)
        lead_gest_res = func.avg(func.extract("epoch", WarrantyCard.resuelto_date - WarrantyCard.en_gestion_date) / 86400)
        lead_res_ent = func.avg(func.extract("epoch", WarrantyCard.entregado_date - WarrantyCard.resuelto_date) / 86400)

    lead_row = db.query(
        lead_rec_gest.label("rec_gest"),
        lead_gest_res.label("gest_res"),
        lead_res_ent.label("res_ent"),
    ).filter(*ct_filters).first()

    avg_leads = {
        "recibido_en_gestion": round(float(lead_row.rec_gest or 0), 1),
        "en_gestion_resuelto": round(float(lead_row.gest_res or 0), 1),
        "resuelto_entregado": round(float(lead_row.res_ent or 0), 1),
    }

    # --- 3. Throughput semanal (single query with GROUP BY) ---
    semanas = max(dias // 7, 4)
    since_weeks = now - timedelta(weeks=semanas)

    if dialect == "sqlite":
        week_expr = func.strftime("%Y-%W", WarrantyCard.entregado_date)
    else:
        week_expr = func.date_trunc("week", WarrantyCard.entregado_date)

    tp_rows = db.query(
        week_expr.label("semana"),
        func.count(WarrantyCard.id).label("completadas"),
    ).filter(
        WarrantyCard.board_id == board_id,
        WarrantyCard.deleted_at.is_(None),
        WarrantyCard.entregado_date.isnot(None),
        WarrantyCard.entregado_date >= since_weeks,
    ).group_by(week_expr).order_by(week_expr).all()

    throughput = []
    for row in tp_rows:
        week_label = row.semana
        if hasattr(week_label, "strftime"):
            week_label = week_label.strftime("%d/%m")
        elif isinstance(week_label, str) and len(week_label) > 5:
            week_label = week_label[-5:]
        throughput.append({"semana": str(week_label), "completadas": row.completadas})

    # --- 4. CFD: Single query using current counts per status ---
    statuses = ["recibido", "en_gestion", "resuelto", "entregado"]

    # Current state snapshot (fast, 1 query)
    current_counts = db.query(
        WarrantyCard.status,
        func.count(WarrantyCard.id),
    ).filter(
        WarrantyCard.board_id == board_id,
        WarrantyCard.deleted_at.is_(None),
    ).group_by(WarrantyCard.status).all()
    current_map = dict(current_counts)

    # Historical CFD: batch query grouping StatusHistory by date + status
    sample_days = min(dias, 60)
    cfd_since = now - timedelta(days=sample_days)

    if dialect == "sqlite":
        day_expr = func.strftime("%Y-%m-%d", StatusHistory.changed_at)
    else:
        day_expr = func.date_trunc("day", StatusHistory.changed_at)

    # Get transition counts grouped by day and new_status (1 query instead of 240)
    # Join with WarrantyCard to filter by board_id
    cfd_rows = db.query(
        day_expr.label("dia"),
        StatusHistory.new_status,
        func.count(StatusHistory.id).label("cnt"),
    ).join(
        WarrantyCard, StatusHistory.tarjeta_id == WarrantyCard.id,
    ).filter(
        WarrantyCard.board_id == board_id,
        StatusHistory.changed_at >= cfd_since,
        StatusHistory.new_status.in_(statuses),
    ).group_by(day_expr, StatusHistory.new_status).order_by(day_expr).all()

    # Build cumulative flow from transition counts
    cfd_by_day: dict[str, dict[str, int]] = {}
    for row in cfd_rows:
        day_str = row.dia
        if hasattr(day_str, "strftime"):
            day_str = day_str.strftime("%d/%m")
        elif isinstance(day_str, str) and len(day_str) >= 10:
            day_str = f"{day_str[8:10]}/{day_str[5:7]}"
        if day_str not in cfd_by_day:
            cfd_by_day[day_str] = {s: 0 for s in statuses}
        cfd_by_day[day_str][row.new_status] = row.cnt

    cfd_data: list[dict[str, str | int]] = [
        {"fecha": day, **counts} for day, counts in cfd_by_day.items()
    ]

    # Add current point
    current_point: dict[str, str | int] = {"fecha": now.strftime("%d/%m")}
    for status in statuses:
        current_point[status] = current_map.get(status, 0)
    cfd_data.append(current_point)

    # --- 5. SLA violations (batch query) ---
    from app.models.kanban import KanbanColumn
    columns = db.query(KanbanColumn).filter(
        KanbanColumn.board_id == board_id,
        KanbanColumn.sla_hours.isnot(None),
    ).all()

    sla_violations = []
    if columns:
        date_field_map = {
            "recibido": WarrantyCard.recibido_date,
            "en_gestion": WarrantyCard.en_gestion_date,
            "resuelto": WarrantyCard.resuelto_date,
        }

        for col in columns:
            if not col.sla_hours:
                continue
            date_field = date_field_map.get(col.key)
            if date_field is None:
                continue
            threshold = now - timedelta(hours=col.sla_hours)

            # Single query per SLA column with DB-side hour calculation
            if dialect == "sqlite":
                hours_expr = (func.julianday("now") - func.julianday(date_field)) * 24
            else:
                hours_expr = func.extract("epoch", func.now() - date_field) / 3600

            violators = db.query(
                WarrantyCard.id,
                WarrantyCard.client_name,
                hours_expr.label("hours_in"),
            ).filter(
                WarrantyCard.board_id == board_id,
                WarrantyCard.deleted_at.is_(None),
                WarrantyCard.status == col.key,
                date_field.isnot(None),
                date_field < threshold,
            ).all()

            for v in violators:
                sla_violations.append({
                    "tarjeta_id": v.id,
                    "nombre": v.client_name,
                    "columna": col.title,
                    "horas_en_columna": round(float(v.hours_in or 0)),
                    "sla_horas": col.sla_hours,
                })

    # --- 6. Blocked cards count ---
    blocked_count = base.filter(WarrantyCard.blocked_at.isnot(None)).count()

    result = {
        "cycle_time": {
            "promedio_dias": avg_cycle,
            "total_completadas": total_completed,
            "detalle": cycle_times,
        },
        "lead_time_por_etapa": avg_leads,
        "throughput_semanal": throughput,
        "cfd": cfd_data,
        "sla_violations": sla_violations,
        "blocked_count": blocked_count,
    }

    set_cached(cache_key, result, METRICAS_TTL)
    return result
