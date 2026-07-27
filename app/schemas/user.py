from typing import Literal
from pydantic import BaseModel


class LoginRequest(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: str
    email: str
    role: str

    class Config:
        from_attributes = True


class RegisterRequest(BaseModel):
    email: str
    password: str
    role: Literal["admin", "staff", "counselor"] = "staff"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class PushTokenRequest(BaseModel):
    token: str
