# payment_service/app/utils.py

import logging
from jose import jwt, JWTError
from fastapi import HTTPException, status, Depends
from app import settings
from app.models import PaymentStatus, PaymentMethod, PaymentCreate, OrderStatus as PythonOrderStatus
from app import payment_pb2
from typing import Annotated
import stripe
from sqlmodel import Session, select
from app.db import engine
from app import order_pb2
from datetime import timedelta

# Configure logging
logger = logging.getLogger(__name__)

# JWT Configuration
SECRET_KEY = settings.SECRET_KEY
ALGORITHM = "HS256"

# Initialize Stripe
stripe.api_key = settings.STRIPE_SECRET_KEY

# Authentication Utilities
def get_current_user(token: str):
    """
    Retrieves the current user based on the JWT token.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: int = int(payload.get("sub"))
        role: str = payload.get("role")
        if user_id is None or role is None:
            raise credentials_exception
        return {"id": user_id, "role": role}
    except (JWTError, ValueError):
        raise credentials_exception


def get_admin_user(current_user: Annotated[dict, Depends(get_current_user)]):
    """
    Ensures that the current user is an admin.
    """
    if current_user["role"] != "ADMIN":
        raise HTTPException(status_code=403, detail="Insufficient permissions.")
    return current_user


# Payment Processing Functions
async def process_payment(payment_data: PaymentCreate, amount: float, currency: str, order_id: int, user_id: int) -> dict:
    """
    Processes the payment using Stripe.
    """
    payment_method = PaymentMethod.STRIPE
    payment_result = await process_stripe_payment(payment_data, amount, currency, order_id)
    payment_result["payment_method"] = payment_method
    payment_result["order_id"] = order_id
    payment_result["user_id"] = user_id
    return payment_result


async def process_stripe_payment(payment_data: PaymentCreate, amount: float, currency: str, order_id: int) -> dict:
    """
    Processes payment using Stripe with PaymentMethod ID in Test Mode.
    """
    logger.info(f"Processing Stripe payment for Order ID {order_id}")
    try:
        payment_intent = stripe.PaymentIntent.create(
            amount=int(amount * 100),  # Convert to smallest currency unit (e.g., cents)
            currency=currency.lower(),
            payment_method=payment_data.payment_method_id,  # Use PaymentMethod ID
            confirmation_method='automatic',
            confirm=True,
            payment_method_types=['card'],  # Specify 'card' payment method
            description=f"Payment for Order {order_id}",
            metadata={
                "order_id": str(order_id)
            }
        )
        if payment_intent.status == "succeeded":
            return {
                "status": PaymentStatus.SUCCESS,
                "transaction_id": payment_intent.id,
            }
        elif payment_intent.status in ["requires_action", "requires_confirmation"]:
            # Payment requires additional actions (e.g., 3D Secure)
            return {
                "status": PaymentStatus.PENDING,
                "transaction_id": payment_intent.id,
            }
        else:
            logger.error(f"Stripe payment failed: {payment_intent.last_payment_error}")
            return {
                "status": PaymentStatus.FAILURE,
                "failure_reason": payment_intent.last_payment_error.message if payment_intent.last_payment_error else "Unknown error",
            }
    except stripe.error.CardError as e:
        # Since it's a decline, stripe.error.CardError will be caught
        logger.error(f"Stripe CardError: {e.user_message}")
        return {
            "status": PaymentStatus.FAILURE,
            "failure_reason": e.user_message,
        }
    except stripe.error.StripeError as e:
        # Handle other Stripe errors
        logger.error(f"Stripe Error: {e.user_message}")
        return {
            "status": PaymentStatus.FAILURE,
            "failure_reason": e.user_message,
        }
    except Exception as e:
        # Handle any other exceptions
        logger.error(f"Stripe payment failed: {e}")
        return {
            "status": PaymentStatus.FAILURE,
            "failure_reason": str(e),
        }


# Mapping Functions
def map_payment_status_to_protobuf(status: PaymentStatus) -> payment_pb2.PaymentStatus:
    mapping = {
        PaymentStatus.SUCCESS: payment_pb2.PaymentStatus.SUCCESS,
        PaymentStatus.FAILURE: payment_pb2.PaymentStatus.FAILURE,
        PaymentStatus.PENDING: payment_pb2.PaymentStatus.PENDING,
    }
    return mapping.get(status, payment_pb2.PaymentStatus.PENDING)


# Extract Order ID from Metadata
def extract_order_id_from_metadata(metadata: dict) -> int:
    """
    Extracts the order_id from the payment metadata.
    """
    try:
        return int(metadata.get("order_id", 0))
    except ValueError:
        logger.error(f"Invalid order_id in metadata: {metadata.get('order_id')}")
        return 0


def map_protobuf_to_python_order_status(protobuf_status: order_pb2.OrderStatus) -> PythonOrderStatus:
    """
    Maps Protobuf OrderStatus enum to Python OrderStatus enum.

    Args:
        protobuf_status (OrderStatus): Protobuf OrderStatus enum.

    Returns:
        PythonOrderStatus: Mapped Python OrderStatus enum.
    """
    mapping = {
        order_pb2.OrderStatus.NEW: PythonOrderStatus.NEW,
        order_pb2.OrderStatus.PROCESSING: PythonOrderStatus.PROCESSING,
        order_pb2.OrderStatus.CONFIRMED: PythonOrderStatus.CONFIRMED,
        order_pb2.OrderStatus.SHIPPED: PythonOrderStatus.SHIPPED,
        order_pb2.OrderStatus.CANCELLED: PythonOrderStatus.CANCELLED,
        order_pb2.OrderStatus.COMPLETED: PythonOrderStatus.COMPLETED,
    }
    if protobuf_status in mapping:
        return mapping[protobuf_status]
    else:
        raise ValueError(f"Unknown Protobuf OrderStatus: {protobuf_status}")