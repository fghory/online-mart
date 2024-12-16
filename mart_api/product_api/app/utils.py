# app/utils.py

from app import inventory_pb2
from app.models import ProductStatus as PythonItemStatus

def map_protobuf_to_python_item_status(protobuf_status: inventory_pb2.ItemStatus) -> PythonItemStatus:
    """
    Maps Protobuf ItemStatus enum to Python ItemStatus enum.
    """
    mapping = {
        inventory_pb2.ItemStatus.IN_STOCK: PythonItemStatus.IN_STOCK,
        inventory_pb2.ItemStatus.OUT_OF_STOCK: PythonItemStatus.OUT_OF_STOCK,
        inventory_pb2.ItemStatus.COMING_SOON: PythonItemStatus.COMING_SOON,
        inventory_pb2.ItemStatus.DISCONTINUED: PythonItemStatus.DISCONTINUED,
    }
    if protobuf_status in mapping:
        return mapping[protobuf_status]
    else:
        raise ValueError(f"Unknown Protobuf ItemStatus: {protobuf_status}")
