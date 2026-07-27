"""Enforce append-only audit records at the PostgreSQL table boundary.

Revision ID: 20260727_0003
Revises: 20260727_0002
"""

from alembic import op

revision = "20260727_0003"
down_revision = "20260727_0002"
branch_labels = None
depends_on = None

PROTECTION_FUNCTION = "echosense_protect_audit_logs"
MUTATION_TRIGGER = "trg_audit_logs_prevent_mutation"
TRUNCATE_TRIGGER = "trg_audit_logs_prevent_truncate"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE FUNCTION {PROTECTION_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'UPDATE'
               AND pg_trigger_depth() > 1
               AND OLD.actor_user_id IS NOT NULL
               AND NEW.actor_user_id IS NULL
               AND (to_jsonb(NEW) - 'actor_user_id')
                   IS NOT DISTINCT FROM (to_jsonb(OLD) - 'actor_user_id')
            THEN
                RETURN NEW;
            END IF;

            RAISE EXCEPTION 'audit logs are append-only'
                USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {MUTATION_TRIGGER}
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW
        EXECUTE FUNCTION {PROTECTION_FUNCTION}()
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {TRUNCATE_TRIGGER}
        BEFORE TRUNCATE ON audit_logs
        FOR EACH STATEMENT
        EXECUTE FUNCTION {PROTECTION_FUNCTION}()
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {TRUNCATE_TRIGGER} ON audit_logs")
    op.execute(f"DROP TRIGGER IF EXISTS {MUTATION_TRIGGER} ON audit_logs")
    op.execute(f"DROP FUNCTION IF EXISTS {PROTECTION_FUNCTION}()")
