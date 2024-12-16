# user_api/app/models.py

from sqlmodel import SQLModel, Field
from enum import Enum

class Role(str, Enum):
    ADMIN = "ADMIN"
    CUSTOMER = "CUSTOMER"

class User(SQLModel, table=True):
    id: int|None = Field(default=None, primary_key=True)
    user_name: str = Field(index=True, nullable=False)
    user_email: str = Field(index=True, unique=True, nullable=False)
    user_cellno: str = Field(index=True, unique=True, nullable=False)
    user_address: str = Field(nullable=False)
    hashed_password: str = Field(nullable=False)
    role: Role = Field(default=Role.CUSTOMER, nullable=False)

class UserCreate(SQLModel):
    user_name: str
    user_email: str
    user_cellno: str
    user_address: str
    password: str

class UserRead(SQLModel):
    id: int
    user_name: str
    user_email: str
    user_cellno: str
    user_address: str
    role: Role

class UserUpdate(SQLModel):
    user_name: str|None = None
    user_email: str|None = None
    user_cellno: str|None = None
    user_address: str|None = None
    password: str|None = None
    role: Role|None = None

class Token(SQLModel):
    access_token: str
    token_type: str

class TokenData(SQLModel):
    user_id: int|None = None
