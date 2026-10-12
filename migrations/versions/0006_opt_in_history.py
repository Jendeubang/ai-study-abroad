"""Add anonymous, opt-in ownership for query and chat history.

Revision ID: 0006_opt_in_history
Revises: 0005_sync_runs_and_program_candidates
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_opt_in_history"
down_revision = "0005_sync_runs_and_program_candidates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "owner_hash" not in {column["name"] for column in inspector.get_columns("query_logs")}:
        op.add_column("query_logs", sa.Column("owner_hash", sa.String(64), nullable=True))
    if "ix_query_logs_owner_hash" not in {index["name"] for index in sa.inspect(connection).get_indexes("query_logs")}:
        op.create_index("ix_query_logs_owner_hash", "query_logs", ["owner_hash"])
    if "history_consents" not in sa.inspect(connection).get_table_names():
        op.create_table(
            "history_consents",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade() -> None:
    op.drop_table("history_consents")
    op.drop_index("ix_query_logs_owner_hash", table_name="query_logs")
    op.drop_column("query_logs", "owner_hash")
