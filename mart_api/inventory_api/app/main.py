# inventory_api/app/main.py

from fastapi import FastAPI, Depends, HTTPException
from sqlmodel import Session, select
from .models import InventoryCreate, Inventory, InventoryUpdate
from typing import Annotated
from aiokafka import AIOKafkaProducer, AIOKafkaConsumer
from contextlib import asynccontextmanager
from .db import engine, create_db_and_tables, get_session
from topic_generator.create_topic import create_kafka_topic
from app import inventory_pb2, order_pb2, user_pb2
from app.utils import (
    map_python_to_protobuf_item_status,
    get_current_user,
    get_current_admin_user,
    get_current_active_user
)
from app.session_store import user_sessions 
import logging, asyncio
from app import settings



# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Kafka Configuration
KAFKA_BOOTSTRAP_SERVERS = "broker:19092"  # Kafka broker address
KAFKA_TOPIC = ["order_data", "user_data", "user_loginlogout"]               # Topic to consume from
GROUP_ID = "order_inventory"                    # Consumer group ID



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
            
            if topic == "order_data":
                order_inventory_update = order_pb2.Order()
                order_inventory_update.ParseFromString(msg.value)
                logger.info(f"\n\nConsumer Deserialized data: {order_inventory_update}")

                with Session(engine) as session:
                    # Fetch the relevant inventory item based on product_id
                    inventory_item = session.get(Inventory, order_inventory_update.product_id)

                    if order_inventory_update.item_action == order_pb2.ActionType.CREATE:
                        # Reduce inventory based on order quantity for a new order
                        if inventory_item and inventory_item.item_quantity >= order_inventory_update.order_quantity:
                            inventory_item.item_quantity -= order_inventory_update.order_quantity
                            logger.info(f"Inventory updated for CREATE action: Reduced item_quantity by {order_inventory_update.order_quantity}")
                        else:
                            logger.warning(f"Insufficient quantity for CREATE action on product ID {order_inventory_update.product_id}")

                    elif order_inventory_update.item_action == order_pb2.ActionType.UPDATE:
                        # Adjust inventory based on the latest product quantity after an update
                        if inventory_item:
                            inventory_item.item_quantity = order_inventory_update.order_quantity
                            logger.info(f"Inventory updated for UPDATE action: Set item_quantity to {order_inventory_update.order_quantity}")

                    elif order_inventory_update.item_action == order_pb2.ActionType.DELETE:
                        # Restore inventory based on order quantity for a deleted order
                        if inventory_item:
                            inventory_item.item_quantity = order_inventory_update.order_quantity
                            logger.info(f"Inventory updated for DELETE action: Restored item_quantity by {order_inventory_update.order_quantity}")

                    # Commit changes to the database if the inventory item exists and was updated
                    if inventory_item:
                        session.add(inventory_item)
                        session.commit()
                    else:
                        logger.warning(f"Product ID {order_inventory_update.product_id} not found in inventory for action {order_inventory_update.item_action}")

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

    except Exception as e:
        logger.error(f"Error in consumer: {e}")

    finally:
        await consumer.stop()
        logger.info("Kafka consumer stopped.")



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
    
    # Create Kafka topic
    #await create_kafka_topic('stock_items')
    await create_kafka_topic('order_data')
    await create_kafka_topic('user_data')
    await create_kafka_topic('user_loginlogout')

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



app = FastAPI(lifespan=lifespan, title="Fahad Mart API App", 
    version="0.0.1",
    servers=[
        {
            "url": "http://127.0.0.1:8000", # ADD NGROK URL Here Before Creating GPT Action
            "description": "Development Server"
        }
        ])



@app.get("/inventory", response_model=list[Inventory])
async def get_items(
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[dict, Depends(get_current_admin_user)]
):
        items = session.exec(select(Inventory)).all()
        return items

@app.post("/inventory", response_model=Inventory)
async def create_item(
    item: InventoryCreate,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[dict, Depends(get_current_admin_user)],
    producer: Annotated[AIOKafkaProducer, Depends(produce_message)]
):
        item_db = Inventory.model_validate(item)
        
        session.add(item_db)
        session.commit()
        session.refresh(item_db)
        # itemdb_json = item_db.model_dump_json().encode('utf-8')

        # Map Python enum to Protobuf enum
        protobuf_item_status = map_python_to_protobuf_item_status(item_db.item_status)
        itemdb_protobuf = inventory_pb2.Item(item_id=item_db.item_id, item_name=item_db.item_name,
                                            item_category=item_db.item_category, item_price=item_db.item_price,
                                            item_quantity=item_db.item_quantity,
                                            item_status=protobuf_item_status,
                                            item_action= inventory_pb2.ActionType.CREATE)
        # produce message to kafka
        await producer.send_and_wait('stock_items', itemdb_protobuf.SerializeToString())
        return item_db

@app.patch("/inventory/{item_id}", response_model=Inventory)
async def update_item(
    item_id: int,
    updated_item: InventoryUpdate,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[dict, Depends(get_current_admin_user)],
    producer: Annotated[AIOKafkaProducer, Depends(produce_message)]
):
        item_db = session.get(Inventory, item_id)
        if not item_db:
            raise HTTPException(status_code=404, detail="Item not found")
        
        previous_price = item_db.item_price
        previous_status = item_db.item_status

        item_data = updated_item.model_dump(exclude_unset=True)
        for key, value in item_data.items():
            setattr(item_db, key, value)
        #item_db.sqlmodel_update(item_data) 

        item_db.update_status()   # updates status based on item_quantity
        logger.info(f"previous_price: {previous_price}, previous_status: {previous_status}")
        session.add(item_db)
        session.commit()
        session.refresh(item_db)

        current_price = item_db.item_price
        current_status = item_db.item_status
        logger.info(f"current_price: {current_price}, current_status: {current_status}")

        # Determine if price and status has changed
        price_drop = previous_price - current_price
        status_changed = previous_status != current_status

        # Map Python enum to Protobuf enum
        protobuf_item_status = map_python_to_protobuf_item_status(item_db.item_status)
        itemdb_protobuf = inventory_pb2.Item(item_id=item_db.item_id, item_name=item_db.item_name,
                                            item_category=item_db.item_category, item_price=item_db.item_price,
                                            item_quantity=item_db.item_quantity,
                                            item_status=protobuf_item_status,
                                            item_status_change=status_changed,
                                            item_price_drop=price_drop,
                                            item_action=inventory_pb2.ActionType.UPDATE)
        # produce message to kafka
        await producer.send_and_wait('stock_items', itemdb_protobuf.SerializeToString())
        return item_db


@app.delete("/inventory/{item_id}", status_code=204)
async def delete_item(
    item_id: int,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[dict, Depends(get_current_admin_user)],
    producer: Annotated[AIOKafkaProducer, Depends(produce_message)]
):
        item_db = session.get(Inventory, item_id)
        if not item_db:
            raise HTTPException(status_code=404, detail="Item not found")
        session.delete(item_db)
        session.commit()

        itemdb_protobuf = inventory_pb2.Item(item_id=item_db.item_id,
                                             item_name=item_db.item_name,
                                            item_action=inventory_pb2.ActionType.DELETE)
        # produce message to kafka
        await producer.send_and_wait('stock_items', itemdb_protobuf.SerializeToString())

        return


