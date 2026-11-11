"""Teachers assigned to classrooms (per-room alerts).

Revision ID: 20261105_0017
Revises: 20261105_0016

classroom_teachers links a teacher (role staff) to the classrooms they teach. A teacher
sees and is pushed only those classrooms' alerts; school admins and counselors still see
the whole school. The composite foreign key keeps each row's school equal to the
classroom's school. No rows are created: existing teachers see no alerts until an
administrator assigns their rooms.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261105_0017"
down_revision = "20261105_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "classroom_teachers",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("classroom_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("school_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["classroom_id", "school_id"],
            ["classrooms.id", "classrooms.school_id"],
            name="fk_classroom_teachers_classroom_school",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_classroom_teachers_classroom_id", "classroom_teachers", ["classroom_id"])
    op.create_index("ix_classroom_teachers_school_id", "classroom_teachers", ["school_id"])


def downgrade() -> None:
    op.drop_index("ix_classroom_teachers_school_id", table_name="classroom_teachers")
    op.drop_index("ix_classroom_teachers_classroom_id", table_name="classroom_teachers")
    op.drop_table("classroom_teachers")
