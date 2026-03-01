"""Servicio de notificaciones in-app."""

from sqlalchemy.orm import Session

from app.models.kanban import Notification
from app.models.warranty_card import WarrantyCard

ESTADO_LABELS = {
    "recibido":   "Recibido",
    "en_gestion": "En Gestión",
    "resuelto":   "Resuelto",
    "entregado":  "Entregado",
}


def crear_notificacion(
    db: Session,
    *,
    title: str,
    message: str,
    type: str = "info",
    user_id: int | None = None,
    tarjeta_id: int | None = None,
) -> Notification:
    notif = Notification(
        title=title,
        message=message,
        type=type,
        user_id=user_id,
        tarjeta_id=tarjeta_id,
    )
    db.add(notif)
    return notif


def notificar_cambio_estado(
    db: Session, tarjeta: WarrantyCard, old_status: str, new_status: str
) -> None:
    old_label = ESTADO_LABELS.get(old_status, old_status)
    new_label = ESTADO_LABELS.get(new_status, new_status)
    nombre = tarjeta.client_name or "Cliente"

    if tarjeta.assigned_to:
        crear_notificacion(
            db,
            title="Cambio de estado",
            message=f"Garantía de {nombre} movida de '{old_label}' a '{new_label}'",
            type="info",
            user_id=tarjeta.assigned_to,
            tarjeta_id=tarjeta.id,
        )

    if new_status == "resuelto":
        crear_notificacion(
            db,
            title="¡Garantía resuelta!",
            message=f"La garantía de {nombre} ha sido resuelta por el proveedor",
            type="success",
            tarjeta_id=tarjeta.id,
        )

    if new_status == "entregado":
        crear_notificacion(
            db,
            title="Garantía entregada",
            message=f"El equipo de {nombre} ha sido entregado exitosamente",
            type="success",
            tarjeta_id=tarjeta.id,
        )


def generar_url_whatsapp(telefono: str, mensaje: str) -> str | None:
    if not telefono:
        return None
    digits = "".join(c for c in telefono if c.isdigit())
    if len(digits) == 10 and digits.startswith("3"):
        digits = "57" + digits
    if len(digits) < 10:
        return None
    import urllib.parse
    return f"https://wa.me/{digits}?text={urllib.parse.quote(mensaje)}"
