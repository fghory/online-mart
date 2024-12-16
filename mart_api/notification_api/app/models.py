# notification_api/app/models.py

from sqlmodel import SQLModel, Field
from enum import Enum

class NotificationType(str, Enum):
    EMAIL = "Email"
    SMS = "SMS"

class OrderStatus(str, Enum):
    NEW = "NEW"
    CONFIRMED = "CONFIRMED"
    PROCESSING = "PROCESSING"
    SHIPPED = "SHIPPED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"

class PaymentStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    PENDING = "PENDING"

class NotificationLog(SQLModel, table=True):
    id: int|None = Field(default=None, primary_key=True)
    notification_type: NotificationType
    recipient: str
    message: str
    status: str
    timestamp: float|None = Field(default=None)

class User(SQLModel, table=True):
    id: int|None = Field(default=None, primary_key=True)
    name: str = Field(index=True, nullable=False)
    email: str = Field(index=True, nullable=False)
    cell_number: str = Field(index=True, nullable=False)    
    address: str = Field(index=True, nullable=False)

# Define Enum for item status
class ItemStatus(str, Enum):
    IN_STOCK = "In Stock"
    OUT_OF_STOCK = "Out of Stock"
    COMING_SOON = "Coming Soon"
    DISCONTINUED = "Discontinued"    
