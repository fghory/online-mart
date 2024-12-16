# inventory_api/app/utils.py
from fastapi import Depends, HTTPException, status
from jose import JWTError, jwt
from fastapi.security import OAuth2PasswordBearer
from app import inventory_pb2, user_pb2, settings
from app.models import ItemStatus as PythonItemStatus
from typing import Annotated


# OAuth2 scheme to extract token from authorization header
# oauth2_scheme = OAuth2PasswordBearer(tokenUrl="http://127.0.0.1:8004/token")

# JWT Configuration
SECRET_KEY = settings.SECRET_KEY  # Should match User service
ALGORITHM = "HS256"

# Import the user_sessions from session_store
from app.session_store import user_sessions

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


def map_python_to_protobuf_item_status(python_status: PythonItemStatus) -> inventory_pb2.ItemStatus:
    # Mapping Python Enum to Protobuf Enum
    mapping = {
        PythonItemStatus.IN_STOCK: inventory_pb2.ItemStatus.IN_STOCK,
        PythonItemStatus.OUT_OF_STOCK: inventory_pb2.ItemStatus.OUT_OF_STOCK,
        PythonItemStatus.COMING_SOON: inventory_pb2.ItemStatus.COMING_SOON,
        PythonItemStatus.DISCONTINUED: inventory_pb2.ItemStatus.DISCONTINUED,
    }
    # Default or error handling can be customized here
    return mapping.get(python_status, inventory_pb2.ItemStatus.IN_STOCK)
