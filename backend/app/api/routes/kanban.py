"""Rutas para gestión del tablero Kanban: columnas, tags, subtasks, comments, notificaciones."""
import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.board import Board
from app.models.kanban import Comment, KanbanColumn, Notification, SubTask, Tag, warranty_card_tags
from app.models.user import User
from app.models.warranty_card import WarrantyCard
from app.schemas.kanban import (
    ColumnCreate,
    ColumnReorder,
    ColumnUpdate,
    CommentCreate,
    KanbanRules,
    NotificationMarkRead,
    SubTaskCreate,
    SubTaskUpdate,
    TagCreate,
    TagUpdate,
)
from app.services.auth_service import get_current_user, get_current_user_optional, require_role

router = APIRouter(prefix="/api/boards/{board_id}", tags=["kanban"])


def _get_board_or_404(board_id: int, db: Session) -> Board:
    board = db.query(Board).filter(Board.id == board_id).first()
    if not board:
        raise HTTPException(status_code=404, detail={"code": "board_not_found", "message": "Tablero no encontrado"})
    return board


# ==================== KANBAN RULES ====================

@router.get("/kanban/rules")
def get_kanban_rules(board_id: int = Path(...), db: Session = Depends(get_db)):
    _get_board_or_404(board_id, db)
    cols = db.query(KanbanColumn).filter(
        KanbanColumn.board_id == board_id
    ).order_by(KanbanColumn.position).all()
    wip_limits: dict[str, int] = {}
    sla_by_column: dict[str, int] = {}
    transition_requirements: dict[str, list[str]] = {}
    for col in cols:
        if col.wip_limit is not None:
            wip_limits[col.key] = col.wip_limit
        if col.sla_hours is not None:
            sla_by_column[col.key] = col.sla_hours
        if col.required_fields:
            try:
                transition_requirements[col.key] = json.loads(col.required_fields)
            except Exception:
                transition_requirements[col.key] = []
    return {
        "wip_limits": wip_limits,
        "sla_by_column": sla_by_column,
        "transition_requirements": transition_requirements,
    }


@router.put("/kanban/rules")
def update_kanban_rules(
    data: KanbanRules,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role("admin")),
):
    _get_board_or_404(board_id, db)
    by_key = {c.key: c for c in db.query(KanbanColumn).filter(KanbanColumn.board_id == board_id).all()}
    for key, value in data.wip_limits.items():
        col = by_key.get(key)
        if col:
            col.wip_limit = value
    for key, value in data.sla_by_column.items():
        col = by_key.get(key)
        if col:
            col.sla_hours = value
    for key, fields in data.transition_requirements.items():
        col = by_key.get(key)
        if col:
            col.required_fields = json.dumps(fields, ensure_ascii=True)
    db.commit()
    return get_kanban_rules(board_id=board_id, db=db)


# ==================== COLUMNAS ====================

@router.get("/columnas")
def get_columnas(board_id: int = Path(...), db: Session = Depends(get_db)):
    _get_board_or_404(board_id, db)
    cols = db.query(KanbanColumn).filter(
        KanbanColumn.board_id == board_id
    ).order_by(KanbanColumn.position).all()
    return [c.to_dict() for c in cols]


@router.post("/columnas", status_code=201)
def create_columna(
    data: ColumnCreate,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
):
    _get_board_or_404(board_id, db)
    if db.query(KanbanColumn).filter(
        KanbanColumn.key == data.key,
        KanbanColumn.board_id == board_id,
    ).first():
        raise HTTPException(status_code=409, detail="Ya existe una columna con esa clave en este tablero")
    col_data = data.model_dump()
    col_data["board_id"] = board_id
    col = KanbanColumn(**col_data)
    db.add(col)
    db.commit()
    db.refresh(col)
    return col.to_dict()


@router.put("/columnas/{col_id}")
def update_columna(
    col_id: int,
    data: ColumnUpdate,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
):
    col = db.query(KanbanColumn).filter(
        KanbanColumn.id == col_id,
        KanbanColumn.board_id == board_id,
    ).first()
    if not col:
        raise HTTPException(status_code=404, detail="Columna no encontrada")
    upd = data.model_dump(exclude_unset=True)
    for k, v in upd.items():
        setattr(col, k, v)
    db.commit()
    db.refresh(col)
    return col.to_dict()


@router.delete("/columnas/{col_id}", status_code=204)
def delete_columna(
    col_id: int,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
):
    col = db.query(KanbanColumn).filter(
        KanbanColumn.id == col_id,
        KanbanColumn.board_id == board_id,
    ).first()
    if not col:
        raise HTTPException(status_code=404, detail="Columna no encontrada")
    count = db.query(WarrantyCard).filter(
        WarrantyCard.board_id == board_id,
        WarrantyCard.status == col.key,
        WarrantyCard.deleted_at.is_(None),
    ).count()
    if count > 0:
        raise HTTPException(status_code=400, detail=f"No se puede eliminar: hay {count} tarjetas en esta columna")
    db.delete(col)
    db.commit()
    return None


@router.put("/columnas/reorder")
def reorder_columnas(
    data: ColumnReorder,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
):
    _get_board_or_404(board_id, db)
    for item in data.columns:
        col = db.query(KanbanColumn).filter(
            KanbanColumn.id == item["id"],
            KanbanColumn.board_id == board_id,
        ).first()
        if col:
            col.position = item["position"]
    db.commit()
    return {"ok": True}


# ==================== TAGS ====================

@router.get("/tags")
def get_tags(board_id: int = Path(...), db: Session = Depends(get_db)):
    _get_board_or_404(board_id, db)
    tags = db.query(Tag).filter(Tag.board_id == board_id).order_by(Tag.name).all()
    return [t.to_dict() for t in tags]


@router.post("/tags", status_code=201)
def create_tag(
    data: TagCreate,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
):
    _get_board_or_404(board_id, db)
    if db.query(Tag).filter(Tag.name == data.name, Tag.board_id == board_id).first():
        raise HTTPException(status_code=409, detail="Ya existe un tag con ese nombre en este tablero")
    tag_data = data.model_dump()
    tag_data["board_id"] = board_id
    tag = Tag(**tag_data)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag.to_dict()


@router.put("/tags/{tag_id}")
def update_tag(
    tag_id: int,
    data: TagUpdate,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
):
    tag = db.query(Tag).filter(Tag.id == tag_id, Tag.board_id == board_id).first()
    if not tag:
        raise HTTPException(status_code=404, detail="Tag no encontrado")
    upd = data.model_dump(exclude_unset=True)
    for k, v in upd.items():
        setattr(tag, k, v)
    db.commit()
    db.refresh(tag)
    return tag.to_dict()


@router.delete("/tags/{tag_id}", status_code=204)
def delete_tag(
    tag_id: int,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
):
    tag = db.query(Tag).filter(Tag.id == tag_id, Tag.board_id == board_id).first()
    if not tag:
        raise HTTPException(status_code=404, detail="Tag no encontrado")
    db.delete(tag)
    db.commit()
    return None


@router.post("/tarjetas/{tarjeta_id}/tags/{tag_id}", status_code=201)
def add_tag_to_tarjeta(
    tarjeta_id: int,
    tag_id: int,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
):
    t = db.query(WarrantyCard).filter(
        WarrantyCard.id == tarjeta_id,
        WarrantyCard.board_id == board_id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
    existing = db.execute(
        warranty_card_tags.select().where(
            warranty_card_tags.c.warranty_card_id == tarjeta_id,
            warranty_card_tags.c.tag_id == tag_id,
        )
    ).first()
    if existing:
        return {"ok": True}
    db.execute(insert(warranty_card_tags).values(warranty_card_id=tarjeta_id, tag_id=tag_id))
    db.commit()
    return {"ok": True}


@router.delete("/tarjetas/{tarjeta_id}/tags/{tag_id}", status_code=204)
def remove_tag_from_tarjeta(
    tarjeta_id: int,
    tag_id: int,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
):
    db.execute(
        delete(warranty_card_tags).where(
            warranty_card_tags.c.warranty_card_id == tarjeta_id,
            warranty_card_tags.c.tag_id == tag_id,
        )
    )
    db.commit()
    return None


@router.get("/tarjetas/{tarjeta_id}/tags")
def get_tarjeta_tags(
    tarjeta_id: int,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
):
    t = db.query(WarrantyCard).filter(
        WarrantyCard.id == tarjeta_id,
        WarrantyCard.board_id == board_id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
    tag_ids = db.execute(
        select(warranty_card_tags.c.tag_id).where(warranty_card_tags.c.warranty_card_id == tarjeta_id)
    ).scalars().all()
    tags = db.query(Tag).filter(Tag.id.in_(tag_ids)).all() if tag_ids else []
    return [t.to_dict() for t in tags]


# ==================== SUBTASKS ====================

@router.get("/tarjetas/{tarjeta_id}/subtasks")
def get_subtasks(
    tarjeta_id: int,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
):
    items = db.query(SubTask).filter(SubTask.tarjeta_id == tarjeta_id).order_by(SubTask.position).all()
    return [s.to_dict() for s in items]


@router.post("/tarjetas/{tarjeta_id}/subtasks", status_code=201)
def create_subtask(
    tarjeta_id: int,
    data: SubTaskCreate,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
):
    t = db.query(WarrantyCard).filter(
        WarrantyCard.id == tarjeta_id,
        WarrantyCard.board_id == board_id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
    max_pos = db.query(SubTask).filter(SubTask.tarjeta_id == tarjeta_id).count()
    st = SubTask(tarjeta_id=tarjeta_id, title=data.title, position=data.position or max_pos)
    db.add(st)
    db.commit()
    db.refresh(st)
    return st.to_dict()


@router.put("/subtasks/{subtask_id}")
def update_subtask(
    subtask_id: int,
    data: SubTaskUpdate,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
):
    st = db.query(SubTask).filter(SubTask.id == subtask_id).first()
    if not st:
        raise HTTPException(status_code=404, detail="Sub-tarea no encontrada")
    upd = data.model_dump(exclude_unset=True)
    for k, v in upd.items():
        setattr(st, k, v)
    if data.completed is True and not st.completed_at:
        st.completed_at = datetime.now(UTC)
    elif data.completed is False:
        st.completed_at = None
    st.completed = data.completed if data.completed is not None else st.completed
    db.commit()
    db.refresh(st)
    return st.to_dict()


@router.delete("/subtasks/{subtask_id}", status_code=204)
def delete_subtask(
    subtask_id: int,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
):
    st = db.query(SubTask).filter(SubTask.id == subtask_id).first()
    if not st:
        raise HTTPException(status_code=404, detail="Sub-tarea no encontrada")
    db.delete(st)
    db.commit()
    return None


# ==================== COMMENTS ====================

@router.get("/tarjetas/{tarjeta_id}/comments")
def get_comments(
    tarjeta_id: int,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
):
    items = db.query(Comment).filter(Comment.tarjeta_id == tarjeta_id).order_by(Comment.created_at.desc()).all()
    return [c.to_dict() for c in items]


@router.post("/tarjetas/{tarjeta_id}/comments", status_code=201)
def create_comment(
    tarjeta_id: int,
    data: CommentCreate,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    t = db.query(WarrantyCard).filter(
        WarrantyCard.id == tarjeta_id,
        WarrantyCard.board_id == board_id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
    comment = Comment(
        tarjeta_id=tarjeta_id,
        user_id=user.id if user else None,
        author_name=user.full_name if user else "Anónimo",
        content=data.content,
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return comment.to_dict()


@router.delete("/comments/{comment_id}", status_code=204)
def delete_comment(
    comment_id: int,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
):
    c = db.query(Comment).filter(Comment.id == comment_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Comentario no encontrado")
    db.delete(c)
    db.commit()
    return None


# ==================== NOTIFICACIONES ====================

@router.get("/notificaciones")
def get_notificaciones(
    board_id: int = Path(...),
    db: Session = Depends(get_db),
    unread_only: bool = Query(False),
    limit: int = Query(50),
    user: User = Depends(get_current_user),
):
    q = db.query(Notification).filter(Notification.user_id == user.id)
    if unread_only:
        q = q.filter(Notification.read.is_(False))
    items = q.order_by(Notification.created_at.desc()).limit(limit).all()
    unread_count = db.query(Notification).filter(
        Notification.user_id == user.id,
        Notification.read.is_(False),
    ).count()
    return {"notifications": [n.to_dict() for n in items], "unread_count": unread_count}


@router.put("/notificaciones/mark-read")
def mark_read(
    data: NotificationMarkRead,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
):
    db.query(Notification).filter(Notification.id.in_(data.ids)).update({"read": True}, synchronize_session=False)
    db.commit()
    return {"ok": True}


@router.put("/notificaciones/mark-all-read")
def mark_all_read(
    board_id: int = Path(...),
    db: Session = Depends(get_db),
):
    db.query(Notification).filter(Notification.read.is_(False)).update({"read": True}, synchronize_session=False)
    db.commit()
    return {"ok": True}


@router.delete("/notificaciones/{notif_id}", status_code=204)
def delete_notification(
    notif_id: int,
    board_id: int = Path(...),
    db: Session = Depends(get_db),
):
    n = db.query(Notification).filter(Notification.id == notif_id).first()
    if n:
        db.delete(n)
        db.commit()
    return None
