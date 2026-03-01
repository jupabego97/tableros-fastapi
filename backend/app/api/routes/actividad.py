"""Activity feed: historial de actividad por tablero."""
from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.warranty_card import StatusHistory, WarrantyCard

router = APIRouter(prefix="/api/boards/{board_id}/actividad", tags=["actividad"])


@router.get("")
def get_activity_feed(
    board_id: int = Path(...),
    db: Session = Depends(get_db),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    tarjeta_id: int | None = Query(None),
):
    """Feed de actividad por tablero o por tarjeta."""
    q = (
        db.query(StatusHistory)
        .join(WarrantyCard, StatusHistory.tarjeta_id == WarrantyCard.id)
        .filter(WarrantyCard.board_id == board_id)
        .order_by(desc(StatusHistory.changed_at))
    )

    if tarjeta_id is not None:
        q = q.filter(StatusHistory.tarjeta_id == tarjeta_id)

    total = q.count()
    items = q.offset(offset).limit(limit).all()

    card_ids = list({h.tarjeta_id for h in items})
    cards = {}
    if card_ids:
        for c in db.query(WarrantyCard.id, WarrantyCard.client_name).filter(WarrantyCard.id.in_(card_ids)).all():
            cards[c.id] = c.client_name

    feed = []
    for h in items:
        d = h.to_dict()
        d["nombre_cliente"] = cards.get(h.tarjeta_id, "Desconocido")
        feed.append(d)

    return {"actividad": feed, "total": total}
