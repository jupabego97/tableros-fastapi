"""initial warranty boards schema

Revision ID: 0001
Revises:
Create Date: 2026-02-23 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- users ---
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("hashed_password", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("avatar_color", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_login", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_role", "users", ["role"])

    # --- user_preferences ---
    op.create_table(
        "user_preferences",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("preferences_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_user_preferences_user_id", "user_preferences", ["user_id"])

    # --- boards ---
    op.create_table(
        "boards",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("color", sa.Text(), nullable=False),
        sa.Column("icon", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_boards_name", "boards", ["name"])
    op.create_index("ix_boards_created_by", "boards", ["created_by"])

    # --- kanban_columns ---
    op.create_table(
        "kanban_columns",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("board_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("color", sa.Text(), nullable=False),
        sa.Column("icon", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("wip_limit", sa.Integer(), nullable=True),
        sa.Column("is_done_column", sa.Boolean(), nullable=False),
        sa.Column("sla_hours", sa.Integer(), nullable=True),
        sa.Column("required_fields", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["board_id"], ["boards.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("board_id", "key", name="uq_kanban_columns_board_key"),
    )
    op.create_index("ix_kanban_columns_board_id", "kanban_columns", ["board_id"])
    op.create_index("ix_kanban_columns_key", "kanban_columns", ["key"])

    # --- tags ---
    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("board_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("color", sa.Text(), nullable=False),
        sa.Column("icon", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["board_id"], ["boards.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("board_id", "name", name="uq_tags_board_name"),
    )
    op.create_index("ix_tags_board_id", "tags", ["board_id"])
    op.create_index("ix_tags_name", "tags", ["name"])

    # --- warranty_cards ---
    op.create_table(
        "warranty_cards",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("board_id", sa.Integer(), nullable=False),
        sa.Column("client_name", sa.Text(), nullable=True),
        sa.Column("whatsapp_number", sa.Text(), nullable=True),
        sa.Column("product", sa.Text(), nullable=True),
        sa.Column("problem", sa.Text(), nullable=True),
        sa.Column("purchase_date", sa.DateTime(), nullable=True),
        sa.Column("invoice_number", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("start_date", sa.DateTime(), nullable=False),
        sa.Column("due_date", sa.DateTime(), nullable=False),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("recibido_date", sa.DateTime(), nullable=False),
        sa.Column("en_gestion_date", sa.DateTime(), nullable=True),
        sa.Column("resuelto_date", sa.DateTime(), nullable=True),
        sa.Column("entregado_date", sa.DateTime(), nullable=True),
        sa.Column("technical_notes", sa.Text(), nullable=True),
        sa.Column("priority", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("assigned_to", sa.Integer(), nullable=True),
        sa.Column("assigned_name", sa.Text(), nullable=True),
        sa.Column("estimated_cost", sa.Float(), nullable=True),
        sa.Column("final_cost", sa.Float(), nullable=True),
        sa.Column("cost_notes", sa.Text(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("blocked_at", sa.DateTime(), nullable=True),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("blocked_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["assigned_to"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["blocked_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["board_id"], ["boards.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_warranty_cards_board_deleted_status_pos", "warranty_cards", ["board_id", "deleted_at", "status", "position"])
    op.create_index("ix_warranty_cards_board_deleted_assigned", "warranty_cards", ["board_id", "deleted_at", "assigned_to"])
    op.create_index("ix_warranty_cards_deleted_priority", "warranty_cards", ["deleted_at", "priority"])
    op.create_index("ix_warranty_cards_board_id", "warranty_cards", ["board_id"])
    op.create_index("ix_warranty_cards_client_name", "warranty_cards", ["client_name"])
    op.create_index("ix_warranty_cards_whatsapp_number", "warranty_cards", ["whatsapp_number"])
    op.create_index("ix_warranty_cards_invoice_number", "warranty_cards", ["invoice_number"])
    op.create_index("ix_warranty_cards_status", "warranty_cards", ["status"])
    op.create_index("ix_warranty_cards_start_date", "warranty_cards", ["start_date"])
    op.create_index("ix_warranty_cards_due_date", "warranty_cards", ["due_date"])
    op.create_index("ix_warranty_cards_priority", "warranty_cards", ["priority"])
    op.create_index("ix_warranty_cards_position", "warranty_cards", ["position"])
    op.create_index("ix_warranty_cards_assigned_to", "warranty_cards", ["assigned_to"])
    op.create_index("ix_warranty_cards_deleted_at", "warranty_cards", ["deleted_at"])

    # --- warranty_card_tags (M:N) ---
    op.create_table(
        "warranty_card_tags",
        sa.Column("warranty_card_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["warranty_card_id"], ["warranty_cards.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("warranty_card_id", "tag_id"),
    )
    op.create_index("ix_warranty_card_tags_tag_card", "warranty_card_tags", ["tag_id", "warranty_card_id"])

    # --- status_history ---
    op.create_table(
        "status_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tarjeta_id", sa.Integer(), nullable=False),
        sa.Column("old_status", sa.Text(), nullable=True),
        sa.Column("new_status", sa.Text(), nullable=False),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
        sa.Column("changed_by", sa.Integer(), nullable=True),
        sa.Column("changed_by_name", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["changed_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tarjeta_id"], ["warranty_cards.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_status_history_tarjeta_id", "status_history", ["tarjeta_id"])
    op.create_index("ix_status_history_changed_at", "status_history", ["changed_at"])
    op.create_index("ix_status_history_changed_tarjeta", "status_history", ["changed_at", "tarjeta_id"])

    # --- warranty_card_media ---
    op.create_table(
        "warranty_card_media",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tarjeta_id", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("thumb_url", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("is_cover", sa.Boolean(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["tarjeta_id"], ["warranty_cards.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_warranty_card_media_tarjeta_id", "warranty_card_media", ["tarjeta_id"])
    op.create_index("ix_warranty_card_media_created_at", "warranty_card_media", ["created_at"])
    op.create_index("ix_warranty_card_media_deleted_at", "warranty_card_media", ["deleted_at"])
    op.create_index("ix_warranty_card_media_tarjeta_position_cover", "warranty_card_media", ["tarjeta_id", "position", "is_cover"])

    # --- subtasks ---
    op.create_table(
        "subtasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tarjeta_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["tarjeta_id"], ["warranty_cards.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_subtasks_tarjeta_id", "subtasks", ["tarjeta_id"])

    # --- comments ---
    op.create_table(
        "comments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tarjeta_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("author_name", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tarjeta_id"], ["warranty_cards.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_comments_tarjeta_id", "comments", ["tarjeta_id"])
    op.create_index("ix_comments_user_id", "comments", ["user_id"])

    # --- notifications ---
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("tarjeta_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("read", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tarjeta_id"], ["warranty_cards.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_index("ix_notifications_read", "notifications", ["read"])
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])

    # --- card_templates ---
    op.create_table(
        "card_templates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("board_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("problem_template", sa.Text(), nullable=True),
        sa.Column("default_priority", sa.Text(), nullable=False),
        sa.Column("default_notes", sa.Text(), nullable=True),
        sa.Column("estimated_hours", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["board_id"], ["boards.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_card_templates_board_id", "card_templates", ["board_id"])


def downgrade() -> None:
    op.drop_table("card_templates")
    op.drop_table("notifications")
    op.drop_table("comments")
    op.drop_table("subtasks")
    op.drop_table("warranty_card_media")
    op.drop_table("status_history")
    op.drop_table("warranty_card_tags")
    op.drop_table("warranty_cards")
    op.drop_table("tags")
    op.drop_table("kanban_columns")
    op.drop_table("boards")
    op.drop_table("user_preferences")
    op.drop_table("users")
