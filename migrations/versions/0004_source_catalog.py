"""Add a persistent exact-page source catalogue, separate from programmes.

Revision ID: 0004_source_catalog
Revises: 0003_data_review_loop
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_source_catalog"
down_revision = "0003_data_review_loop"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    names = set(sa.inspect(connection).get_table_names())
    if "source_definitions" in names:
        expected = {
            "source_id", "programme_key", "host", "url", "university", "country",
            "city", "program_name", "degree", "discipline", "parser_key",
            "allowed_fields_json", "enabled",
        }
        actual = {column["name"] for column in sa.inspect(connection).get_columns("source_definitions")}
        if actual != expected:
            raise RuntimeError("来源目录表结构不一致，已停止迁移以保护现有数据")
        return
    op.create_table(
        "source_definitions",
        sa.Column("source_id", sa.String(100), primary_key=True),
        sa.Column("programme_key", sa.String(100), nullable=False),
        sa.Column("host", sa.String(200), nullable=False),
        sa.Column("url", sa.String(500), nullable=False, unique=True),
        sa.Column("university", sa.String(200), nullable=False),
        sa.Column("country", sa.String(100), nullable=False),
        sa.Column("city", sa.String(100)),
        sa.Column("program_name", sa.String(250), nullable=False),
        sa.Column("degree", sa.String(50), nullable=False),
        sa.Column("discipline", sa.String(150), nullable=False),
        sa.Column("parser_key", sa.String(50), nullable=False),
        sa.Column("allowed_fields_json", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_source_definitions_host", "source_definitions", ["host"])


def downgrade() -> None:
    op.drop_index("ix_source_definitions_host", table_name="source_definitions")
    op.drop_table("source_definitions")
