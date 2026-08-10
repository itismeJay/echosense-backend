import argparse
import asyncio
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from passlib.context import CryptContext
from sqlalchemy import select

SEED_EMAIL_ENV = "ECHOSENSE_SEED_USER_EMAIL"
SEED_PASSWORD_ENV = "ECHOSENSE_SEED_USER_PASSWORD"
SEED_ROLE_ENV = "ECHOSENSE_SEED_USER_ROLE"
ALLOWED_ROLES = ("admin", "staff", "counselor")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class SeedConfigurationError(ValueError):
    """Raised before database access when explicit seed credentials are missing."""


@dataclass(frozen=True)
class SeedUserConfig:
    email: str
    password: str = field(repr=False)
    role: str
    is_super_admin: bool = False


def load_seed_config(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> SeedUserConfig:
    environment = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(
        description="Create one explicitly configured EchoSense development user.",
    )
    parser.add_argument(
        "--email",
        default=environment.get(SEED_EMAIL_ENV),
        help=f"User email; defaults only to the explicit {SEED_EMAIL_ENV} environment value.",
    )
    parser.add_argument(
        "--role",
        default=environment.get(SEED_ROLE_ENV, "admin"),
        choices=ALLOWED_ROLES,
        help=f"User role; may also be supplied through {SEED_ROLE_ENV}.",
    )
    parser.add_argument(
        "--super-admin",
        action="store_true",
        help="Explicitly bootstrap a global administrator. Valid only with --role admin.",
    )
    args = parser.parse_args(argv)

    email = (args.email or "").strip()
    password = environment.get(SEED_PASSWORD_ENV)
    if not email:
        raise SeedConfigurationError(
            f"Provide --email or set {SEED_EMAIL_ENV}; no seed email default exists."
        )
    if password is None or not password.strip():
        raise SeedConfigurationError(
            f"Set {SEED_PASSWORD_ENV} explicitly; no seed password default exists."
        )
    if args.super_admin and args.role != "admin":
        raise SeedConfigurationError("--super-admin requires --role admin.")

    return SeedUserConfig(
        email=email,
        password=password,
        role=args.role,
        is_super_admin=args.super_admin,
    )


async def seed(config: SeedUserConfig) -> None:
    # Import application configuration only after seed inputs pass validation, so a missing
    # credential always fails before any database access is attempted.
    from app.database import AsyncSessionLocal, Base, engine
    import app.models.alert  # noqa: F401
    import app.models.classroom  # noqa: F401
    import app.models.edge_device  # noqa: F401
    import app.models.school  # noqa: F401
    import app.models.slur  # noqa: F401
    from app.models.user import User

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email == config.email))
        if result.scalar_one_or_none():
            print(f"{config.email} already exists — skipping.")
            return

        db.add(
            User(
                email=config.email,
                hashed_password=pwd_context.hash(config.password),
                role=config.role,
                is_super_admin=config.is_super_admin,
            )
        )
        await db.commit()
        print(f"Created development user: {config.email} ({config.role}).")


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    try:
        config = load_seed_config(argv, environ)
    except SeedConfigurationError as exc:
        print(f"Seed configuration error: {exc}")
        return 2
    asyncio.run(seed(config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
