# payment_service/app/main.py

from fastapi import FastAPI, Depends, HTTPException, Request, status
from sqlmodel import Session, select
from contextlib import asynccontextmanager
import asyncio
import logging
from typing import Annotated
from aiokafka import AIOKafkaProducer, AIOKafkaConsumer
import stripe
from app import settings
from datetime import datetime

from app.models import (
    Payment,
    PaymentCreate,
    PaymentRead,
    PaymentStatus,
    OrderPayment,
)
from app.db import engine, create_db_and_tables, get_session
from app.utils import (
    get_current_user,
    get_admin_user,
    process_payment,
    map_payment_status_to_protobuf,
    map_protobuf_to_python_order_status,
    extract_order_id_from_metadata,
)
from app import payment_pb2, order_pb2, user_pb2  # Importing Protobuf messages

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Stripe
stripe.api_key = settings.STRIPE_SECRET_KEY


# Kafka Configuration
KAFKA_BOOTSTRAP_SERVERS = "broker:19092"  # Kafka broker address
KAFKA_TOPIC = ["order_data", "user_loginlogout"]               # Topic to consume from
GROUP_ID = "payment_group"                    # Consumer group ID


# In-memory session store to track logged-in users
# Key: user_id, Value: user details
user_sessions: dict[int, dict] = {}


async def consume_messages():
    """Function to consume messages from Kafka topics."""
    
    # Initialize Kafka Consumer
    consumer = AIOKafkaConsumer(
        *KAFKA_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        group_id=GROUP_ID,
        auto_offset_reset="earliest",
        enable_auto_commit=True
    )

    try:
        await consumer.start()
        logger.info("Kafka consumer started and connected to the broker.")
        async for msg in consumer:
            topic = msg.topic
            message = msg.value
            logger.info(f"Received message from {topic}: {message}")
            if topic == 'order_data':
                await handle_order_message(message)
            elif topic == "user_loginlogout":
                await handle_user_loginlogout(message)
    except Exception as e:
        logger.error(f"Error in Kafka consumer: {e}")
    finally:
        await consumer.stop()


async def handle_order_message(message: bytes):
    """Handles messages from the Order Service."""
    try:
        order_message = order_pb2.Order()
        order_message.ParseFromString(message)
        logger.info(f"\n\nConsumer Deserialized data: {order_message}")

        order_status = map_protobuf_to_python_order_status(order_message.order_status)

        with Session(engine) as session:
            # Check if the order already exists
            existing_order = session.get(OrderPayment, order_message.order_id)
            if existing_order:
                # Update existing order
                existing_order.user_id = order_message.user_id
                existing_order.order_total = order_message.order_total
                existing_order.currency = "usd"  # Set to default
                existing_order.customer_address = order_message.customer_address
                # Map Protobuf enum to Python enum
                existing_order.status = order_status  # Assuming enum names match
                existing_order.timestamp = datetime.utcnow()  # Use current time
                session.add(existing_order)
                logger.info(f"Updated Order ID {order_message.order_id} in the database.")
            else:
                # Create new order
                new_order = OrderPayment(
                    order_id=order_message.order_id,
                    user_id=order_message.user_id,
                    order_total=order_message.order_total,
                    currency="usd",  # Set to default
                    customer_address=order_message.customer_address,
                    status=order_status,  # Assuming enum names match
                    timestamp=datetime.utcnow(),  # Use current time
                )
                session.add(new_order)
                logger.info(f"Added Order ID {order_message.order_id} to the database.")
            session.commit()
    except Exception as e:
        logger.error(f"Error handling order message: {e}")


async def handle_user_loginlogout(message: bytes):
    """Handles user login/logout events from the User Service."""
    try:
        user_event = user_pb2.User()
        user_event.ParseFromString(message)
        logger.info(f"\n\nConsumer Deserialized data: {user_event}")

        user_id = user_event.id
        action = user_event.action

        if action == user_pb2.ActionType.LOGIN:
            # Add user to in-memory session store
            user_sessions[user_id] = {
                "role": user_event.role,  # Assuming enum names match
                "user_name": user_event.user_name,
                "user_email": user_event.user_email,
                "user_cellno": user_event.user_cellno,
                "user_address": user_event.user_address,
            }
            logger.info(f"User ID {user_id} logged in. Session updated.")
        elif action == user_pb2.ActionType.LOGOUT:
            # Remove user from in-memory session store
            if user_id in user_sessions:
                del user_sessions[user_id]
                logger.info(f"User ID {user_id} logged out. Session removed.")
            else:
                logger.warning(f"Logout event received for unknown User ID {user_id}.")
        else:
            logger.warning(f"Unhandled user action: {action}")
    except Exception as e:
        logger.error(f"Error handling user login/logout message: {e}")


async def produce_message():
    producer = AIOKafkaProducer(
        bootstrap_servers='broker:19092')
    # Get cluster layout and initial topic/partition leadership information
    await producer.start()
    try:
        # Produce message
        yield producer
    finally:
        # Wait for all pending messages to be delivered or expire.
        await producer.stop()


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    print("Database created and tables ensured")

    # Start Kafka consumer as a background task
    consumer_task = asyncio.create_task(consume_messages())
    logger.info("Kafka consumer task started.")

    try:
        yield
    finally:
        # Cancel the consumer task on shutdown
        consumer_task.cancel()
        try:
            await consumer_task
        except asyncio.CancelledError:
            logger.info("Kafka consumer task has been cancelled.")


app = FastAPI(
    lifespan=lifespan,
    title="Payment Service API",
    version="1.0.0",
    # servers=[
    #     {
    #         "url": "http://localhost:8000",  # Update as needed
    #         "description": "Development Server"
    #     }
    # ]
)



@app.post("/payments", response_model=PaymentRead)
async def create_payment(
    payment_data: PaymentCreate,
    current_user: Annotated[dict, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
    producer: Annotated[AIOKafkaProducer, Depends(produce_message)]
):
    user_id = current_user["id"]
    logger.info(f"user_id: {user_id}")

    # Verify if the user is currently logged in by checking in-memory session store
    if user_id not in user_sessions:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User is not currently logged in.",
        )

    # Fetch the latest eligible order for the user
    eligible_order = session.exec(
        select(OrderPayment)
        .where(
            (OrderPayment.user_id == user_id) &
            (OrderPayment.status.in_(["New", "CONFIRMED", "Confirmed"]))
        )
        .order_by(OrderPayment.timestamp.desc())
    ).first()

    if not eligible_order:
        raise HTTPException(status_code=404, detail="No eligible orders found for payment.")

    # Proceed only if the order's user_id matches the current user
    if eligible_order.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Order does not belong to the authenticated user."
        )

    amount = eligible_order.order_total
    currency = "usd"  # Fixed currency as per requirement

    # Process payment
    payment_result = await process_payment(payment_data, amount, currency, eligible_order.order_id, user_id)

    # Create Payment record
    payment = Payment(
        order_id=eligible_order.order_id,
        user_id=user_id,
        amount=amount,
        currency=currency,
        status=payment_result["status"],
        payment_method=payment_result["payment_method"],
        transaction_id=payment_result.get("transaction_id"),
        failure_reason=payment_result.get("failure_reason"),
    )

    # Save payment record
    session.add(payment)
    session.commit()
    session.refresh(payment)
    logger.info(f"Payment record created with ID {payment.payment_id}")

    # Update order status if payment is successful
    if payment.status == PaymentStatus.SUCCESS:
        eligible_order.status = "Processing"
        session.add(eligible_order)
        session.commit()
        logger.info(f"Order ID {eligible_order.order_id} status updated to Processing.")

    # Send payment result to Payment Data topic via Kafka
    await send_payment_message(payment, producer)

    return payment


@app.get("/payments/{payment_id}", response_model=PaymentRead)
async def get_payment(
    payment_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
):
    payment = session.get(Payment, payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment.user_id != current_user["id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this payment"
        )
    return payment


@app.get("/payments", response_model=list[PaymentRead])
async def get_payments(
    current_user: Annotated[dict, Depends(get_admin_user)],
    session: Annotated[Session, Depends(get_session)],
):
    payments = session.exec(select(Payment)).all()
    return payments

@app.get("/orders_for_payments", response_model=list[OrderPayment])
async def get_orders_for_payments(
    #current_user: Annotated[dict, Depends(get_admin_user)],
    session: Annotated[Session, Depends(get_session)],
):
    orders_for_payments = session.exec(select(OrderPayment)).all()
    return orders_for_payments


@app.post("/stripe/webhook/", status_code=200)
async def stripe_webhook(request: Request):
    """
    Endpoint to handle Stripe webhook events.
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        # Invalid payload
        logger.error("Invalid payload")
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        # Invalid signature
        logger.error("Invalid signature")
        raise HTTPException(status_code=400, detail="Invalid signature")

    # Handle the event
    event_type = event["type"]
    data_object = event["data"]["object"]

    if event_type == "payment_intent.succeeded":
        await handle_successful_payment(data_object)
    elif event_type == "payment_intent.payment_failed":
        await handle_failed_payment(data_object)
    else:
        logger.warning(f"Unhandled event type {event_type}")

    return {"status": "success"}


async def handle_successful_payment(payment_intent):
    """
    Handle successful payment intent.
    """
    logger.info(f"Handling successful payment intent: {payment_intent.id}")
    order_id = extract_order_id_from_metadata(payment_intent.metadata)
    if order_id == 0:
        logger.error("Invalid order_id extracted from metadata.")
        return

    with Session(engine) as session:
        payment = session.exec(
            select(Payment).where(Payment.transaction_id == payment_intent.id)
        ).first()
        if payment:
            payment.status = PaymentStatus.SUCCESS
            session.add(payment)
            session.commit()
            session.refresh(payment)
            logger.info(f"Payment ID {payment.payment_id} marked as SUCCESS")
            # Notify Payment Data topic
            await send_payment_message(payment)
        else:
            logger.error(f"No payment record found for Transaction ID {payment_intent.id}")


async def handle_failed_payment(payment_intent):
    """
    Handle failed payment intent.
    """
    logger.info(f"Handling failed payment intent: {payment_intent.id}")
    failure_reason = (
        payment_intent.last_payment_error.message
        if payment_intent.last_payment_error
        else "Unknown error"
    )
    with Session(engine) as session:
        payment = session.exec(
            select(Payment).where(Payment.transaction_id == payment_intent.id)
        ).first()
        if payment:
            payment.status = PaymentStatus.FAILURE
            payment.failure_reason = failure_reason
            session.add(payment)
            session.commit()
            session.refresh(payment)
            logger.info(f"Payment ID {payment.payment_id} marked as FAILURE")
            # Notify Payment Data topic
            await send_payment_message(payment)
        else:
            logger.error(f"No payment record found for Transaction ID {payment_intent.id}")


async def send_payment_message(payment: Payment,
                               producer: AIOKafkaProducer) :
    """
    Sends payment status messages to the Payment Data topic via Kafka.
    """
    try:
        # Create a Protobuf PaymentStatusMessage
        payment_status_message = payment_pb2.PaymentStatusMessage(
            payment_id=payment.payment_id,
            order_id=payment.order_id,
            user_id=payment.user_id,
            status=map_payment_status_to_protobuf(payment.status),
            transaction_id=payment.transaction_id or "",
            failure_reason=payment.failure_reason or "",
        )
        message = payment_status_message.SerializeToString()
        await producer.send_and_wait("payment_data", message)
        logger.info(
            f"Sent payment status message for Payment ID {payment.payment_id} to payment_data topic."
        )
    except Exception as e:
        logger.error(f"Failed to send payment status message: {e}")