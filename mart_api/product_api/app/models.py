from sqlmodel import SQLModel, Field
from typing import Optional
from enum import Enum



# Define Enum for item status
class ProductStatus(str, Enum):
    IN_STOCK = "In Stock"
    OUT_OF_STOCK = "Out of Stock"
    COMING_SOON = "Coming Soon"
    DISCONTINUED = "Discontinued"

class ProductBase(SQLModel):
    product_name: str = Field(index=True)
    product_category: str
    product_price: float = Field(index=True)
    product_quantity: int
    product_status: ProductStatus

class ProductCreate(ProductBase):
    pass

class Products(ProductBase, table=True):
    product_id: Optional[int] = Field(default=None, primary_key=True)


    