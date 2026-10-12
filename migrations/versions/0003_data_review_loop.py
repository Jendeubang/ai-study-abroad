"""Store field evidence, application rounds, source permissions and review decisions.

Revision ID: 0003_data_review_loop
Revises: 0002_reviewed_official
"""

from alembic import op
import sqlalchemy as sa

from app.models import Base


revision = "0003_data_review_loop"
down_revision = "0002_reviewed_official"
branch_labels = None
depends_on = None

TABLES = (
    "source_permissions", "evidence_facts", "application_rounds",
    "source_checks", "review_candidates", "review_decisions",
)


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    existing = set(TABLES).intersection(inspector.get_table_names())
    if existing:
        if existing != set(TABLES):
            raise RuntimeError("数据核对表只创建了一部分，已停止迁移")
        for name in TABLES:
            expected = {column.name for column in Base.metadata.tables[name].columns}
            actual = {column["name"] for column in inspector.get_columns(name)}
            if actual != expected:
                raise RuntimeError(f"数据核对表 {name} 字段不一致，已停止迁移")
        return

    op.create_table(
        "source_permissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("host", sa.String(200), nullable=False, unique=True),
        sa.Column("terms_url", sa.String(500), nullable=False),
        sa.Column("use_scope", sa.String(50), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewer", sa.String(100), nullable=False),
        sa.Column("note", sa.String(500), nullable=False),
    )
    op.create_table(
        "evidence_facts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("program_id", sa.Integer(), sa.ForeignKey("programs.id"), nullable=False),
        sa.Column("field_name", sa.String(50), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("scope_json", sa.Text(), nullable=False),
        sa.Column("evidence_excerpt", sa.String(500), nullable=False),
        sa.Column("source_url", sa.String(500), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewer", sa.String(100), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_evidence_facts_program_active", "evidence_facts", ["program_id", "active"])
    op.create_table(
        "application_rounds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("program_id", sa.Integer(), sa.ForeignKey("programs.id"), nullable=False),
        sa.Column("intake", sa.String(100), nullable=False),
        sa.Column("round_label", sa.String(100), nullable=False),
        sa.Column("opens_on", sa.Date()),
        sa.Column("deadline_on", sa.Date()),
        sa.Column("evidence_excerpt", sa.String(500), nullable=False),
        sa.Column("source_url", sa.String(500), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewer", sa.String(100), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_application_rounds_program_active", "application_rounds", ["program_id", "active"])
    op.create_table(
        "source_checks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.String(80), nullable=False),
        sa.Column("program_id", sa.Integer(), sa.ForeignKey("programs.id")),
        sa.Column("source_url", sa.String(500), nullable=False),
        sa.Column("source_sha256", sa.String(64)),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("message", sa.String(300)),
    )
    op.create_index("ix_source_checks_source_time", "source_checks", ["source_id", "checked_at"])
    op.create_table(
        "review_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("program_id", sa.Integer(), sa.ForeignKey("programs.id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("field_name", sa.String(50)),
        sa.Column("scope_json", sa.Text(), nullable=False),
        sa.Column("new_value_json", sa.Text(), nullable=False),
        sa.Column("old_value_json", sa.Text()),
        sa.Column("evidence_excerpt", sa.String(500), nullable=False),
        sa.Column("source_url", sa.String(500), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewer", sa.String(100)),
        sa.Column("review_note", sa.String(500)),
    )
    op.create_index("ix_review_candidates_status", "review_candidates", ["status"])
    op.create_index("ix_review_candidates_fingerprint", "review_candidates", ["fingerprint"], unique=True)
    op.create_table(
        "review_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("candidate_id", sa.Integer(), sa.ForeignKey("review_candidates.id"), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("reviewer", sa.String(100), nullable=False),
        sa.Column("note", sa.String(500), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    for name in reversed(TABLES):
        op.drop_table(name)
