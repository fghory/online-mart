# product_api

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Annotated

from aiokafka import AIOKafkaConsumer
from fastapi import FastAPI, Depends
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from sqlmodel import Session, select

from .db import create_db_and_tables, get_session, engine
from .models import ProductCreate, Products
# from ...create_topic import create_kafka_topic
from topic_generator.create_topic import create_kafka_topic
from app import inventory_pb2, order_pb2
from app.utils import map_protobuf_to_python_item_status



# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Kafka Configuration
KAFKA_BOOTSTRAP_SERVERS = "broker:19092"  # Kafka broker address
KAFKA_TOPIC = ["stock_items","order_data"]               # Topic to consume from
GROUP_ID = "inventory_product"                    # Consumer group ID




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
                new_itemdb = inventory_pb2.Item()
                new_itemdb.ParseFromString(msg.value)
                logger.info(f"\n\n Consumer Deserialized data: {new_itemdb}")
                # message = json.loads(msg.value.decode('utf-8'))  # Adjust decoding as per your message format
            
            # Map Protobuf Enum to Python Enum
            # Required coz new_itemdb.item_status is set to inventory_pb2.ItemStatus.IN_STOCK (or any other status)
            # means new_itemdb.item_status is set to 0. But product_status can only be a enum as per Products model
            # So we need b/m mapping. 
            # We will not need the mapping If we don't put above condition, make product_status as int in Products model

                python_item_status = map_protobuf_to_python_item_status(new_itemdb.item_status)

                # Here you can add processing logic, e.g., save to database'
                with Session(engine) as session:
                    if new_itemdb.item_action == inventory_pb2.ActionType.CREATE:   
                        product_db = Products(product_id=new_itemdb.item_id,
                                            product_name=new_itemdb.item_name,
                                            product_category=new_itemdb.item_category,
                                            product_price=new_itemdb.item_price, 
                                            product_quantity=new_itemdb.item_quantity, 
                                            product_status=python_item_status)
                        session.add(product_db)
                        session.commit()
                        session.refresh(product_db)

                    elif new_itemdb.item_action == inventory_pb2.ActionType.UPDATE:
                        product_db = session.exec(select(Products).where(Products.product_id == new_itemdb.item_id)).one()
                        product_db.product_name = new_itemdb.item_name
                        product_db.product_category = new_itemdb.item_category
                        product_db.product_price = new_itemdb.item_price
                        product_db.product_quantity = new_itemdb.item_quantity
                        product_db.product_status = python_item_status
                        session.add(product_db)
                        session.commit()
                        session.refresh(product_db)    

                    elif new_itemdb.item_action == inventory_pb2.ActionType.DELETE:
                        product_db = session.exec(select(Products).where(Products.product_id == new_itemdb.item_id)).one()
                        session.delete(product_db)
                        session.commit()    

            if topic == "order_data":
                product_quantity_update = order_pb2.Order()
                product_quantity_update.ParseFromString(msg.value)
                logger.info(f"\n\nConsumer Deserialized data: {product_quantity_update}")

                with Session(engine) as session:
                    # product_db = session.exec(select(Products).where(Products.product_id == product_quantity_update.product_id)).one()
                    product_db = session.get(Products, product_quantity_update.product_id)
                    if product_quantity_update.item_action == order_pb2.ActionType.CREATE:
                        product_db.product_quantity -= product_quantity_update.order_quantity
                        logger.info(f"Product quantity updated for order CREATE action by {product_quantity_update.order_quantity}")
                        session.commit()
                        session.refresh(product_db)

                    elif product_quantity_update.item_action == order_pb2.ActionType.UPDATE:
                        product_db.product_quantity = product_quantity_update.order_quantity
                        session.commit()
                        session.refresh(product_db)

                    elif product_quantity_update.item_action == order_pb2.ActionType.DELETE:
                        product_db.product_quantity = product_quantity_update.order_quantity
                        session.commit()
                        session.refresh(product_db)    

    except Exception as e:
        logger.error(f"Error in consumer: {e}") 
    finally:
        await consumer.stop()
        logger.info("Kafka consumer stopped.")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan event to manage application startup and shutdown.
    """
    # Initialize the database and create tables
    create_db_and_tables()
    logger.info("Database created and tables ensured.")

    # Create Kafka topic
    await create_kafka_topic('stock_items',2)

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

# Initialize FastAPI with lifespan event
app = FastAPI(
    lifespan=lifespan,
    title="Fahad Mart API App",
    version="0.0.1",
    # servers=[
    #     {
    #         "url": "http://127.0.0.1:8000",  # ADD NGROK URL Here Before Creating GPT Action
    #         "description": "Development Server"
    #     }
    # ]
)

@app.get("/products", response_model=list[Products])
def get_products(session: Annotated[Session, Depends(get_session)]):
    items = session.exec(select(Products)).all()
    return items

# products should be added through consumed inventory services messages only. 
# coz logically a new product cannot be shown to customer unless it is present in inventory

# @app.post("/products/", response_model=Products)
# def create_product(item: ProductCreate, session: Annotated[Session, Depends(get_session)]):
#     product_db = Products.model_validate(item)
#     session.add(product_db)
#     session.commit()
#     session.refresh(product_db)
#     return product_db
