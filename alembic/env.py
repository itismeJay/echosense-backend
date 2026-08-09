import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.environ.get("ALEMBIC_DATABASE_URL")
if not database_url:
    raise RuntimeError(
        "ALEMBIC_DATABASE_URL must be set explicitly; migrations never read .env implicitly"
    )

# Ensure importing the application metadata cannot construct an engine for the
# repository's .env database. The explicitly supplied migration URL is always
# the source of truth for this process.
os.environ["DATABASE_URL"] = database_url
os.environ.setdefault("SECRET_KEY", "local-migration-only")
os.environ["SQL_ECHO"] = "false"

from app.database import Base  # noqa: E402
from app.models.alert import Alert  # noqa: E402,F401
from app.models.audit_log import AuditLog  # noqa: E402,F401
from app.models.report import Report  # noqa: E402,F401
from app.models.slur import SlurEntry  # noqa: E402,F401
from app.models.system_settings import SystemSettings  # noqa: E402,F401
from app.models.user import User  # noqa: E402,F401
from app.models.edge_device import EdgeDevice  # noqa: E402,F401

database_url = database_url.replace(
    "postgresql+asyncpg://",
    "postgresql+psycopg2://",
)
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
