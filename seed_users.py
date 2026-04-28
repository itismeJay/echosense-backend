import asyncio
from passlib.context import CryptContext
from sqlalchemy import select
from app.database import engine, Base, AsyncSessionLocal
from app.models.user import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

async def seed():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    users_to_create = [
        {"email": "admin@echosense.local", "password": "echosense2026", "role": "admin"},
        {"email": "worker1@echosense.local", "password": "password123", "role": "admin"},
        {"email": "worker2@echosense.local", "password": "password123", "role": "admin"},
    ]

    async with AsyncSessionLocal() as db:
        for user_data in users_to_create:
            result = await db.execute(
                select(User).where(User.email == user_data["email"])
            )

            if result.scalar_one_or_none():
                print(f"{user_data['email']} already exists — skipping.")
                continue  # 🔑 don't stop everything, just skip this one

            user = User(
                email=user_data["email"],
                hashed_password=pwd_context.hash(user_data["password"]),
                role=user_data["role"],
            )

            db.add(user)
            print(f"Creating user: {user.email}")

        await db.commit()  # 🔑 commit once after all inserts

if __name__ == "__main__":
    asyncio.run(seed())