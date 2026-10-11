"""Initial project, university and audit tables plus SQLite FTS5.

Revision ID: 0001_initial
Revises:
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint

from app.models import Base


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    app_tables = set(Base.metadata.tables)
    existing = app_tables.intersection(inspector.get_table_names())
    if existing:
        if existing != app_tables:
            raise RuntimeError("已有项目表不完整，已停止迁移以保护现有数据")
        for name, table in Base.metadata.tables.items():
            actual_columns = {
                column["name"]: (str(column["type"]), column["nullable"], column["primary_key"])
                for column in inspector.get_columns(name)
            }
            expected_columns = {
                column.name: (str(column.type), column.nullable, column.primary_key)
                for column in table.columns
            }
            # This pre-Alembic bootstrap may contain either the original columns
            # or a full schema made by the current model before stamping.
            if name == "programs":
                later_columns = {
                    "data_origin", "source_id", "source_sha256", "source_retrieved_at",
                    "reviewed_at", "academic_standard", "language_details_json",
                }
                legacy_columns = {
                    key: value for key, value in expected_columns.items() if key not in later_columns
                }
                valid_columns = (expected_columns, legacy_columns)
            else:
                valid_columns = (expected_columns,)
            if actual_columns not in valid_columns:
                raise RuntimeError(f"已有项目表 {name} 的字段与初始版本不一致，已停止迁移")
            actual_unique = {
                tuple(sorted(constraint["column_names"]))
                for constraint in inspector.get_unique_constraints(name)
            }
            expected_unique = {
                tuple(sorted(column.name for column in constraint.columns))
                for constraint in table.constraints if isinstance(constraint, UniqueConstraint)
            }
            if actual_unique != expected_unique:
                raise RuntimeError(f"已有项目表 {name} 的唯一约束与初始版本不一致，已停止迁移")
            actual_fks = {
                (tuple(foreign_key["constrained_columns"]), foreign_key["referred_table"],
                 tuple(foreign_key["referred_columns"]))
                for foreign_key in inspector.get_foreign_keys(name)
            }
            expected_fks = {
                (tuple(column.name for column in constraint.columns),
                 next(iter(constraint.elements)).column.table.name,
                 tuple(element.column.name for element in constraint.elements))
                for constraint in table.constraints if isinstance(constraint, ForeignKeyConstraint)
            }
            if actual_fks != expected_fks:
                raise RuntimeError(f"已有项目表 {name} 的外键与初始版本不一致，已停止迁移")
        from app.database import FTS_DDL

        for statement in FTS_DDL:
            op.execute(statement)
        op.execute("INSERT INTO program_fts(program_fts) VALUES ('rebuild')")
        return

    op.create_table(
        "universities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("country", sa.String(100), nullable=False),
        sa.Column("ranking", sa.Integer()),
        sa.Column("logo_url", sa.String(500)),
        sa.UniqueConstraint("name", "country"),
    )
    op.create_table(
        "programs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("university_id", sa.Integer(), sa.ForeignKey("universities.id"), nullable=False),
        sa.Column("university", sa.String(200), nullable=False),
        sa.Column("country", sa.String(100), nullable=False),
        sa.Column("city", sa.String(100)),
        sa.Column("program_name", sa.String(250), nullable=False),
        sa.Column("degree", sa.String(50), nullable=False),
        sa.Column("discipline", sa.String(150), nullable=False),
        sa.Column("duration", sa.String(100)),
        sa.Column("tuition", sa.Float()),
        sa.Column("currency", sa.String(10)),
        sa.Column("intake", sa.String(100)),
        sa.Column("deadline", sa.Date()),
        sa.Column("gpa_min", sa.Float()),
        sa.Column("ielts_min", sa.Float()),
        sa.Column("toefl_min", sa.Float()),
        sa.Column("gre_required", sa.Boolean()),
        sa.Column("website", sa.String(500)),
        sa.Column("source_url", sa.String(500), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("tags", sa.Text()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "query_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("raw_query", sa.Text(), nullable=False),
        sa.Column("parsed_json", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip_hash", sa.String(64)),
    )
    op.create_table(
        "chat_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.String(100), nullable=False),
        sa.Column("user_message", sa.Text(), nullable=False),
        sa.Column("assistant_message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("program_id", sa.Integer(), sa.ForeignKey("programs.id"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding_json", sa.Text()),
    )
    from app.database import FTS_DDL

    for statement in FTS_DDL:
        op.execute(statement)
    op.execute("INSERT INTO program_fts(program_fts) VALUES ('rebuild')")


def downgrade() -> None:
    for trigger in ("programs_ai", "programs_ad", "programs_au"):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    op.execute("DROP TABLE IF EXISTS program_fts")
    for table in ("document_chunks", "chat_logs", "query_logs", "programs", "universities"):
        op.drop_table(table)
