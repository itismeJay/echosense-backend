import asyncio
from passlib.context import CryptContext
from sqlalchemy import select
from app.database import engine, Base, AsyncSessionLocal
from app.models.user import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

async def seed():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email == "admin@echosense.local"))
        if result.scalar_one_or_none():
            print("Admin user already exists — skipping.")
            return

        admin = User(
            email="admin@echosense.local",
            hashed_password=pwd_context.hash("echosense2026"),
            role="admin",
        )
        db.add(admin)
        await db.commit()
        await db.refresh(admin)
        print(f"Created admin user: {admin.email} (id={admin.id})")

if __name__ == "__main__":
    asyncio.run(seed())
