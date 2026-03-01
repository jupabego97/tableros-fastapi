"""Boards (tableros) CRUD — un tablero por proveedor de garantías."""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.board import Board
from app.models.kanban import KanbanColumn
from app.models.user import User
from app.models.warranty_card import WarrantyCard
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/api/boards", tags=["boards"])

DEFAULT_WARRANTY_COLUMNS = [
    {"key": "recibido",   "title": "Recibido",    "color": "#0369a1", "icon": "fas fa-inbox",        "position": 0, "is_done_column": False},
    {"key": "en_gestion", "title": "En gestión",  "color": "#92400e", "icon": "fas fa-cogs",          "position": 1, "is_done_column": False},
    {"key": "resuelto",   "title": "Resuelto",    "color": "#5b21b6", "icon": "fas fa-check-square",  "position": 2, "is_done_column": False},
    {"key": "entregado",  "title": "Entregado",   "color": "#065f46", "icon": "fas fa-handshake",     "position": 3, "is_done_column": True},
]


class BoardCreate(BaseModel):
    name: str
    color: str = "#0369a1"
    icon: str = "📦"
    description: str | None = None


class BoardUpdate(BaseModel):
    name: str | None = None
    color: str | None = None
    icon: str | None = None
    description: str | None = None


def _board_with_count(board: Board, db: Session) -> dict:
    count = db.scalar(
        select(func.count(WarrantyCard.id)).where(
            WarrantyCard.board_id == board.id,
            WarrantyCard.deleted_at.is_(None),
        )
    ) or 0
    d = board.to_dict()
    d["card_count"] = count
    return d


@router.get("")
def list_boards(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    boards = db.query(Board).order_by(Board.created_at).all()
    return [_board_with_count(b, db) for b in boards]


@router.post("", status_code=201)
def create_board(
    data: BoardCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not data.name or not data.name.strip():
        raise HTTPException(status_code=400, detail={"code": "invalid_name", "message": "El nombre del tablero es obligatorio"})

    board = Board(
        name=data.name.strip(),
        color=data.color,
        icon=data.icon,
        description=data.description,
        created_by=current_user.id,
    )
    db.add(board)
    db.flush()  # get board.id before commit

    # Seed default columns
    for col_data in DEFAULT_WARRANTY_COLUMNS:
        col = KanbanColumn(
            board_id=board.id,
            key=col_data["key"],
            title=col_data["title"],
            color=col_data["color"],
            icon=col_data["icon"],
            position=col_data["position"],
            is_done_column=col_data["is_done_column"],
        )
        db.add(col)

    db.commit()
    db.refresh(board)
    d = board.to_dict()
    d["card_count"] = 0
    return d


@router.get("/{board_id}")
def get_board(
    board_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    board = db.query(Board).filter(Board.id == board_id).first()
    if not board:
        raise HTTPException(status_code=404, detail={"code": "board_not_found", "message": "Tablero no encontrado"})
    return _board_with_count(board, db)


@router.put("/{board_id}")
def update_board(
    board_id: int,
    data: BoardUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    board = db.query(Board).filter(Board.id == board_id).first()
    if not board:
        raise HTTPException(status_code=404, detail={"code": "board_not_found", "message": "Tablero no encontrado"})

    if data.name is not None:
        if not data.name.strip():
            raise HTTPException(status_code=400, detail={"code": "invalid_name", "message": "El nombre no puede estar vacío"})
        board.name = data.name.strip()
    if data.color is not None:
        board.color = data.color
    if data.icon is not None:
        board.icon = data.icon
    if data.description is not None:
        board.description = data.description

    board.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(board)
    return _board_with_count(board, db)


@router.delete("/{board_id}", status_code=204)
def delete_board(
    board_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    board = db.query(Board).filter(Board.id == board_id).first()
    if not board:
        raise HTTPException(status_code=404, detail={"code": "board_not_found", "message": "Tablero no encontrado"})

    card_count = db.scalar(
        select(func.count(WarrantyCard.id)).where(
            WarrantyCard.board_id == board_id,
            WarrantyCard.deleted_at.is_(None),
        )
    ) or 0
    if card_count > 0:
        raise HTTPException(
            status_code=409,
            detail={"code": "board_has_cards", "message": f"El tablero tiene {card_count} tarjeta(s). Eliminalas primero."},
        )

    db.delete(board)
    db.commit()
