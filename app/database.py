from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import settings

DATABASE_URL = settings.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://").replace(
    "?sslmode=require&channel_binding=require", ""
)

engine_options = {"echo": settings.SQL_ECHO}
if settings.TESTING:
    engine_options["poolclass"] = NullPool

engine = create_async_engine(DATABASE_URL, **engine_options)

AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
