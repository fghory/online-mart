# order_api/app/utils.py

from fastapi import Depends, HTTPException, status
from jose import JWTError, jwt
from app import order_pb2, user_pb2, payment_pb2
from app import settings
from app.models import OrderStatus as PythonOrderStatus, PaymentStatus
from typing import Annotated
from app.session_store import user_sessions # Import the user_sessions from session_store

# OAuth2 scheme to extract token from authorization header
# oauth2_scheme = OAuth2PasswordBearer(tokenUrl="http://127.0.0.1:8004/token")

# JWT Configuration
SECRET_KEY = settings.SECRET_KEY  # Should match User service
ALGORITHM = "HS256"


# Dependency to get current user
# Commented out function definition to be used for actual token extraction from authorization header.
# This can be implemented using front end. Fastapi documentation does not support it.
# async def get_current_user(
#     token: Annotated[str, Depends(oauth2_scheme)]
# ):
async def get_current_user(
    token: str
):    
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub"))
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")

    # Check if user is in session store
    if user_id not in user_sessions:
        raise HTTPException(status_code=401, detail="User not logged in")

    # Retrieve user info from session
    user = user_sessions[user_id]
    user['user_id'] = user_id  # Include user_id in user data
    return user

# Dependency to get current active user
async def get_current_active_user(
    user: Annotated[dict, Depends(get_current_user)]
):
    return user

# Dependency to get current admin user
async def get_current_admin_user(
    user: Annotated[dict, Depends(get_current_user)]
):
    if user['role'] != user_pb2.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Insufficient permissions.")
    return user


def map_python_to_protobuf_order_status(python_status: PythonOrderStatus) -> order_pb2.OrderStatus:
    # Mapping Python Enum to Protobuf Enum
    mapping = {
        PythonOrderStatus.NEW: order_pb2.OrderStatus.NEW,
        PythonOrderStatus.PROCESSING: order_pb2.OrderStatus.PROCESSING,
        PythonOrderStatus.CONFIRMED: order_pb2.OrderStatus.CONFIRMED,
        PythonOrderStatus.SHIPPED: order_pb2.OrderStatus.SHIPPED,
        PythonOrderStatus.COMPLETED: order_pb2.OrderStatus.COMPLETED,
        PythonOrderStatus.CANCELLED: order_pb2.OrderStatus.CANCELLED,
    }
    # Default or error handling can be customized here
    return mapping.get(python_status, order_pb2.OrderStatus.NEW)


def map_protobuf_to_payment_status(protobuf_status: payment_pb2.PaymentStatus) -> PaymentStatus:
    """
    Maps Protobuf ItemStatus enum to Python ItemStatus enum.
    """
    mapping = {
        payment_pb2.PaymentStatus.SUCCESS: PaymentStatus.SUCCESS,
        payment_pb2.PaymentStatus.FAILURE: PaymentStatus.FAILURE,
        payment_pb2.PaymentStatus.PENDING: PaymentStatus.PENDING,
       
    }
    if protobuf_status in mapping:
        return mapping[protobuf_status]
    else:
        raise ValueError(f"Unknown Protobuf ItemStatus: {protobuf_status}") 