# notification_api/app/main.py

from fastapi import FastAPI, Depends, Request
from fastapi.responses import JSONResponse
from sqlmodel import Session, select
from typing import Annotated
from aiokafka import AIOKafkaConsumer
from contextlib import asynccontextmanager
from topic_generator.create_topic import create_kafka_topic
import asyncio
import logging
from .models import NotificationLog, NotificationType, User, PaymentStatus
from .db import engine, create_db_and_tables, get_session
from .utils import send_email, send_sms
from app import inventory_pb2, order_pb2, user_pb2, payment_pb2
from .utils import (
    map_protobuf_to_python_order_status,
    map_protobuf_to_python_item_status,
    map_protobuf_to_python_payment_status,
)  # Implement this mapping

from datetime import datetime


# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Kafka Configuration
KAFKA_BOOTSTRAP_SERVERS = "broker:19092"  # Update as per your setup
CONSUME_TOPICS = ["order_data", "stock_items", "user_data", "payment_data"]
GROUP_ID = "notification_service"


# Handler Functions

async def handle_user_data(user_event: user_pb2.User):
    """
    Processes user data messages to update the User table.
    """
    user_id = user_event.id
    action = user_event.action
    name = user_event.user_name
    email = user_event.user_email
    phone_number = user_event.user_cellno
    address = user_event.user_address

    with Session(engine) as session:
        user = session.exec(select(User).where(User.id == user_id)).first()
        
        if action in [user_pb2.ActionType.CREATE, user_pb2.ActionType.UPDATE]:
            if user:
                # Update existing user details
                user.name = name
                user.email = email
                user.cell_number = phone_number
                user.address = address
                session.add(user)
                logger.info(f"Updated User ID {user_id} in Notification Service.")
            else:
                # Insert new user
                new_user = User(
                    id=user_id,
                    name=name,
                    email=email,
                    cell_number=phone_number,
                    address=address
                )
                session.add(new_user)
                logger.info(f"Added new User ID {user_id} to Notification Service.")
        elif action == user_pb2.ActionType.DELETE:
            if user:
                session.delete(user)
                logger.info(f"Deleted User ID {user_id} from Notification Service.")
            else:
                logger.warning(f"User ID {user_id} not found in Notification Service for deletion.")
        else:
            logger.warning(f"Unknown action {action} for user data.")

        session.commit()

async def handle_order_data(order_update: order_pb2.Order):
    """
    Processes order data messages to send notifications to users.
    """
    order_id = order_update.order_id
    status = map_protobuf_to_python_order_status(order_update.order_status)
    user_id = order_update.user_id
    action = order_update.item_action

    # Fetch user details from the User table
    with Session(engine) as session:
        user = session.exec(select(User).where(User.id == user_id)).first()
        if not user:
            logger.error(f"User ID {user_id} not found in Notification Service.")
            return  # Optionally, handle this scenario differently

    # Determine notification content based on order status
    if status == "COMPLETED":
        subject = f"Your Order {order_id} has been Completed!"
        body = f"Dear Customer,\n\nYour order #{order_id} has been successfully completed. Thank you for shopping with us!"
    elif status == "CANCELLED":
        subject = f"Your Order {order_id} has been Cancelled."
        body = f"Dear Customer,\n\nYour order #{order_id} has been cancelled as per your request. If you have any questions, feel free to contact our support."
    elif status == "CONFIRMED" and action == order_pb2.ActionType.CREATE:
        subject = f"Your Order {order_id} has been Confirmed."
        body = f"Dear Customer,\n\nYour order #{order_id} is confirmed. Please check your order details for more information."
    elif status == "CONFIRMED" and action == order_pb2.ActionType.UPDATE:
        subject = f"Your Order {order_id} has been Updated."
        body = f"Dear Customer,\n\nYour order #{order_id} has been updated. Please check your order details for more information."
    elif status == "PROCESSING" and action == order_pb2.ActionType.UPDATE:
        subject = f"Payment received for Order {order_id}."
        body = f'''Dear Customer,\n\nYour order #{order_id} is in processing and will be shipped in 2 working days.
        Please check your order details for more information.'''
    elif status == "CONFIRMED" and action == order_pb2.ActionType.DELETE:
        subject = f"Your Order {order_id} has been Cancelled."
        body = f"Dear Customer,\n\nYour order #{order_id} has been Cancelled on your request."
    else:
        subject = f"Your Order {order_id} has been Updated."
        body = f"Dear Customer,\n\nYour order #{order_id} has been updated. Please check your order details for more information."

    # Send Email
    email_sent = send_email(user.email, subject, body)

    # Send SMS
    sms_sent = send_sms(user.cell_number, body)

    logger.info(f"Notifications sent for Order ID {order_id}: Email={email_sent}, SMS={sms_sent}")

    # Log notifications
    log_notification(NotificationType.EMAIL, user.email, subject + " " + body, "Sent")
    log_notification(NotificationType.SMS, user.cell_number, body, "Sent")

async def handle_stock_items(inventory_update: inventory_pb2.Item):
    """
    Processes inventory update messages to send notifications to all users.
    """
    product_name = inventory_update.item_name
    product_category = inventory_update.item_category
    product_price = inventory_update.item_price
    product_quantity = inventory_update.item_quantity
    product_status = map_protobuf_to_python_item_status(inventory_update.item_status)
    product_status_change = inventory_update.item_status_change
    product_price_drop = inventory_update.item_price_drop
    action_type = inventory_update.item_action

    subject = ""
    body = ""

    if action_type == inventory_pb2.ActionType.CREATE:
        subject = f"New Product Added: {product_name}"
        body = f"Exciting News! We've added a new product to our inventory: {product_name} in {product_category} category, priced at ${product_price}."
    elif action_type == inventory_pb2.ActionType.UPDATE:
        # Assuming inventory_update has previous_price or similar to detect price drop
        if product_status_change == True and product_status == "In Stock":
            subject = f"{product_name} is Now Back in Stock!"
            body = f"Good news! {product_name} has been restocked. We now have {product_quantity} units available."
        elif product_price_drop > 0:
            subject = f"Price Drop Alert for {product_name}"
            body = f"The price of {product_name} has dropped to ${product_price}. Don't miss out!" 
    elif action_type == inventory_pb2.ActionType.DELETE:
        subject = f"Product Removed: {product_name}"
        body = f"We regret to inform you that {product_name} has been removed from our inventory."

    if subject and body:
        # Fetch all users from the User table
        with Session(engine) as session:
            users = session.exec(select(User)).all()
            if not users:
                logger.warning("No users found in Notification Service to send inventory notifications.")
                return

            for user in users:
                # Send Email
                email_sent = send_email(user.email, subject, body)

                # Send SMS
                sms_sent = send_sms(user.cell_number, body)

                logger.info(f"Notifications sent for Inventory Action on {product_name}: Email={email_sent}, SMS={sms_sent}")

                # Log notifications
                log_notification(NotificationType.EMAIL, user.email, subject + " " + body, "Sent")
                log_notification(NotificationType.SMS, user.cell_number, body, "Sent")

# notification_api/app/main.py


async def handle_payment_data(payment_message: payment_pb2.PaymentStatusMessage):
    """
    Processes payment status messages to send notifications on payment failure.
    """
    order_id = payment_message.order_id
    user_id = payment_message.user_id
    status = map_protobuf_to_python_payment_status(payment_message.status)
    transaction_id = payment_message.transaction_id
    failure_reason = payment_message.failure_reason

    # Fetch user details from the User table
    with Session(engine) as session:
        user = session.exec(select(User).where(User.id == user_id)).first()
        if not user:
            logger.error(f"User ID {user_id} not found in Notification Service.")
            return  # Optionally, handle this scenario differently

    # If payment failed, send notifications
    if status == PaymentStatus.FAILURE:
        subject = f"Payment Failed for Order {order_id}"
        body = (
            f"Dear {user.name},\n\n"
            f"We regret to inform you that your payment for Order #{order_id} has failed.\n"
            f"Reason: {failure_reason}\n"
            f"Please try again or contact our support for assistance.\n\n"
            f"Transaction ID: {transaction_id}"
        )

        # Send Email
        email_sent = send_email(user.email, subject, body)

        # Send SMS
        sms_sent = send_sms(user.cell_number, body)

        logger.info(f"Payment failure notifications sent for Order ID {order_id}: Email={email_sent}, SMS={sms_sent}")

        # Log notifications
        log_notification(NotificationType.EMAIL, user.email, subject + " " + body, "Sent")
        log_notification(NotificationType.SMS, user.cell_number, body, "Sent")
    else:
        logger.info(f"No notification sent for payment status: {status}")


def log_notification(notification_type: NotificationType, recipient: str, message: str, status: str):
    """
    Logs the sent notification to the database.
    
    Args:
        notification_type (NotificationType): Type of notification (EMAIL/SMS).
        recipient (str): Recipient's contact information.
        message (str): Notification message content.
        status (str): Status of the notification (e.g., Sent, Failed).
    """
    with Session(engine) as session:
        log = NotificationLog(
            notification_type=notification_type,
            recipient=recipient,
            message=message,
            status=status,
            timestamp=datetime.utcnow().timestamp()
        )
        session.add(log)
        session.commit()
        logger.info(f"Logged {notification_type} notification to {recipient}.")

async def consume_messages():
    """Consumes messages from Kafka topics and triggers notifications."""

    # Initialize Kafka Consumer
    consumer = AIOKafkaConsumer(
        *CONSUME_TOPICS,
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

            if topic == "user_data":
                user_event = user_pb2.User()
                user_event.ParseFromString(message)
                logger.info(f"\n\nConsumer Deserialized data: {user_event}")
                await handle_user_data(user_event)

            elif topic == "order_data":
                order_update = order_pb2.Order()
                order_update.ParseFromString(message)
                logger.info(f"\n\nConsumer Deserialized data: {order_update}")
                await handle_order_data(order_update)

            elif topic == "stock_items":
                inventory_update = inventory_pb2.Item()
                inventory_update.ParseFromString(message)
                logger.info(f"\n\nConsumer Deserialized data: {inventory_update}")
                await handle_stock_items(inventory_update)

            elif topic == "payment_data":
                payment_message = payment_pb2.PaymentStatusMessage()
                payment_message.ParseFromString(message)
                logger.info(f"\n\nConsumer Deserialized data: {payment_message}")
                await handle_payment_data(payment_message)    

            else:
                logger.warning(f"Unhandled topic: {topic}")

    except Exception as e:
        logger.error(f"Error in consumer: {e}")

    finally:
        await consumer.stop()
        logger.info("Kafka consumer stopped.")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manages application startup and shutdown.
    """
    # Initialize the database and create tables
    create_db_and_tables()
    logger.info("Database created and tables ensured.")
    
    # Create Kafka topics if they don't exist
    # await create_kafka_topic(*CONSUME_TOPICS, num_partitions=1, replication_factor=1)
    
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

app = FastAPI(lifespan=lifespan, title="Notification Service", version="1.0.0")

@app.get("/notifications", response_model=list[NotificationLog])
def get_notifications(session: Annotated[Session, Depends(get_session)]):
    """
    Retrieves all notification logs.
    
    Args:
        session (Session): Database session.
        
    Returns:
        List[NotificationLog]: List of all notification logs.
    """
    notifications = session.exec(select(NotificationLog)).all()
    return notifications

@app.get("/users", response_model=list[User])
def get_users(session: Annotated[Session, Depends(get_session)]):
    """
    Retrieves all saved users for Notification Service.
    
    Args:
        session (Session): Database session.
        
    Returns:
        List[User]: List of all users.
    """
    users = session.exec(select(User)).all()
    return users


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Catches all unhandled exceptions and returns a 500 Internal Server Error.
    """
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )