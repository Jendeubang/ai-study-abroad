"""Record sync batches and initial programme candidates.

Revision ID: 0005_sync_runs_and_program_candidates
Revises: 0004_source_catalog
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_sync_runs_and_program_candidates"
down_revision = "0004_source_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    names = set(inspector.get_table_names())
    if "sync_runs" not in names:
        op.create_table(
            "sync_runs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("run_id", sa.String(36), nullable=False, unique=True),
            sa.Column("trigger", sa.String(30), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True)),
            sa.Column("eligible_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("checked_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("changed_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("pending_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("source_statuses_json", sa.Text(), nullable=False, server_default="[]"),
        )
    if "program_candidates" not in names:
        op.create_table(
            "program_candidates",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("programme_key", sa.String(100), nullable=False),
            sa.Column("source_ids_json", sa.Text(), nullable=False),
            sa.Column("source_snapshots_json", sa.Text(), nullable=False),
            sa.Column("metadata_json", sa.Text(), nullable=False),
            sa.Column("facts_json", sa.Text(), nullable=False),
            sa.Column("snapshot_sha256", sa.String(64), nullable=False),
            sa.Column("fingerprint", sa.String(64), nullable=False),
            sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("reviewed_at", sa.DateTime(timezone=True)),
            sa.Column("reviewer", sa.String(100)),
            sa.Column("review_note", sa.String(500)),
        )
        op.create_index("ix_program_candidates_status", "program_candidates", ["status"])
        op.create_index("ix_program_candidates_fingerprint", "program_candidates", ["fingerprint"], unique=True)
    actual_sync = {item["name"] for item in sa.inspect(connection).get_columns("sync_runs")}
    actual_candidates = {item["name"] for item in sa.inspect(connection).get_columns("program_candidates")}
    expected_sync = {
        "id", "run_id", "trigger", "started_at", "finished_at", "eligible_count",
        "checked_count", "changed_count", "failed_count", "skipped_count", "pending_count",
        "source_statuses_json",
    }
    expected_candidates = {
        "id", "programme_key", "source_ids_json", "source_snapshots_json", "metadata_json", "facts_json",
        "snapshot_sha256", "fingerprint", "retrieved_at", "status", "created_at",
        "reviewed_at", "reviewer", "review_note",
    }
    if actual_sync != expected_sync or actual_candidates != expected_candidates:
        raise RuntimeError("同步记录表结构不一致，已停止迁移")
    source_check_columns = {item["name"] for item in sa.inspect(connection).get_columns("source_checks")}
    if "sync_run_id" not in source_check_columns:
        with op.batch_alter_table("source_checks") as batch:
            batch.add_column(sa.Column(
                "sync_run_id", sa.Integer(),
                sa.ForeignKey("sync_runs.id", name="fk_source_checks_sync_run_id_sync_runs"),
                nullable=True,
            ))


def downgrade() -> None:
    op.drop_column("source_checks", "sync_run_id")
    op.drop_index("ix_program_candidates_fingerprint", table_name="program_candidates")
    op.drop_index("ix_program_candidates_status", table_name="program_candidates")
    op.drop_table("program_candidates")
    op.drop_table("sync_runs")
