import csv
from datetime import UTC, datetime
from io import BytesIO, StringIO

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.limiter import limiter
from app.models.warranty_card import WarrantyCard

router = APIRouter(prefix="/api/boards/{board_id}", tags=["exportar"])

BATCH_SIZE = 500
EXCEL_LIMIT = 5000


def _row_to_csv_dict(t: WarrantyCard) -> dict:
    return {
        "ID": t.id,
        "Cliente": t.client_name,
        "WhatsApp": t.whatsapp_number,
        "Producto": t.product or "",
        "Número Factura": t.invoice_number or "",
        "Fecha Compra": t.purchase_date.strftime("%Y-%m-%d") if t.purchase_date else "",
        "Problema": t.problem,
        "Estado": t.status,
        "Prioridad": t.priority,
        "Asignado A": t.assigned_name or "",
        "Fecha Inicio": t.start_date.strftime("%Y-%m-%d %H:%M") if t.start_date else "",
        "Fecha Límite": t.due_date.strftime("%Y-%m-%d") if t.due_date else "",
        "Notas Técnicas": t.technical_notes or "",
        "Costo Estimado": t.estimated_cost or "",
        "Costo Final": t.final_cost or "",
        "Fecha Recibido": t.recibido_date.strftime("%Y-%m-%d %H:%M") if t.recibido_date else "",
        "Fecha En Gestión": t.en_gestion_date.strftime("%Y-%m-%d %H:%M") if t.en_gestion_date else "",
        "Fecha Resuelto": t.resuelto_date.strftime("%Y-%m-%d %H:%M") if t.resuelto_date else "",
        "Fecha Entregado": t.entregado_date.strftime("%Y-%m-%d %H:%M") if t.entregado_date else "",
    }


@router.get("/exportar")
@limiter.limit("20 per minute")
def exportar_datos(
    request: Request,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
    formato: str = Query("csv"),
    estado: str | None = Query(None),
    fecha_desde: str | None = Query(None),
    fecha_hasta: str | None = Query(None),
):
    query = db.query(WarrantyCard).filter(
        WarrantyCard.board_id == board_id,
        WarrantyCard.deleted_at.is_(None),
    )
    if estado and estado != "todos":
        query = query.filter(WarrantyCard.status == estado)
    if fecha_desde:
        query = query.filter(WarrantyCard.start_date >= datetime.strptime(fecha_desde, "%Y-%m-%d"))
    if fecha_hasta:
        query = query.filter(WarrantyCard.start_date <= datetime.strptime(fecha_hasta, "%Y-%m-%d"))

    total_count = query.count()
    if total_count == 0:
        raise HTTPException(status_code=404, detail="No hay datos para exportar con los filtros especificados")

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    ordered = query.order_by(WarrantyCard.start_date.desc())

    if formato == "excel":
        if total_count > EXCEL_LIMIT:
            raise HTTPException(
                status_code=400,
                detail=f"Excel limitado a {EXCEL_LIMIT} filas. Use CSV para exportar más datos.",
            )
        tarjetas = ordered.all()
        datos = [_row_to_csv_dict(t) for t in tarjetas]
        df = pd.DataFrame(datos)
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Garantías")
        output.seek(0)
        return Response(
            content=output.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename=garantias_board{board_id}_{timestamp}.xlsx"},
        )

    headers = [
        "ID", "Cliente", "WhatsApp", "Producto", "Número Factura", "Fecha Compra",
        "Problema", "Estado", "Prioridad", "Asignado A",
        "Fecha Inicio", "Fecha Límite", "Notas Técnicas", "Costo Estimado", "Costo Final",
        "Fecha Recibido", "Fecha En Gestión", "Fecha Resuelto", "Fecha Entregado",
    ]

    def generate_csv():
        yield "\ufeff"
        buf = StringIO()
        wr = csv.DictWriter(buf, fieldnames=headers)
        wr.writeheader()
        yield buf.getvalue()
        offset = 0
        while True:
            batch = ordered.offset(offset).limit(BATCH_SIZE).all()
            if not batch:
                break
            buf = StringIO()
            wr = csv.DictWriter(buf, fieldnames=headers)
            for t in batch:
                wr.writerow(_row_to_csv_dict(t))
            yield buf.getvalue()
            offset += BATCH_SIZE

    if total_count <= BATCH_SIZE:
        datos = [_row_to_csv_dict(t) for t in ordered.all()]
        df = pd.DataFrame(datos)
        output = BytesIO()
        df.to_csv(output, index=False, encoding="utf-8-sig")
        output.seek(0)
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=garantias_board{board_id}_{timestamp}.csv"},
        )

    return StreamingResponse(
        generate_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=garantias_board{board_id}_{timestamp}.csv"},
    )
