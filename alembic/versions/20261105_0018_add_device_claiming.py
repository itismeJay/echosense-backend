"""QR device claiming: unclaimed -> claimed -> active edge devices.

Revision ID: 20261105_0018
Revises: 20261105_0017

A super admin provisions boxes that belong to no school yet (claim_state='unclaimed',
no device key). A school admin claims one into a classroom by scanning its QR sticker
(claim_state='claimed'); the box then activates itself and receives its device key once
(claim_state='active'). Only hashes of the claim code, the box secret and the device key
are stored.

Existing devices were all registered with a key, so they become 'active' and behave as
before; no existing row is otherwise changed. api_key_hash becomes nullable because
unclaimed and claimed boxes have no key yet.

Downgrade: rows without a key (unclaimed/claimed boxes) get an unusable key hash and are
disabled so the NOT NULL column can return; their claim data is dropped with the columns.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261105_0018"
down_revision = "20261105_0017"
branch_labels = None
depends_on = None

# Never verifies as a passlib hash (verify_device_key treats it as invalid).
_UNUSABLE_KEY_HASH = "!unclaimed-device-no-key"


def upgrade() -> None:
    op.add_column(
        "edge_devices",
        sa.Column(
            "claim_state",
            sa.String(20),
            nullable=False,
            server_default="active",
        ),
    )
    op.add_column("edge_devices", sa.Column("claim_code_hash", sa.String(255), nullable=True))
    op.add_column("edge_devices", sa.Column("box_secret_hash", sa.String(128), nullable=True))
    op.add_column(
        "edge_devices", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "edge_devices",
        sa.Column(
            "claimed_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_edge_devices_claimed_by_users"),
            nullable=True,
        ),
    )
    op.add_column(
        "edge_devices", sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.alter_column("edge_devices", "api_key_hash", existing_type=sa.String(255), nullable=True)
    op.create_check_constraint(
        "ck_edge_devices_claim_state",
        "edge_devices",
        "claim_state IN ('unclaimed', 'claimed', 'active')",
    )
    # Unclaimed boxes belong to no school and have no key; claimed boxes sit in a
    # classroom and wait for their key; active devices always have a key.
    op.create_check_constraint(
        "ck_edge_devices_claim_state_shape",
        "edge_devices",
        "(claim_state = 'unclaimed' AND school_id IS NULL AND classroom_id IS NULL"
        " AND api_key_hash IS NULL)"
        " OR (claim_state = 'claimed' AND school_id IS NOT NULL AND classroom_id IS NOT NULL"
        " AND api_key_hash IS NULL)"
        " OR (claim_state = 'active' AND api_key_hash IS NOT NULL)",
    )
    op.create_index("ix_edge_devices_claim_state", "edge_devices", ["claim_state"])


def downgrade() -> None:
    op.drop_index("ix_edge_devices_claim_state", table_name="edge_devices")
    op.drop_constraint("ck_edge_devices_claim_state_shape", "edge_devices", type_="check")
    op.drop_constraint("ck_edge_devices_claim_state", "edge_devices", type_="check")
    op.execute(
        sa.text(
            "UPDATE edge_devices SET api_key_hash = :unusable, is_active = false "
            "WHERE api_key_hash IS NULL"
        ).bindparams(unusable=_UNUSABLE_KEY_HASH)
    )
    op.alter_column("edge_devices", "api_key_hash", existing_type=sa.String(255), nullable=False)
    op.drop_column("edge_devices", "activated_at")
    op.drop_constraint("fk_edge_devices_claimed_by_users", "edge_devices", type_="foreignkey")
    op.drop_column("edge_devices", "claimed_by")
    op.drop_column("edge_devices", "claimed_at")
    op.drop_column("edge_devices", "box_secret_hash")
    op.drop_column("edge_devices", "claim_code_hash")
    op.drop_column("edge_devices", "claim_state")
