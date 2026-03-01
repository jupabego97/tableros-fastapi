from datetime import UTC, datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, Text

from app.core.database import Base


class WarrantyCard(Base):
    __tablename__ = "warranty_cards"
    __table_args__ = (
        Index("ix_warranty_cards_board_deleted_status_pos", "board_id", "deleted_at", "status", "position"),
        Index("ix_warranty_cards_board_deleted_assigned", "board_id", "deleted_at", "assigned_to"),
        Index("ix_warranty_cards_deleted_priority", "deleted_at", "priority"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    board_id = Column(Integer, ForeignKey("boards.id", ondelete="CASCADE"), nullable=False, index=True)
    client_name = Column(Text, nullable=True, default="Cliente", index=True)
    whatsapp_number = Column(Text, nullable=True, default="", index=True)
    product = Column(Text, nullable=True)
    problem = Column(Text, nullable=True, default="Sin descripción")
    purchase_date = Column(DateTime, nullable=True)
    invoice_number = Column(Text, nullable=True, index=True)
    status = Column(Text, nullable=False, index=True)
    start_date = Column(DateTime, nullable=False, index=True)
    due_date = Column(DateTime, nullable=False, index=True)
    image_url = Column(Text, nullable=True)

    # Date tracking per column
    recibido_date = Column(DateTime, nullable=False)
    en_gestion_date = Column(DateTime, nullable=True)
    resuelto_date = Column(DateTime, nullable=True)
    entregado_date = Column(DateTime, nullable=True)

    technical_notes = Column(Text, nullable=True)
    priority = Column(Text, nullable=False, default="media", index=True)  # alta, media, baja
    position = Column(Integer, nullable=False, default=0, index=True)

    # Technician assignment
    assigned_to = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    assigned_name = Column(Text, nullable=True)

    # Costs
    estimated_cost = Column(Float, nullable=True)
    final_cost = Column(Float, nullable=True)
    cost_notes = Column(Text, nullable=True)

    # Soft delete
    deleted_at = Column(DateTime, nullable=True, index=True)

    # Blocked cards
    blocked_at = Column(DateTime, nullable=True)
    blocked_reason = Column(Text, nullable=True)
    blocked_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    def to_dict(self, include_image: bool = True) -> dict:
        d = {
            "id": self.id,
            "board_id": self.board_id,
            "nombre_cliente": self.client_name,
            "producto": self.product,
            "problema": self.problem,
            "whatsapp": self.whatsapp_number,
            "numero_factura": self.invoice_number,
            "fecha_compra": self.purchase_date.strftime("%Y-%m-%d") if self.purchase_date else None,
            "fecha_inicio": self.start_date.strftime("%Y-%m-%d %H:%M:%S") if self.start_date else None,
            "fecha_limite": self.due_date.strftime("%Y-%m-%d") if self.due_date else None,
            "columna": self.status,
            "fecha_recibido": self.recibido_date.strftime("%Y-%m-%d %H:%M:%S") if self.recibido_date else None,
            "fecha_en_gestion": self.en_gestion_date.strftime("%Y-%m-%d %H:%M:%S") if self.en_gestion_date else None,
            "fecha_resuelto": self.resuelto_date.strftime("%Y-%m-%d %H:%M:%S") if self.resuelto_date else None,
            "fecha_entregado": self.entregado_date.strftime("%Y-%m-%d %H:%M:%S") if self.entregado_date else None,
            "notas_tecnicas": self.technical_notes,
            "prioridad": self.priority,
            "posicion": self.position,
            "asignado_a": self.assigned_to,
            "asignado_nombre": self.assigned_name,
            "costo_estimado": self.estimated_cost,
            "costo_final": self.final_cost,
            "notas_costo": self.cost_notes,
            "eliminado": self.deleted_at is not None,
            "bloqueada": self.blocked_at is not None,
            "motivo_bloqueo": self.blocked_reason,
            "bloqueada_por": self.blocked_by,
            "fecha_bloqueo": self.blocked_at.strftime("%Y-%m-%d %H:%M:%S") if self.blocked_at else None,
        }
        d["imagen_url"] = self.image_url if include_image else None
        return d


class StatusHistory(Base):
    __tablename__ = "status_history"
    __table_args__ = (
        Index("ix_status_history_changed_tarjeta", "changed_at", "tarjeta_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tarjeta_id = Column(Integer, ForeignKey("warranty_cards.id", ondelete="CASCADE"), nullable=False, index=True)
    old_status = Column(Text, nullable=True)
    new_status = Column(Text, nullable=False)
    changed_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC), index=True)
    changed_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    changed_by_name = Column(Text, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tarjeta_id": self.tarjeta_id,
            "old_status": self.old_status,
            "new_status": self.new_status,
            "changed_at": self.changed_at.strftime("%Y-%m-%d %H:%M:%S") if self.changed_at else None,
            "changed_by": self.changed_by,
            "changed_by_name": self.changed_by_name,
        }


class WarrantyCardMedia(Base):
    __tablename__ = "warranty_card_media"
    __table_args__ = (
        Index("ix_warranty_card_media_tarjeta_position_cover", "tarjeta_id", "position", "is_cover"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tarjeta_id = Column(Integer, ForeignKey("warranty_cards.id", ondelete="CASCADE"), nullable=False, index=True)
    storage_key = Column(Text, nullable=True)
    url = Column(Text, nullable=False)
    thumb_url = Column(Text, nullable=True)
    position = Column(Integer, nullable=False, default=0)
    is_cover = Column(Boolean, nullable=False, default=False)
    mime_type = Column(Text, nullable=True)
    size_bytes = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC), index=True)
    deleted_at = Column(DateTime, nullable=True, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tarjeta_id": self.tarjeta_id,
            "storage_key": self.storage_key,
            "url": self.url,
            "thumb_url": self.thumb_url,
            "position": self.position,
            "is_cover": self.is_cover,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None,
            "deleted_at": self.deleted_at.strftime("%Y-%m-%d %H:%M:%S") if self.deleted_at else None,
        }
