# payment_service/app/models.py

from sqlmodel import SQLModel, Field
from enum import Enum
from datetime import datetime


class PaymentStatus(str, Enum):
    SUCCESS = "Success"
    FAILURE = "Failure"
    PENDING = "Pending"

class OrderStatus(str, Enum):
    NEW = "New"
    PROCESSING = "Processing"
    CONFIRMED = "Confirmed"
    SHIPPED = "Shipped"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"


class PaymentMethod(str, Enum):
    STRIPE = "Stripe"


class Payment(SQLModel, table=True):
    payment_id: int|None = Field(default=None, primary_key=True)
    order_id: int = Field(index=True, nullable=False)
    user_id: int = Field(index=True, nullable=False)
    amount: float = Field(nullable=False)
    currency: str = Field(nullable=False)
    status: PaymentStatus = Field(default=PaymentStatus.PENDING, nullable=False)
    payment_method: PaymentMethod = Field(default=PaymentMethod.STRIPE, nullable=False)
    transaction_id: str|None = Field(default=None, nullable=True)
    failure_reason: str|None = Field(default=None, nullable=True)
    timestamp: datetime = Field(default_factory=datetime.utcnow, nullable=False)


class PaymentCreate(SQLModel):
    payment_method_id: str  # Accepts predefined PaymentMethod IDs from Stripe


class PaymentRead(SQLModel):
    payment_id: int
    order_id: int
    user_id: int
    amount: float
    currency: str
    status: PaymentStatus
    payment_method: PaymentMethod
    transaction_id: str|None
    failure_reason: str|None
    timestamp: datetime


class OrderPayment(SQLModel, table=True):
    order_id: int = Field(primary_key=True)
    user_id: int = Field(index=True, nullable=False)
    order_total: float = Field(nullable=False)
    currency: str = Field(nullable=False)
    customer_address: str|None = Field(default=None, nullable=True)
    status: str = Field(default="New", nullable=False)
    timestamp: datetime = Field(default_factory=datetime.utcnow, nullable=False)
