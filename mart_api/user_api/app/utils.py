# user_api/app/utils.py

from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext
from jose import jwt, JWTError, ExpiredSignatureError
from datetime import datetime, timedelta
from app import settings
from fastapi import Depends, HTTPException, status
from sqlmodel import Session
from app.models import User, Role, TokenData
from .db import get_session
from app.user_pb2 import Role as ProtobufRole
import logging


# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT Configuration
SECRET_KEY = settings.SECRET_KEY
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")



def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify that a plain password matches the given hashed password.

    Args:
        plain_password (str): The plain text password provided by the user.
        hashed_password (str): The hashed password stored in the database.

    Returns:
        bool: True if the passwords match, False otherwise.
    """
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    """
    Hash a plain password using a secure hashing algorithm.

    Args:
        password (str): The plain text password to hash.

    Returns:
        str: The hashed password.
    """
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: timedelta = None) -> str:
    """
    Create a JWT access token with optional expiration.

    Args:
        data (dict): The data payload to include in the token.
        expires_delta (timedelta, optional): The time delta after which the token expires.

    Returns:
        str: The encoded JWT token as a string.
    """
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta if expires_delta else timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

# Dependency to get current user
async def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: Session = Depends(get_session),
    allow_expired: bool = False,
) -> User:
    """
    Retrieve the current user based on the provided JWT token.

    Args:
        token (str): The JWT access token from the Authorization header.
        session (Session): The database session dependency.
        allow_expired (bool): Flag to allow expired tokens for certain operations.

    Returns:
        User: The authenticated user object.

    Raises:
        HTTPException: If the token is invalid or the user does not exist.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        options = {"verify_exp": not allow_expired}
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM], options=options)
        user_id: str = payload.get("sub")
        role: str = payload.get("role")  # Extract role from token
        if user_id is None or role is None:
            raise credentials_exception
        token_data = TokenData(user_id=int(user_id))
    except ExpiredSignatureError:
        if not allow_expired: # access token's expiration time having passed (as defined by ACCESS_TOKEN_EXPIRE_MINUTES).
            raise credentials_exception
        # Proceed with expired token
        payload = jwt.decode(
            token, SECRET_KEY, algorithms=[ALGORITHM], options={"verify_exp": False}
        )
        user_id: str = payload.get("sub")
        role: str = payload.get("role")
        if user_id is None or role is None:
            raise credentials_exception
        token_data = TokenData(user_id=int(user_id))
    except JWTError:
        raise credentials_exception
    user = session.get(User, token_data.user_id)
    if user is None:
        raise credentials_exception
    logger.debug(f"Decoded token for user ID: {user_id}, Role: {role}")
    return user

# Dependency to get current active user
async def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    """
    Ensure that the current user is active.

    Args:
        current_user (User): The current authenticated user.

    Returns:
        User: The current active user.
    """
    return current_user

# Dependency to get current admin user
async def get_current_admin_user(current_user: User = Depends(get_current_user)) -> User:
    """
    Ensure that the current user has admin privileges.

    Args:
        current_user (User): The current authenticated user.

    Returns:
        User: The current admin user.

    Raises:
        HTTPException: If the user does not have admin privileges.
    """

    if current_user.role != Role.ADMIN:
        raise HTTPException(status_code=403, detail="Insufficient permissions.")
    return current_user

# converts User model Role to protobuf Role
def map_role_to_protobuf_role(role: Role) -> ProtobufRole:
    if role == Role.ADMIN:
        return ProtobufRole.ADMIN
    elif role == Role.CUSTOMER:
        return ProtobufRole.CUSTOMER
    else:
        raise ValueError(f"Unknown role: {role}")

    
# New function to allow expired tokens
async def get_current_user_allow_expired(
    token: str = Depends(oauth2_scheme),
    session: Session = Depends(get_session),
) -> User:
    """
    Retrieve the current user, allowing for expired tokens (used during logout).

    Args:
        token (str): The JWT access token from the Authorization header.
        session (Session): The database session dependency.

    Returns:
        User: The authenticated user object, even if the token is expired.
    """
    return await get_current_user(token, session, allow_expired=True)    