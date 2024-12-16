# inventory_api/app/models.py

from sqlmodel import SQLModel, Field
from typing import Optional
from enum import Enum



# Define Enum for item status
class ItemStatus(str, Enum):
    IN_STOCK = "In Stock"
    OUT_OF_STOCK = "Out of Stock"
    COMING_SOON = "Coming Soon"
    DISCONTINUED = "Discontinued"


class InventoryBase(SQLModel):
    item_name: str = Field(index=True)
    item_category: str
    item_price: float = Field(index=True)
    item_quantity: int
    item_status: ItemStatus

class InventoryCreate(InventoryBase):
    pass

class Inventory(InventoryBase, table=True):
    item_id: Optional[int] = Field(default=None, primary_key=True)

    def update_status(self):
        if self.item_quantity > 0:
            self.item_status = ItemStatus.IN_STOCK
        else:
            self.item_status = ItemStatus.OUT_OF_STOCK

class InventoryUpdate(SQLModel):
    item_name: str | None = None
    item_category: str | None = None
    item_price: float | None = None
    item_quantity: int | None = None
    item_status: ItemStatus | None = None    


    