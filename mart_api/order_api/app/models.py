# order_api/app/models.py

from sqlmodel import SQLModel, Field
from pydantic import EmailStr
from enum import Enum


class OrderStatus(str, Enum):
    NEW = "New"
    PROCESSING = "Processing"
    CONFIRMED = "Confirmed"
    SHIPPED = "Shipped"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"

class OrderCreate(SQLModel):
    customer_name: str = Field(index=True)
    customer_email: EmailStr
    customer_cellno: str
    customer_address: str
    product_id: int = Field(index=True)
    order_quantity: int

class OrderBase(OrderCreate):
    order_status: OrderStatus = Field(default=OrderStatus.NEW)
    product_price: float|None = None  # Set from Inventory Service
  
class Orders(OrderBase, table=True):
    order_id: int|None = Field(default=None, primary_key=True)
    user_id: int = Field(default=None, index=True)
    order_total: float = Field(default=None)

class OrderOutput(SQLModel):
    order_id: int
    user_id: int
    customer_name: str
    customer_email: EmailStr
    customer_cellno: str
    customer_address: str
    product_id: int
    order_quantity: int
    order_status: OrderStatus
    product_price: float
    order_total: float

class ProductInventory(SQLModel, table=True):
    product_id: int = Field(default=None, primary_key=True)
    product_price: float
    product_quantity: int    

class OrderUpdate(SQLModel):
    customer_name: str|None = None
    customer_email: EmailStr|None = None
    customer_cellno: str|None = None
    customer_address: str|None = None
    product_id: int|None = None 
    order_quantity: int|None = None    

class OrderStatusUpdate(SQLModel):
    order_status: OrderStatus|None = None

class PaymentStatus(str, Enum):
    SUCCESS = "Success"
    FAILURE = "Failure"
    PENDING = "Pending"    

