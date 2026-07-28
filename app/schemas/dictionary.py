from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.languages import DictionaryLanguageCode


class SlurCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    slur_text: str = Field(..., min_length=1, max_length=100)
    language: DictionaryLanguageCode
    severity_weight: float = Field(..., ge=0.0, le=1.0)


class SlurEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    term_id: int
    slur_text: str
    language: DictionaryLanguageCode
    severity_weight: float
    added_at: datetime
