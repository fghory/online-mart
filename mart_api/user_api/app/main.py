# user_api/app/main.py

import os
from fastapi import FastAPI, Depends, HTTPException, status
from datetime import timedelta
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlmodel import Session, select
from typing import Optional
from .models import User, Role, UserCreate, UserUpdate, UserRead, Token
from topic_generator.create_topic import create_kafka_topic
from .db import create_db_and_tables, get_session, engine
from .utils import (
    verify_password,
    get_password_hash,
    create_access_token,
    get_current_user,
    get_current_active_user,
    get_current_admin_user,
    map_role_to_protobuf_role,
    get_current_user_allow_expired,
    SECRET_KEY,
    ALGORITHM
)
from app.user_pb2 import User as ProtobufUser, Role as ProtobufRole, ActionType as ProtobufActionType
from aiokafka import AIOKafkaProducer
from contextlib import asynccontextmanager
import asyncio
import logging
from app import settings

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Kafka Configuration
KAFKA_BOOTSTRAP_SERVERS = "broker:19092"  # Kafka broker address
KAFKA_TOPIC = ["user_data","user_loginlogout"]              # Topic to consume from
GROUP_ID = "users"                    # Consumer group ID


# Global variable for the Kafka producer
producer: Optional[AIOKafkaProducer] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan event handler to manage application startup and shutdown tasks.

    Initializes the database, creates default admin user, and starts the Kafka producer.
    """ 

    global producer
    # Initialize the database and create tables
    create_db_and_tables()
    logger.info("Database created and tables ensured.")

    # Create Kafka topic
    await create_kafka_topic('user_data')
    await create_kafka_topic('user_loginlogout')

    # Create default admin user if none exists
    with Session(engine) as session:
        admin_user = session.exec(select(User).where(User.role == Role.ADMIN)).first()
        if not admin_user:
            hashed_password = get_password_hash(settings.DEFAULT_ADMIN_PASSWORD)
            admin_user = User(
                user_name="admin",
                user_email="admin@example.com",
                user_cellno="0000000000",
                user_address="Admin Address",
                hashed_password=hashed_password,
                role=Role.ADMIN
            )
            session.add(admin_user)
            session.commit()
            session.refresh(admin_user)
            logger.info("Default admin user created.")

    # Start Kafka producer
    
    producer = AIOKafkaProducer(
        bootstrap_servers='broker:19092')
    # Get cluster layout and initial topic/partition leadership information
    await producer.start()
    logger.info("Kafka producer started.")
    try:
        # Produce message
        yield
    finally:
        # Wait for all pending messages to be delivered or expire.
        await producer.stop()
        logger.info("Kafka producer stopped.")        
        

app = FastAPI(title="User Service", version="1.0.0", lifespan=lifespan)

# User Registration Endpoint
@app.post("/register/", response_model=UserRead, status_code=201)
async def register_user(
    user_create: UserCreate,
    session: Session = Depends(get_session)
):
    """
    Register a new user with the provided details.

    Args:
        user_create (UserCreate): The user data for registration.
        session (Session): The database session dependency.

    Returns:
        UserRead: The registered user's data.

    Raises:
        HTTPException: If the email or cell number is already registered.
    """

    # Check if email or cell number already exists
    existing_user = session.exec(
        select(User).where(
            (User.user_email == user_create.user_email) | (User.user_cellno == user_create.user_cellno)
        )
    ).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email or cell number already registered.")

    hashed_password = get_password_hash(user_create.password)
    user = User(
        user_name=user_create.user_name,
        user_email=user_create.user_email,
        user_cellno=user_create.user_cellno,
        user_address=user_create.user_address,
        hashed_password=hashed_password,
        role=Role.CUSTOMER  # Assign role as CUSTOMER by default
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    # Publish user creation event to Kafka directly in the endpoint
    try:
        # Map Role to Protobuf Role using the mapping function
        protobuf_role = map_role_to_protobuf_role(user.role)

        user_protobuf = ProtobufUser(
            id=user.id,
            user_name=user.user_name,
            user_email=user.user_email,
            user_cellno=user.user_cellno,
            user_address=user.user_address,
            role=protobuf_role,
            action=ProtobufActionType.CREATE
        )
        message = user_protobuf.SerializeToString()
        await producer.send_and_wait('user_data', message)
        logger.info(f"Published user creation event for user ID {user.id} to Kafka.")
    except Exception as e:
        # Handle exception (log it)
        logger.error(f"Failed to publish user event: {e}")

    return user  # UserRead response model will handle serialization

# Token (Login) Endpoint
@app.post("/token/", response_model=Token)
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: Session = Depends(get_session)
):
    """
    Authenticate a user and provide a JWT access token.

    Args:
        form_data (OAuth2PasswordRequestForm): The login form data.
        session (Session): The database session dependency.

    Returns:
        Token: The access token and token type.

    Raises:
        HTTPException: If the username or password is incorrect.
    """

    user = session.exec(select(User).where(User.user_name == form_data.username)).first()
    if not user:
        raise HTTPException(status_code=400, detail="Incorrect username.")
    if not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Incorrect password.")

    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": str(user.id), "role": user.role.value},   # Include role in the token
        expires_delta=access_token_expires
    )
    logger.info(f"User ID {user.id} logged in successfully.")

    # Publish user login event to Kafka
    try:
        # Map Role to Protobuf Role using the mapping function
        protobuf_role = map_role_to_protobuf_role(user.role)

        user_protobuf = ProtobufUser(
            id=user.id,
            user_name=user.user_name,
            user_email=user.user_email,
            user_cellno=user.user_cellno,
            user_address=user.user_address,
            role=protobuf_role,
            action=ProtobufActionType.LOGIN,
        )
        message = user_protobuf.SerializeToString()
        await producer.send_and_wait('user_loginlogout', message)
        logger.info(f"Published user login event for user ID {user.id} to Kafka.")
    except Exception as e:
        # Handle exception (log it)
        logger.error(f"Failed to publish user login event: {e}")

    return Token(access_token=access_token, token_type="bearer")


# Logout Endpoint
@app.post("/logout/", status_code=204)
async def logout(
    current_user: User = Depends(get_current_user_allow_expired)  # Use the new dependency
):
    """
    Logout the current user by publishing a logout event to Kafka.

    Args:
        current_user (User): The current authenticated user.

    Returns:
        None
    """

    # Publish user logout event to Kafka
    try:
        # Map Role to Protobuf Role using the mapping function
        protobuf_role = map_role_to_protobuf_role(current_user.role)

        user_protobuf = ProtobufUser(
            id=current_user.id,
            user_name=current_user.user_name,
            user_email=current_user.user_email,
            user_cellno=current_user.user_cellno,
            user_address=current_user.user_address,
            role=protobuf_role,
            action=ProtobufActionType.LOGOUT,
        )
        message = user_protobuf.SerializeToString()
        await producer.send_and_wait('user_loginlogout', message)
        logger.info(
            f"Published user logout event for user ID {current_user.id} to Kafka."
        )
    except Exception as e:
        # Handle exception (log it)
        logger.error(f"Failed to publish user logout event: {e}")

    return

# Get Current User Endpoint
@app.get("/users/me", response_model=UserRead)
async def read_users_me(current_user: User = Depends(get_current_active_user)):
    """
    Retrieve the current authenticated user's information.

    Args:
        current_user (User): The current authenticated user.

    Returns:
        UserRead: The current user's data.
    """

    return current_user  # UserRead response model will handle serialization

# Get All Users (Admin Only)
@app.get("/users", response_model=list[UserRead])
async def read_users(
    session: Session = Depends(get_session),
    admin_user: User = Depends(get_current_admin_user)
):
    """
    Retrieve all users (Admin only).

    Args:
        session (Session): The database session dependency.
        admin_user (User): The current authenticated admin user.

    Returns:
        List[UserRead]: A list of all users.
    """

    users = session.exec(select(User)).all()
    return users  # UserRead response model will handle serialization

# Get User by ID (Admin Only)
@app.get("/users/{user_id}/", response_model=UserRead)
async def read_user(
    user_id: int,
    session: Session = Depends(get_session),
    admin_user: User = Depends(get_current_admin_user)
):
    """
    Retrieve a specific user by ID (Admin only).

    Args:
        user_id (int): The ID of the user to retrieve.
        session (Session): The database session dependency.
        admin_user (User): The current authenticated admin user.

    Returns:
        UserRead: The user's data.

    Raises:
        HTTPException: If the user is not found.
    """
     
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return user  # UserRead response model will handle serialization

# Update User (Admin or Self)
@app.patch("/users/{user_id}/", response_model=UserRead)
async def update_user(
    user_id: int,
    user_update: UserUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user)
):
    """
    Update a user's information (Admin or the user themselves).

    Args:
        user_id (int): The ID of the user to update.
        user_update (UserUpdate): The updated user data.
        session (Session): The database session dependency.
        current_user (User): The current authenticated user.

    Returns:
        UserRead: The updated user's data.

    Raises:
        HTTPException: If the user is not found or permissions are insufficient.
    """

    user = session.get(User, user_id)
    logger.info(f"{user}")
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    # If the current user is not admin and not updating their own data
    if current_user.role != Role.ADMIN and current_user.id != user_id:
        raise HTTPException(status_code=403, detail="Insufficient permissions.")
    
    # Non-admin users cannot change roles
    if user_update.role and current_user.role != Role.ADMIN:
        raise HTTPException(status_code=403, detail="Only admin users can change roles.")

    # Admin users cannot change their own role (optional but recommended)
    if user_update.role and current_user.role == Role.ADMIN and current_user.id == user_id:
        raise HTTPException(status_code=400, detail="Admin users cannot change their own role.")

    # Check if the new email or cell number already exists (excluding current user)
    if user_update.user_email or user_update.user_cellno:
        existing_user = session.exec(
            select(User).where(
                ((User.user_email == user_update.user_email) | (User.user_cellno == user_update.user_cellno)) &
                (User.id != user_id)
            )
        ).first()
        if existing_user:
            raise HTTPException(status_code=400, detail="Email or cell number already registered.")

    # Update user fields
    user_data = user_update.model_dump(exclude_unset=True)
    if "password" in user_data:
        user_data["hashed_password"] = get_password_hash(user_data.pop("password"))

    for key, value in user_data.items():
        setattr(user, key, value)

    session.add(user)
    session.commit()
    session.refresh(user)

    # Publish user update event to Kafka directly in the endpoint
    try:
        # Map Role to Protobuf Role using the mapping function
        protobuf_role = map_role_to_protobuf_role(user.role)

        user_protobuf = ProtobufUser(
            id=user.id,
            user_name=user.user_name,
            user_email=user.user_email,
            user_cellno=user.user_cellno,
            user_address=user.user_address,
            role=protobuf_role,
            action=ProtobufActionType.UPDATE
        )
        message = user_protobuf.SerializeToString()
        await producer.send_and_wait('user_data', message)
        logger.info(f"Published user update event for user ID {user.id} to Kafka.")
    except Exception as e:
        # Handle exception (log it)
        logger.error(f"Failed to publish user event: {e}")

    return user  # UserRead response model will handle serialization

# Delete User (Admin Only)
@app.delete("/users/{user_id}/", status_code=204)
async def delete_user(
    user_id: int,
    session: Session = Depends(get_session),
    admin_user: User = Depends(get_current_admin_user)
):
    """
    Delete a user by ID (Admin only).

    Args:
        user_id (int): The ID of the user to delete.
        session (Session): The database session dependency.
        admin_user (User): The current authenticated admin user.

    Returns:
        None

    Raises:
        HTTPException: If the user is not found.
    """
     
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    session.delete(user)
    session.commit()

    # Publish user deletion event to Kafka directly in the endpoint
    try:
        # Map Role to Protobuf Role using the mapping function
        protobuf_role = map_role_to_protobuf_role(user.role)

        user_protobuf = ProtobufUser(
            id=user.id,
            user_name=user.user_name,
            user_email=user.user_email,
            user_cellno=user.user_cellno,
            user_address=user.user_address,
            role=protobuf_role,
            action=ProtobufActionType.DELETE
        )
        message = user_protobuf.SerializeToString()
        await producer.send_and_wait('user_data', message)
        logger.info(f"Published user deletion event for user ID {user.id} to Kafka.")
    except Exception as e:
        # Handle exception (log it)
        logger.error(f"Failed to publish user event: {e}")

    return
