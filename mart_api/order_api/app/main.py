# order_api/app/main.py

from fastapi import FastAPI, Depends, HTTPException
from sqlmodel import Session, select
from typing import Annotated
from aiokafka import AIOKafkaProducer, AIOKafkaConsumer
from contextlib import asynccontextmanager
from topic_generator.create_topic import create_kafka_topic
import asyncio
import logging
from .models import (
    OrderCreate, Orders, OrderOutput, ProductInventory, 
    OrderUpdate, OrderStatusUpdate, PaymentStatus
    )
from .db import engine, create_db_and_tables, get_session
from app import inventory_pb2, order_pb2, user_pb2, payment_pb2  # Adjust import as needed
from app.session_store import user_sessions
from app.utils import (
    map_python_to_protobuf_order_status,
    get_current_user,
    get_current_admin_user,
    map_protobuf_to_payment_status
) 


# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Kafka Configuration
KAFKA_BOOTSTRAP_SERVERS = "broker:19092"  # Kafka broker address
KAFKA_TOPIC = ["stock_items", "user_data", "user_loginlogout", "payment_data"]               # Topic to consume from
GROUP_ID = "inventory_order"                    # Consumer group ID




async def consume_messages():
    """Function to consume messages continuously."""

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
            logger.info(f"Received message from {topic}: {msg.value}")

            if topic == "stock_items":
                inventory_update = inventory_pb2.Item()
                inventory_update.ParseFromString(msg.value)
                logger.info(f"\n\n Consumer Deserialized data: {inventory_update}")

                with Session(engine) as session:
                    if inventory_update.item_action in [inventory_pb2.ActionType.CREATE,inventory_pb2.ActionType.UPDATE] : 
                        product = session.get(ProductInventory, inventory_update.item_id)
                        if product:
                            product.product_price = inventory_update.item_price
                            product.product_quantity = inventory_update.item_quantity
                        else:
                            product = ProductInventory(
                                product_id=inventory_update.item_id,
                                product_price=inventory_update.item_price,
                                product_quantity=inventory_update.item_quantity
                            )
                            session.add(product)
                        session.commit()

                    elif inventory_update.item_action == inventory_pb2.ActionType.DELETE:
                        product = session.get(ProductInventory, inventory_update.item_id)
                        if product:
                            session.delete(product)
                            session.commit()
                        else:
                            logger.info(f"Product with ID {inventory_update.item_id} not found.")  

            elif topic == "user_loginlogout":
                user_event = user_pb2.User()
                user_event.ParseFromString(msg.value)
                logger.info(f"Consumer Deserialized user event: {user_event}")

                user_id = user_event.id
                action = user_event.action

                if action == user_pb2.ActionType.LOGIN:
                    # Add user to session store
                    user_sessions[user_id] = {
                        'role': user_event.role,
                        'user_name': user_event.user_name,
                        'user_email': user_event.user_email,
                        'user_cellno': user_event.user_cellno,
                        'user_address': user_event.user_address
                    }
                    logger.info(f"{user_sessions}")
                    logger.info(f"User ID {user_id} logged in. Session updated.")
                elif action == user_pb2.ActionType.LOGOUT:
                    # Remove user from session store
                    if user_id in user_sessions:
                        del user_sessions[user_id]
                        logger.info(f"{user_sessions}")
                        logger.info(f"User ID {user_id} logged out. Session updated.")
                else:
                    logger.warning(f"Unknown user action: {action}")

            elif topic == "user_data":
                # Handle user data updates if necessary
                user_event = user_pb2.User()
                user_event.ParseFromString(msg.value)
                logger.info(f"Consumer Deserialized user data event: {user_event}")

                user_id = user_event.id
                action = user_event.action

                if action in (user_pb2.ActionType.CREATE, user_pb2.ActionType.UPDATE):
                    # Update user information in session store if user is logged in
                    if user_id in user_sessions:
                        user_sessions[user_id].update({
                            'role': user_event.role,
                            'user_name': user_event.user_name,
                            'user_email': user_event.user_email,
                            'user_cellno': user_event.user_cellno,
                            'user_address': user_event.user_address
                        })
                        logger.info(f"User ID {user_id} data updated in session store.")
                elif action == user_pb2.ActionType.DELETE:
                    # Remove user from session store if user is deleted
                    if user_id in user_sessions:
                        del user_sessions[user_id]
                        logger.info(f"User ID {user_id} deleted. Session updated.")
                else:
                    logger.warning(f"Unknown user action: {action}")

            elif topic == "payment_data":
                payment_message = payment_pb2.PaymentStatusMessage()
                payment_message.ParseFromString(msg.value)
                logger.info(f"Consumer Deserialized payment message: {payment_message}")

                status = map_protobuf_to_payment_status(payment_message.status)

                # Check if the payment status is SUCCESS
                if status == PaymentStatus.SUCCESS:
                    order_id = payment_message.order_id
                    with Session(engine) as session:
                        # Fetch the corresponding order
                        order_db = session.get(Orders, order_id)
                        if not order_db:
                            logger.error(f"Order ID {order_id} not found in the database.")
                            return

                        # Update the order status to PROCESSING
                        order_db.order_status = 'Processing'  # Update status to 'Processing'
                        session.add(order_db)
                        session.commit()
                        logger.info(f"Order ID {order_id} status updated to 'Processing'.")

                        # Produce updated order message to 'order_data' topic
                        await produce_order_update_message(order_db)
                else:
                    logger.info(f"Ignoring payment message with status: {payment_message.status}")
                   
    except Exception as e:
        logger.error(f"Error in consumer: {e}")

    finally:
        await consumer.stop()
        logger.info("Kafka consumer stopped.")        


async def produce_order_update_message(order_db: Orders):
    """Produces an updated order message to the 'order_data' topic."""
    try:
        # Map Python order status to Protobuf enum
        protobuf_order_status = map_python_to_protobuf_order_status(order_db.order_status)

        # Create the Protobuf message
        orderdb_protobuf = order_pb2.Order(
            order_id=order_db.order_id,
            user_id=order_db.user_id,
            customer_name=order_db.customer_name,
            customer_email=order_db.customer_email,
            customer_cellno=order_db.customer_cellno,
            customer_address=order_db.customer_address,
            product_id=order_db.product_id,
            order_quantity=order_db.order_quantity,
            product_price=order_db.product_price,
            order_total=order_db.order_total,
            order_status=protobuf_order_status,
            item_action=order_pb2.ActionType.UPDATE
        )

        # Create a Kafka producer
        producer = AIOKafkaProducer(bootstrap_servers='broker:19092')
        await producer.start()
        try:
            # Send the message to 'order_data' topic
            await producer.send_and_wait('order_data', orderdb_protobuf.SerializeToString())
            logger.info(f"Produced updated order message for Order ID {order_db.order_id}.")
        finally:
            await producer.stop()
    except Exception as e:
        logger.error(f"Error producing order update message: {e}")


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
    """
    Lifespan event to manage application startup and shutdown.
    """
    # Initialize the database and create tables
    create_db_and_tables()
    logger.info("Database created and tables ensured.")

    # Create Kafka topic
    await create_kafka_topic('payment_data')
    #await create_kafka_topic('order_items')

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


app = FastAPI(lifespan=lifespan, title="Order Service", version="1.0.0")

@app.get("/orders", response_model=list[Orders])
def get_orders(session: Annotated[Session, Depends(get_session)],
               current_user: Annotated[dict, Depends(get_current_admin_user)]):
    orders = session.exec(select(Orders)).all()
    return orders

@app.get("/orders/me", response_model=list[Orders])
def get_my_orders(
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[dict,Depends(get_current_user)],
):
    """
    Retrieve a list of orders belonging to the currently authenticated user.
    """
    user_id = current_user.get('user_id')  # Adjust the key based on your user model
    if user_id is None:
        raise HTTPException(status_code=400, detail="User ID not found in token.")
    
    orders = session.exec(select(Orders).where(Orders.user_id == user_id)).all()
    return orders

@app.get("/product inventory/", response_model=list[ProductInventory])
def get_product_inventory(session: Annotated[Session, Depends(get_session)],
                          current_user: Annotated[dict, Depends(get_current_admin_user)]):
    product_inventory = session.exec(select(ProductInventory)).all()
    return product_inventory


@app.post("/orders", response_model=OrderOutput)
async def create_order(
    order: OrderCreate,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[dict,Depends(get_current_user)],
    producer: Annotated[AIOKafkaProducer, Depends(produce_message)]
):
    product = session.get(ProductInventory, order.product_id)
    if not product:
        raise HTTPException(status_code=400, detail="Product not found in inventory")
    if order.order_quantity > product.product_quantity:
        raise HTTPException(status_code=400, detail="Insufficient product quantity in inventory")
  
    order_db = Orders(
        user_id=current_user['user_id'],
        customer_name=order.customer_name,
        customer_email=order.customer_email,
        customer_cellno=order.customer_cellno,
        customer_address=order.customer_address,
        product_id=order.product_id,
        order_quantity=order.order_quantity,
        product_price=product.product_price,
        order_total=product.product_price * order.order_quantity,
        order_status='Confirmed'
    )

    product.product_quantity -= order.order_quantity

    session.add(order_db)
    session.add(product)
    session.commit()
    session.refresh(order_db)


    # Map Python enum to Protobuf enum
    protobuf_order_status = map_python_to_protobuf_order_status(order_db.order_status)
    # Produce message
    orderdb_protobuf = order_pb2.Order(
        order_id=order_db.order_id,
        user_id=order_db.user_id,
        customer_name=order_db.customer_name,
        customer_email=order_db.customer_email,
        customer_cellno=order_db.customer_cellno,
        customer_address=order_db.customer_address,
        product_id=order_db.product_id,
        order_quantity=order_db.order_quantity,
        product_price=order_db.product_price,
        order_total=order_db.order_total,
        order_status=protobuf_order_status,
        item_action=order_pb2.ActionType.CREATE
    )
    await producer.send_and_wait('order_data', orderdb_protobuf.SerializeToString())

    return order_db


@app.patch("/orders/{order_id}", response_model=OrderOutput)
async def update_order(
    order_id: int,
    updated_order: OrderUpdate,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[dict,Depends(get_current_user)],
    producer: Annotated[AIOKafkaProducer, Depends(produce_message)]
):
    order_db = session.get(Orders, order_id)
    if not order_db:
        raise HTTPException(status_code=404, detail="Order not found")
    
    user_id = current_user.get('user_id')
    if user_id is None:
        raise HTTPException(status_code=400, detail="User ID not found in token.")
    if order_db.user_id != user_id:
        raise HTTPException(status_code=403, detail="You are not authorized to update this order.")

    product = session.get(ProductInventory, order_db.product_id)
    if not product:
        raise HTTPException(status_code=400, detail="Product not found in inventory")
    
    # Extract the fields to update
    order_data = updated_order.model_dump(exclude_unset=True)
    logger.info(f"Fields to update for Order ID {order_id}: {order_data}")

    # Handle order_quantity update separately to manage inventory
    if 'order_quantity' in order_data:
        new_quantity = order_data['order_quantity']
        if new_quantity < 1:
            logger.error("Order quantity must be at least 1.")
            raise HTTPException(status_code=400, detail="Order quantity must be at least 1")
        
        quantity_diff = new_quantity - order_db.order_quantity
        logger.info(f"Quantity difference for Order ID {order_id}: {quantity_diff}")

        if quantity_diff > 0:
            if quantity_diff > product.product_quantity:
                logger.error("Insufficient product quantity in inventory.")
                raise HTTPException(status_code=400, detail="Insufficient product quantity in inventory")
            # Deduct the additional quantity from inventory
            product.product_quantity -= quantity_diff
            logger.info(f"Deducted {quantity_diff} from Product ID {product.product_id}. New quantity: {product.product_quantity}")
        elif quantity_diff < 0:
            # Add the reduced quantity back to inventory
            product.product_quantity -= quantity_diff  # Since quantity_diff is negative
            logger.info(f"Added {-quantity_diff} back to Product ID {product.product_id}. New quantity: {product.product_quantity}")

        # Update order_quantity and order_total
        order_db.order_quantity = new_quantity
        order_db.order_total = product.product_price * new_quantity
        logger.info(f"Updated Order ID {order_id} quantity to {new_quantity} and total to {order_db.order_total}")

        # Remove order_quantity from order_data to avoid redundant setting
        del order_data['order_quantity']
    
    # Update other fields dynamically
    for key, value in order_data.items():
        setattr(order_db, key, value)
        logger.info(f"Set {key} to {value} for Order ID {order_id}")

    # Update product_price and other related fields if necessary
    if 'product_price' in order_data:
        order_db.order_total = order_db.order_quantity * order_db.product_price
        logger.info(f"Recalculated Order ID {order_id} total to {order_db.order_total} based on new product_price.")

    # Commit the changes to the database
    try:
        session.add(order_db)
        session.add(product)
        session.commit()
        session.refresh(order_db)
        logger.info(f"Order ID {order_id} successfully updated in the database.")
    except Exception as e:
        session.rollback()
        logger.error(f"Database commit failed for Order ID {order_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

    # Map Python enum to Protobuf enum
    protobuf_order_status = map_python_to_protobuf_order_status(order_db.order_status)

    orderdb_protobuf = order_pb2.Order(
        order_id=order_db.order_id,
        user_id=order_db.user_id,
        customer_name=order_db.customer_name,
        customer_email=order_db.customer_email,
        customer_cellno=order_db.customer_cellno,
        customer_address=order_db.customer_address,
        product_id=order_db.product_id,
        order_quantity=product.product_quantity,
        product_price=order_db.product_price,
        order_total=order_db.order_total,
        order_status=protobuf_order_status,
        item_action=order_pb2.ActionType.UPDATE
    )
    await producer.send_and_wait('order_data', orderdb_protobuf.SerializeToString())
    logger.info(f"Produced Kafka message for Order ID {order_id}.")

    return order_db

@app.patch("/orders/status/{order_id}", response_model=OrderOutput)
async def update_order_status(
    order_id: int,
    updated_order_status: OrderStatusUpdate,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[dict,Depends(get_current_admin_user)],
    producer: Annotated[AIOKafkaProducer, Depends(produce_message)]
):
    order_db = session.get(Orders, order_id)
    if not order_db:
        raise HTTPException(status_code=404, detail="Order not found")
    
    order_db.order_status = updated_order_status.order_status

    session.add(order_db)
    session.commit()
    session.refresh(order_db)

     # Map Python enum to Protobuf enum
    protobuf_order_status = map_python_to_protobuf_order_status(order_db.order_status)
    orderdb_protobuf = order_pb2.Order(
        # customer_name=order_db.customer_name,
        # customer_email=order_db.customer_email,
        # customer_cellno=order_db.customer_cellno,
        # customer_address=order_db.customer_address,
        # product_id=order_db.product_id,
        # order_quantity=order_db.order_quantity,
        # product_price=order_db.product_price,
        # order_total=order_db.order_total,
        order_id=order_db.order_id,
        user_id=order_db.user_id,
        order_status=protobuf_order_status,
        item_action=order_pb2.ActionType.UPDATE
    )

    await producer.send_and_wait('order_data', orderdb_protobuf.SerializeToString())

    return order_db


@app.delete("/orders/{order_id}", status_code=204)
async def delete_order(
    order_id: int,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[dict,Depends(get_current_user)],
    producer: AIOKafkaProducer = Depends(produce_message)
):
    order_db = session.get(Orders, order_id)
    if not order_db:
        raise HTTPException(status_code=404, detail="Order not found")
    
    user_id = current_user.get('user_id')
    if user_id is None:
        raise HTTPException(status_code=400, detail="User ID not found in token.")
    if order_db.user_id != user_id:
        raise HTTPException(status_code=403, detail="You are not authorized to delete this order.")

    product = session.get(ProductInventory, order_db.product_id)
    if product:
        product.product_quantity += order_db.order_quantity

    session.delete(order_db)
    session.add(product)
    session.commit()

     # Map Python enum to Protobuf enum
    protobuf_order_status = map_python_to_protobuf_order_status(order_db.order_status)
    # Send the updated product quantity in the message
    orderdb_protobuf = order_pb2.Order(
        order_id=order_db.order_id,
        user_id=order_db.user_id,
        product_id=order_db.product_id,
        order_quantity=product.product_quantity,  # Updated quantity after deletion
        order_status=protobuf_order_status,
        item_action=order_pb2.ActionType.DELETE
    )
    await producer.send_and_wait('order_data', orderdb_protobuf.SerializeToString())

    return {"Order Cancellation": "successful"}