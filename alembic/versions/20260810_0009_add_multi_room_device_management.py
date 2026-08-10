"""Add authoritative schools, classrooms, and current device assignments.

Revision ID: 20260810_0009
Revises: 20260804_0008
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260810_0009"
down_revision = "20260804_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "schools",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute("CREATE UNIQUE INDEX uq_schools_normalized_name ON schools (lower(btrim(name)))")

    op.create_table(
        "classrooms",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("school_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["school_id"],
            ["schools.id"],
            name="fk_classrooms_school_id_schools",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "school_id", name="uq_classrooms_id_school_id"),
    )
    op.create_index("ix_classrooms_school_id", "classrooms", ["school_id"], unique=False)
    op.execute(
        "CREATE UNIQUE INDEX uq_classrooms_school_normalized_name "
        "ON classrooms (school_id, lower(btrim(name)))"
    )

    op.add_column(
        "users",
        sa.Column("school_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "is_super_admin",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.create_index("ix_users_school_id", "users", ["school_id"], unique=False)
    op.create_foreign_key(
        "fk_users_school_id_schools",
        "users",
        "schools",
        ["school_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.add_column(
        "edge_devices",
        sa.Column("school_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "edge_devices",
        sa.Column("classroom_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "edge_devices",
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "edge_devices",
        sa.Column("key_rotated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.alter_column("edge_devices", "classroom_name", existing_type=sa.String(200), nullable=True)

    op.add_column(
        "alerts",
        sa.Column("classroom_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "alerts",
        sa.Column("school_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "reports",
        sa.Column("school_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "audit_logs",
        sa.Column("school_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    # Convert only deterministic pre-existing name assignments. Unknown/null schools remain
    # unassigned instead of fabricating ownership.
    op.execute(
        """
        INSERT INTO schools (id, name)
        SELECT gen_random_uuid(), min(btrim(legacy.school_name))
        FROM (
            SELECT school_name FROM edge_devices
            UNION ALL
            SELECT school_name_snapshot AS school_name FROM alerts
        ) AS legacy
        WHERE legacy.school_name IS NOT NULL AND btrim(legacy.school_name) <> ''
        GROUP BY lower(btrim(legacy.school_name))
        """
    )
    op.execute(
        """
        INSERT INTO classrooms (id, school_id, name)
        SELECT gen_random_uuid(), schools.id, min(btrim(edge_devices.classroom_name))
        FROM edge_devices
        JOIN schools ON lower(btrim(schools.name)) = lower(btrim(edge_devices.school_name))
        WHERE edge_devices.classroom_name IS NOT NULL
          AND btrim(edge_devices.classroom_name) <> ''
        GROUP BY schools.id, lower(btrim(edge_devices.classroom_name))
        """
    )
    op.execute(
        """
        UPDATE edge_devices
        SET school_id = schools.id
        FROM schools
        WHERE edge_devices.school_name IS NOT NULL
          AND btrim(edge_devices.school_name) <> ''
          AND lower(btrim(schools.name)) = lower(btrim(edge_devices.school_name))
        """
    )
    op.execute(
        """
        UPDATE edge_devices
        SET classroom_id = classrooms.id,
            assigned_at = COALESCE(edge_devices.updated_at, edge_devices.created_at, now())
        FROM classrooms
        WHERE edge_devices.school_id = classrooms.school_id
          AND edge_devices.classroom_name IS NOT NULL
          AND btrim(edge_devices.classroom_name) <> ''
          AND lower(btrim(classrooms.name)) = lower(btrim(edge_devices.classroom_name))
        """
    )
    op.execute(
        """
        UPDATE alerts
        SET school_id = schools.id
        FROM schools
        WHERE alerts.school_name_snapshot IS NOT NULL
          AND btrim(alerts.school_name_snapshot) <> ''
          AND lower(btrim(schools.name)) = lower(btrim(alerts.school_name_snapshot))
        """
    )
    op.execute(
        """
        UPDATE alerts
        SET classroom_id = classrooms.id
        FROM classrooms
        WHERE alerts.school_id = classrooms.school_id
          AND alerts.classroom_name_snapshot IS NOT NULL
          AND btrim(alerts.classroom_name_snapshot) <> ''
          AND lower(btrim(classrooms.name)) = lower(btrim(alerts.classroom_name_snapshot))
        """
    )
    op.execute(
        """
        UPDATE users
        SET school_id = (SELECT id FROM schools LIMIT 1)
        WHERE school_id IS NULL AND (SELECT count(*) FROM schools) = 1
        """
    )
    # Existing administrators operated globally before tenant identity existed. Preserve that
    # behavior explicitly; newly created admins default to school-scoped.
    op.execute("UPDATE users SET is_super_admin = true WHERE role = 'admin'")
    op.execute(
        """
        UPDATE reports
        SET school_id = users.school_id
        FROM users
        WHERE reports.generated_by = users.id
          AND users.school_id IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE audit_logs
        SET school_id = users.school_id
        FROM users
        WHERE audit_logs.actor_user_id = users.id
          AND users.school_id IS NOT NULL
        """
    )

    op.create_index("ix_edge_devices_school_id", "edge_devices", ["school_id"], unique=False)
    op.create_index("ix_edge_devices_classroom_id", "edge_devices", ["classroom_id"], unique=False)
    op.create_index("ix_edge_devices_is_active", "edge_devices", ["is_active"], unique=False)
    op.create_foreign_key(
        "fk_edge_devices_school_id_schools",
        "edge_devices",
        "schools",
        ["school_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_edge_devices_classroom_school",
        "edge_devices",
        "classrooms",
        ["classroom_id", "school_id"],
        ["id", "school_id"],
        ondelete="RESTRICT",
    )

    op.create_index("ix_alerts_classroom_id", "alerts", ["classroom_id"], unique=False)
    op.create_index("ix_alerts_school_id", "alerts", ["school_id"], unique=False)
    op.create_foreign_key(
        "fk_alerts_school_id_schools",
        "alerts",
        "schools",
        ["school_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_alerts_classroom_school",
        "alerts",
        "classrooms",
        ["classroom_id", "school_id"],
        ["id", "school_id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_reports_school_id", "reports", ["school_id"], unique=False)
    op.create_foreign_key(
        "fk_reports_school_id_schools",
        "reports",
        "schools",
        ["school_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_audit_logs_school_id", "audit_logs", ["school_id"], unique=False)
    op.create_foreign_key(
        "fk_audit_logs_school_id_schools",
        "audit_logs",
        "schools",
        ["school_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_audit_logs_school_id_schools", "audit_logs", type_="foreignkey")
    op.drop_index("ix_audit_logs_school_id", table_name="audit_logs")
    op.drop_column("audit_logs", "school_id")
    op.drop_constraint("fk_reports_school_id_schools", "reports", type_="foreignkey")
    op.drop_index("ix_reports_school_id", table_name="reports")
    op.drop_column("reports", "school_id")
    op.drop_constraint("fk_alerts_classroom_school", "alerts", type_="foreignkey")
    op.drop_constraint("fk_alerts_school_id_schools", "alerts", type_="foreignkey")
    op.drop_index("ix_alerts_school_id", table_name="alerts")
    op.drop_index("ix_alerts_classroom_id", table_name="alerts")
    op.drop_column("alerts", "school_id")
    op.drop_column("alerts", "classroom_id")

    op.drop_constraint("fk_edge_devices_classroom_school", "edge_devices", type_="foreignkey")
    op.drop_constraint("fk_edge_devices_school_id_schools", "edge_devices", type_="foreignkey")
    op.drop_index("ix_edge_devices_is_active", table_name="edge_devices")
    op.drop_index("ix_edge_devices_classroom_id", table_name="edge_devices")
    op.drop_index("ix_edge_devices_school_id", table_name="edge_devices")
    op.drop_column("edge_devices", "key_rotated_at")
    op.drop_column("edge_devices", "assigned_at")
    op.drop_column("edge_devices", "classroom_id")
    op.drop_column("edge_devices", "school_id")
    op.alter_column("edge_devices", "classroom_name", existing_type=sa.String(200), nullable=False)

    op.drop_constraint("fk_users_school_id_schools", "users", type_="foreignkey")
    op.drop_index("ix_users_school_id", table_name="users")
    op.drop_column("users", "is_super_admin")
    op.drop_column("users", "school_id")

    op.drop_index("uq_classrooms_school_normalized_name", table_name="classrooms")
    op.drop_index("ix_classrooms_school_id", table_name="classrooms")
    op.drop_table("classrooms")
    op.drop_index("uq_schools_normalized_name", table_name="schools")
    op.drop_table("schools")
