from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from app.database import get_db
from app.models.slur import SlurEntry as SlurModel
from app.schemas.dictionary import SlurEntry, SlurCreate
from app.routers.auth import require_admin

router = APIRouter(prefix="/dictionary", tags=["Dictionary"])


@router.get("/", response_model=List[SlurEntry])
async def get_dictionary(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SlurModel).order_by(SlurModel.severity_weight.desc())
    )
    return result.scalars().all()


@router.post("/", response_model=SlurEntry, status_code=201)
async def add_slur(
    body: SlurCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin),
):
    existing = await db.execute(
        select(SlurModel).where(SlurModel.slur_text == body.slur_text)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Slur already exists")

    entry = SlurModel(
        slur_text=body.slur_text,
        language=body.language,
        severity_weight=body.severity_weight,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


@router.delete("/{term_id}")
async def delete_slur(
    term_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin),
):
    result = await db.execute(select(SlurModel).where(SlurModel.term_id == term_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Term not found")
    await db.delete(entry)
    await db.commit()
    return {"message": "Keyword deleted"}
