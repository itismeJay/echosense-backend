"""Give every existing user a school, and make admin@echosense.app the super admin.

Revision ID: 20261105_0016
Revises: 20261105_0015

Live ran 0008 before schools existed. 0009 only assigns users to a school when exactly
one school can be derived from device/alert names, and promotes every role='admin' user
to super admin. Without this revision, a live staff or counselor account can be left with
no school and gets 403 "Administrator is not assigned to a school" on most pages.

What it does (data only, idempotent; a second run changes nothing):
1. admin@echosense.app (case-insensitive) becomes role='admin', is_super_admin=true and
   school-less, which is how super admins are created everywhere else.
2. Every other user stops being a super admin (0009 promoted every admin; an admin stays
   an admin, of their school), and every user with no school is assigned to the default
   school. This runs only when admin@echosense.app exists, so the system is never left
   without a super admin.
3. Alerts with no school (recorded before schools existed) are filed under the default
   school, so its users keep their history. classroom_id is left as it was.
4. One audit row (action MIGRATION_ASSIGN_DEFAULT_SCHOOL) records the user ids changed.

The default school is the active school with the most alerts (then the oldest, then by
name). If there is no active school and someone needs one, a school named
ECHOSENSE_DEFAULT_SCHOOL_NAME (default "EchoSense Pilot School") is created with its
settings row.

Downgrade does nothing: the previous state (users locked out) is not worth restoring,
and the audit row says exactly what changed.
"""

import json
import os

from alembic import op
import sqlalchemy as sa


revision = "20261105_0016"
down_revision = "20261105_0015"
branch_labels = None
depends_on = None

SUPER_ADMIN_EMAIL = "admin@echosense.app"
DEFAULT_SCHOOL_NAME = "EchoSense Pilot School"
AUDIT_ACTION = "MIGRATION_ASSIGN_DEFAULT_SCHOOL"


def _default_school_name() -> str:
    name = (os.environ.get("ECHOSENSE_DEFAULT_SCHOOL_NAME") or "").strip()
    return name[:200] or DEFAULT_SCHOOL_NAME


def _pick_default_school(connection):
    return connection.execute(
        sa.text(
            """
            SELECT s.id
            FROM schools s
            LEFT JOIN alerts a ON a.school_id = s.id
            WHERE s.is_active
            GROUP BY s.id, s.created_at, s.name
            ORDER BY count(a.id) DESC, s.created_at ASC, lower(s.name) ASC
            LIMIT 1
            """
        )
    ).scalar()


def _create_default_school(connection, name: str):
    existing = connection.execute(
        sa.text("SELECT id, is_active FROM schools WHERE lower(btrim(name)) = lower(btrim(:name))"),
        {"name": name},
    ).first()
    if existing is not None:
        # An inactive school with that name: reactivating it silently would be surprising,
        # so use a distinct name instead.
        name = f"{name} (default)"
    school_id = connection.execute(
        sa.text("INSERT INTO schools (id, name) VALUES (gen_random_uuid(), :name) RETURNING id"),
        {"name": name},
    ).scalar()
    connection.execute(
        sa.text(
            "INSERT INTO school_settings (school_id) VALUES (:school_id) "
            "ON CONFLICT (school_id) DO NOTHING"
        ),
        {"school_id": school_id},
    )
    return school_id


def assign(connection) -> dict:
    summary: dict = {
        "super_admin_user_id": None,
        "super_admin_changed": False,
        "demoted_user_ids": [],
        "assigned_user_ids": [],
        "alerts_filed": 0,
    }

    before = connection.execute(
        sa.text(
            "SELECT id, role, is_super_admin, school_id FROM users "
            "WHERE lower(btrim(email)) = :email"
        ),
        {"email": SUPER_ADMIN_EMAIL},
    ).first()
    if before is None:
        print(
            f"[0016] {SUPER_ADMIN_EMAIL} does not exist; no user was changed. "
            "Existing super admins keep their access."
        )
        return summary
    super_admin_id = before.id
    summary["super_admin_user_id"] = super_admin_id
    summary["super_admin_changed"] = (
        before.role != "admin" or not before.is_super_admin or before.school_id is not None
    )
    connection.execute(
        sa.text(
            "UPDATE users SET role = 'admin', is_super_admin = true, school_id = NULL "
            "WHERE id = :id"
        ),
        {"id": super_admin_id},
    )

    # 0009 promoted every admin; only admin@echosense.app keeps global access. The others
    # stay admins, of their (or the default) school.
    summary["demoted_user_ids"] = sorted(
        connection.execute(
            sa.text(
                "UPDATE users SET is_super_admin = false "
                "WHERE is_super_admin AND id <> :id RETURNING id"
            ),
            {"id": super_admin_id},
        )
        .scalars()
        .all()
    )

    needs_school = connection.execute(
        sa.text("SELECT count(*) FROM users WHERE school_id IS NULL AND id <> :id"),
        {"id": super_admin_id},
    ).scalar()
    orphan_alerts = connection.execute(
        sa.text("SELECT count(*) FROM alerts WHERE school_id IS NULL")
    ).scalar()

    school_id = None
    school_name = None
    if needs_school or orphan_alerts:
        school_id = _pick_default_school(connection)
        if school_id is None:
            school_id = _create_default_school(connection, _default_school_name())
            summary["created_school"] = True
        summary["school_id"] = str(school_id)
        school_name = connection.execute(
            sa.text("SELECT name FROM schools WHERE id = :id"), {"id": school_id}
        ).scalar()
        summary["assigned_user_ids"] = sorted(
            connection.execute(
                sa.text(
                    "UPDATE users SET school_id = :school_id "
                    "WHERE school_id IS NULL AND id <> :id RETURNING id"
                ),
                {"school_id": school_id, "id": super_admin_id},
            )
            .scalars()
            .all()
        )
        summary["alerts_filed"] = connection.execute(
            sa.text("UPDATE alerts SET school_id = :school_id WHERE school_id IS NULL"),
            {"school_id": school_id},
        ).rowcount

    changed = (
        summary["super_admin_changed"]
        or summary["demoted_user_ids"]
        or summary["assigned_user_ids"]
        or summary["alerts_filed"]
    )
    if not changed:
        print(f"[0016] {SUPER_ADMIN_EMAIL} is the only super admin; every user has a school.")
        return summary

    print(
        f"[0016] {SUPER_ADMIN_EMAIL} is the only super admin (user {super_admin_id}). "
        f"{len(summary['demoted_user_ids'])} other super admin(s) became school admins. "
        f"Assigned {len(summary['assigned_user_ids'])} user(s) and "
        f"{summary['alerts_filed']} alert(s) to the default school {school_name!r}."
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO audit_logs (
              actor_email, actor_role, action, resource, resource_id, target,
              status, description, metadata, school_id
            )
            VALUES (
              'system:migration', 'system', :action, 'USER', :resource_id, :target,
              'SUCCESS', :description, CAST(:metadata AS JSONB), :school_id
            )
            """
        ),
        {
            "action": AUDIT_ACTION,
            "resource_id": str(super_admin_id),
            "target": (f"Default school {school_name}" if school_name else "Users")[:500],
            "description": (
                f"Migration {revision}: {SUPER_ADMIN_EMAIL} made the only super admin; "
                "users without a school assigned to the default school."
            )[:500],
            "metadata": json.dumps(summary),
            "school_id": school_id,
        },
    )
    return summary


def upgrade() -> None:
    assign(op.get_bind())


def downgrade() -> None:
    pass
