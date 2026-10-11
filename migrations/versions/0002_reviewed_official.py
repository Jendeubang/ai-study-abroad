"""Keep reviewed official requirements distinct from demonstration records.

Revision ID: 0002_reviewed_official
Revises: 0001_initial
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_reviewed_official"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


NEW_COLUMNS = (
    "data_origin", "source_id", "source_sha256", "source_retrieved_at",
    "reviewed_at", "academic_standard", "language_details_json",
)


def upgrade() -> None:
    connection = op.get_bind()
    present = {column["name"] for column in sa.inspect(connection).get_columns("programs")}
    missing = set(NEW_COLUMNS) - present
    if missing and missing != set(NEW_COLUMNS):
        raise RuntimeError("官网数据字段只迁移了一部分，已停止以保护现有数据")
    if missing:
        op.add_column("programs", sa.Column(
            "data_origin", sa.String(30), nullable=False, server_default="demo",
        ))
        op.add_column("programs", sa.Column("source_id", sa.String(80)))
        op.add_column("programs", sa.Column("source_sha256", sa.String(64)))
        op.add_column("programs", sa.Column("source_retrieved_at", sa.DateTime(timezone=True)))
        op.add_column("programs", sa.Column("reviewed_at", sa.DateTime(timezone=True)))
        op.add_column("programs", sa.Column("academic_standard", sa.String(120)))
        op.add_column("programs", sa.Column("language_details_json", sa.Text()))
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_programs_source_id ON programs (source_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_programs_source_id")
    with op.batch_alter_table("programs") as batch:
        for name in reversed(NEW_COLUMNS):
            batch.drop_column(name)
