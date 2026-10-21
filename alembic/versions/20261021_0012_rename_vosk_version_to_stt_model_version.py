"""Rename system_settings.vosk_version to stt_model_version for faster-whisper.

Revision ID: 20261021_0012
Revises: 20261021_0011

The edge now transcribes with faster-whisper, so the column name and the stored
default no longer describe the model in use. The rename keeps the type and data; only
a value still equal to the old Vosk default is replaced, so an administrator-set value
is preserved.

Not backward compatible with code that maps vosk_version: deploy the code from this
revision together with the migration (Render's pre-deploy migrates first).

The downgrade renames the column back and restores the Vosk default where the value
is the faster-whisper default. A value that was already the faster-whisper default
before this revision is also reverted; that loss is accepted for a default string.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261021_0012"
down_revision = "20261021_0011"
branch_labels = None
depends_on = None

VOSK_DEFAULT = "vosk-model-small-en-us-0.15"
STT_DEFAULT = "faster-whisper small int8"

REPLACE_VALUE = "UPDATE system_settings SET {column} = :new WHERE {column} = :old"


def upgrade() -> None:
    op.alter_column("system_settings", "vosk_version", new_column_name="stt_model_version")
    op.get_bind().execute(
        sa.text(REPLACE_VALUE.format(column="stt_model_version")),
        {"new": STT_DEFAULT, "old": VOSK_DEFAULT},
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(REPLACE_VALUE.format(column="stt_model_version")),
        {"new": VOSK_DEFAULT, "old": STT_DEFAULT},
    )
    op.alter_column("system_settings", "stt_model_version", new_column_name="vosk_version")
