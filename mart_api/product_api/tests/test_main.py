# product_api/tests/test_main.py

import os

# Set the TESTING environment variable to use the test database
os.environ["TESTING"] = "1"

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, select
from unittest.mock import AsyncMock, patch, Mock
from app.db import engine, get_session
from app.main import app, consume_messages
from app.models import Products, ProductStatus
from app.utils import map_protobuf_to_python_item_status
import asyncio

# Initialize TestClient
client = TestClient(app)

# ------------------------------ Fixtures ------------------------------


@pytest.fixture(name="create_test_database")
def create_test_database_fixture():
    """
    Creates the database and tables before tests and drops them after tests.
    Overrides the get_session dependency to use the test database.
    """
    SQLModel.metadata.create_all(engine)

    def override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    yield

    SQLModel.metadata.drop_all(engine)
    app.dependency_overrides.pop(get_session, None)


@pytest.fixture(name="mock_kafka_consumer")
def mock_kafka_consumer_fixture():
    """
    Mocks the AIOKafkaConsumer to prevent actual Kafka interactions.
    """
    with patch("app.main.AIOKafkaConsumer") as mock_consumer:
        instance = mock_consumer.return_value
        instance.start = AsyncMock()
        instance.stop = AsyncMock()
        instance.__aiter__.return_value = iter([])
        yield instance


# -------------------- Endpoint Functionality Tests --------------------


def test_get_products_empty(create_test_database):
    """
    Tests retrieving products when the database is empty.
    Expects an empty list.
    """
    response = client.get("/products/")
    assert response.status_code == 200
    assert response.json() == []


def test_get_products_with_entries(create_test_database):
    """
    Tests retrieving products when the database has entries.
    Expects a list of products.
    """
    # Pre-populate the database with products
    with Session(engine) as session:
        product1 = Products(
            product_id=1,
            product_name="Laptop",
            product_category="Electronics",
            product_price=999.99,
            product_quantity=10,
            product_status=ProductStatus.IN_STOCK
        )
        product2 = Products(
            product_id=2,
            product_name="Smartphone",
            product_category="Electronics",
            product_price=499.99,
            product_quantity=25,
            product_status=ProductStatus.IN_STOCK
        )
        session.add(product1)
        session.add(product2)
        session.commit()

    response = client.get("/products/")
    assert response.status_code == 200
    products = response.json()
    assert isinstance(products, list)
    assert len(products) == 2
    assert products[0]["product_name"] == "Laptop"
    assert products[1]["product_name"] == "Smartphone"


# -------------------- Kafka Message Handling Tests --------------------


@pytest.mark.asyncio
async def test_consume_stock_items_create(create_test_database):
    """
    Tests consuming a 'stock_items' message with action CREATE.
    Expects a new product to be added to the database.
    """
    # Mock inventory_pb2.Item
    with patch("app.main.inventory_pb2.Item") as mock_item:
        mock_item_instance = mock_item.return_value

        # Define side effect for ParseFromString to set attributes
        def parse_from_string_side_effect(data):
            mock_item_instance.item_id = 3
            mock_item_instance.item_name = "Headphones"
            mock_item_instance.item_category = "Accessories"
            mock_item_instance.item_price = 199.99
            mock_item_instance.item_quantity = 15
            mock_item_instance.item_status = 0  # IN_STOCK
            mock_item_instance.item_action = 0  # CREATE

        mock_item_instance.ParseFromString.side_effect = parse_from_string_side_effect

        # Define an async generator to yield the mocked message
        async def mock_async_gen():
            yield Mock(topic="stock_items", value=b"dummy_bytes")

        with patch("app.main.AIOKafkaConsumer.__aiter__", new=mock_async_gen):
            # Start the consumer
            consumer_task = asyncio.create_task(consume_messages())

            # Allow some time for the consumer to process
            await asyncio.sleep(0.2)

            # Stop the consumer
            consumer_task.cancel()
            try:
                await consumer_task
            except asyncio.CancelledError:
                pass

    # Verify that the product was added
    with Session(engine) as session:
        product = session.get(Products, 3)
        assert product is not None
        assert product.product_name == "Headphones"
        assert product.product_category == "Accessories"
        assert product.product_price == 199.99
        assert product.product_quantity == 15
        assert product.product_status == ProductStatus.IN_STOCK


@pytest.mark.asyncio
async def test_consume_stock_items_update(create_test_database):
    """
    Tests consuming a 'stock_items' message with action UPDATE.
    Expects an existing product to be updated in the database.
    """
    # Pre-populate the database with a product
    with Session(engine) as session:
        product = Products(
            product_id=4,
            product_name="Tablet",
            product_category="Electronics",
            product_price=299.99,
            product_quantity=20,
            product_status=ProductStatus.IN_STOCK
        )
        session.add(product)
        session.commit()

    # Mock inventory_pb2.Item for UPDATE
    with patch("app.main.inventory_pb2.Item") as mock_item:
        mock_item_instance = mock_item.return_value

        # Define side effect for ParseFromString to set attributes
        def parse_from_string_side_effect(data):
            mock_item_instance.item_id = 4
            mock_item_instance.item_name = "Tablet Pro"
            mock_item_instance.item_category = "Electronics"
            mock_item_instance.item_price = 399.99
            mock_item_instance.item_quantity = 18
            mock_item_instance.item_status = 0  # IN_STOCK
            mock_item_instance.item_action = 1  # UPDATE

        mock_item_instance.ParseFromString.side_effect = parse_from_string_side_effect

        # Define an async generator to yield the mocked message
        async def mock_async_gen():
            yield Mock(topic="stock_items", value=b"dummy_bytes")

        with patch("app.main.AIOKafkaConsumer.__aiter__", new=mock_async_gen):
            # Start the consumer
            consumer_task = asyncio.create_task(consume_messages())

            # Allow some time for the consumer to process
            await asyncio.sleep(0.2)

            # Stop the consumer
            consumer_task.cancel()
            try:
                await consumer_task
            except asyncio.CancelledError:
                pass

    # Verify that the product was updated
    with Session(engine) as session:
        product = session.get(Products, 4)
        assert product is not None
        assert product.product_name == "Tablet Pro"
        assert product.product_price == 399.99
        assert product.product_quantity == 18
        assert product.product_status == ProductStatus.IN_STOCK


@pytest.mark.asyncio
async def test_consume_stock_items_delete(create_test_database):
    """
    Tests consuming a 'stock_items' message with action DELETE.
    Expects an existing product to be deleted from the database.
    """
    # Pre-populate the database with a product
    with Session(engine) as session:
        product = Products(
            product_id=5,
            product_name="Smartwatch",
            product_category="Accessories",
            product_price=149.99,
            product_quantity=30,
            product_status=ProductStatus.IN_STOCK
        )
        session.add(product)
        session.commit()

    # Mock inventory_pb2.Item for DELETE
    with patch("app.main.inventory_pb2.Item") as mock_item:
        mock_item_instance = mock_item.return_value

        # Define side effect for ParseFromString to set attributes
        def parse_from_string_side_effect(data):
            mock_item_instance.item_id = 5
            mock_item_instance.item_action = 2  # DELETE

        mock_item_instance.ParseFromString.side_effect = parse_from_string_side_effect

        # Define an async generator to yield the mocked message
        async def mock_async_gen():
            yield Mock(topic="stock_items", value=b"dummy_bytes")

        with patch("app.main.AIOKafkaConsumer.__aiter__", new=mock_async_gen):
            # Start the consumer
            consumer_task = asyncio.create_task(consume_messages())

            # Allow some time for the consumer to process
            await asyncio.sleep(0.2)

            # Stop the consumer
            consumer_task.cancel()
            try:
                await consumer_task
            except asyncio.CancelledError:
                pass

    # Verify that the product was deleted
    with Session(engine) as session:
        product = session.get(Products, 5)
        assert product is None


@pytest.mark.asyncio
async def test_consume_order_data_create(create_test_database):
    """
    Tests consuming an 'order_data' message with action CREATE.
    Expects the product quantity to be decreased accordingly.
    """
    # Pre-populate the database with a product
    with Session(engine) as session:
        product = Products(
            product_id=6,
            product_name="Camera",
            product_category="Electronics",
            product_price=599.99,
            product_quantity=50,
            product_status=ProductStatus.IN_STOCK
        )
        session.add(product)
        session.commit()

    # Mock order_pb2.Order for CREATE
    with patch("app.main.order_pb2.Order") as mock_order:
        mock_order_instance = mock_order.return_value

        # Define side effect for ParseFromString to set attributes
        def parse_from_string_side_effect(data):
            mock_order_instance.product_id = 6
            mock_order_instance.order_quantity = 5
            mock_order_instance.item_action = 0  # CREATE

        mock_order_instance.ParseFromString.side_effect = parse_from_string_side_effect

        # Define an async generator to yield the mocked message
        async def mock_async_gen():
            yield Mock(topic="order_data", value=b"dummy_bytes")

        with patch("app.main.AIOKafkaConsumer.__aiter__", new=mock_async_gen):
            # Start the consumer
            consumer_task = asyncio.create_task(consume_messages())

            # Allow some time for the consumer to process
            await asyncio.sleep(0.2)

            # Stop the consumer
            consumer_task.cancel()
            try:
                await consumer_task
            except asyncio.CancelledError:
                pass

    # Verify that the product quantity was decreased
    with Session(engine) as session:
        product = session.get(Products, 6)
        assert product is not None
        assert product.product_quantity == 45  # 50 - 5


@pytest.mark.asyncio
async def test_consume_order_data_update(create_test_database):
    """
    Tests consuming an 'order_data' message with action UPDATE.
    Expects the product quantity to be updated accordingly.
    """
    # Pre-populate the database with a product
    with Session(engine) as session:
        product = Products(
            product_id=7,
            product_name="Bluetooth Speaker",
            product_category="Accessories",
            product_price=79.99,
            product_quantity=40,
            product_status=ProductStatus.IN_STOCK
        )
        session.add(product)
        session.commit()

    # Mock order_pb2.Order for UPDATE
    with patch("app.main.order_pb2.Order") as mock_order:
        mock_order_instance = mock_order.return_value

        # Define side effect for ParseFromString to set attributes
        def parse_from_string_side_effect(data):
            mock_order_instance.product_id = 7
            mock_order_instance.order_quantity = 35  # New quantity
            mock_order_instance.item_action = 1  # UPDATE

        mock_order_instance.ParseFromString.side_effect = parse_from_string_side_effect

        # Define an async generator to yield the mocked message
        async def mock_async_gen():
            yield Mock(topic="order_data", value=b"dummy_bytes")

        with patch("app.main.AIOKafkaConsumer.__aiter__", new=mock_async_gen):
            # Start the consumer
            consumer_task = asyncio.create_task(consume_messages())

            # Allow some time for the consumer to process
            await asyncio.sleep(0.2)

            # Stop the consumer
            consumer_task.cancel()
            try:
                await consumer_task
            except asyncio.CancelledError:
                pass

    # Verify that the product quantity was updated
    with Session(engine) as session:
        product = session.get(Products, 7)
        assert product is not None
        assert product.product_quantity == 35  # Updated quantity


@pytest.mark.asyncio
async def test_consume_order_data_delete(create_test_database):
    """
    Tests consuming an 'order_data' message with action DELETE.
    Expects the product quantity to be updated accordingly.
    """
    # Pre-populate the database with a product
    with Session(engine) as session:
        product = Products(
            product_id=8,
            product_name="Gaming Mouse",
            product_category="Accessories",
            product_price=49.99,
            product_quantity=60,
            product_status=ProductStatus.IN_STOCK
        )
        session.add(product)
        session.commit()

    # Mock order_pb2.Order for DELETE
    with patch("app.main.order_pb2.Order") as mock_order:
        mock_order_instance = mock_order.return_value

        # Define side effect for ParseFromString to set attributes
        def parse_from_string_side_effect(data):
            mock_order_instance.product_id = 8
            mock_order_instance.order_quantity = 55  # New quantity after deletion
            mock_order_instance.item_action = 2  # DELETE

        mock_order_instance.ParseFromString.side_effect = parse_from_string_side_effect

        # Define an async generator to yield the mocked message
        async def mock_async_gen():
            yield Mock(topic="order_data", value=b"dummy_bytes")

        with patch("app.main.AIOKafkaConsumer.__aiter__", new=mock_async_gen):
            # Start the consumer
            consumer_task = asyncio.create_task(consume_messages())

            # Allow some time for the consumer to process
            await asyncio.sleep(0.2)

            # Stop the consumer
            consumer_task.cancel()
            try:
                await consumer_task
            except asyncio.CancelledError:
                pass

    # Verify that the product quantity was updated
    with Session(engine) as session:
        product = session.get(Products, 8)
        assert product is not None
        assert product.product_quantity == 55  # Updated quantity


# -------------------- Error Handling Tests --------------------


@pytest.mark.asyncio
async def test_consume_invalid_stock_items(create_test_database):
    """
    Tests consuming a 'stock_items' message with invalid data.
    Expects the handler to raise an exception and not alter the database.
    """
    # Pre-populate the database with a product
    with Session(engine) as session:
        product = Products(
            product_id=9,
            product_name="Wireless Charger",
            product_category="Accessories",
            product_price=29.99,
            product_quantity=20,
            product_status=ProductStatus.IN_STOCK
        )
        session.add(product)
        session.commit()

    # Mock inventory_pb2.Item with missing fields
    with patch("app.main.inventory_pb2.Item") as mock_item:
        mock_item_instance = mock_item.return_value

        # Define side effect for ParseFromString (missing fields)
        def parse_from_string_side_effect(data):
            mock_item_instance.item_id = 9
            # Missing item_name, item_category, etc.
            mock_item_instance.item_action = 0  # CREATE

        mock_item_instance.ParseFromString.side_effect = parse_from_string_side_effect

        # Define an async generator to yield the mocked message
        async def mock_async_gen():
            yield Mock(topic="stock_items", value=b"dummy_bytes")

        with patch("app.main.AIOKafkaConsumer.__aiter__", new=mock_async_gen):
            # Start the consumer
            consumer_task = asyncio.create_task(consume_messages())

            # Allow some time for the consumer to process
            await asyncio.sleep(0.2)

            # Stop the consumer
            consumer_task.cancel()
            try:
                await consumer_task
            except asyncio.CancelledError:
                pass

    # Verify that the product was not altered due to invalid data
    with Session(engine) as session:
        product = session.get(Products, 9)
        assert product is not None
        assert product.product_name == "Wireless Charger"
        assert product.product_quantity == 20  # Should remain unchanged


@pytest.mark.asyncio
async def test_consume_order_data_invalid_product(create_test_database):
    """
    Tests consuming an 'order_data' message for a non-existent product.
    Expects the handler to raise an exception and not alter the database.
    """
    # Pre-populate the database with a product
    with Session(engine) as session:
        product = Products(
            product_id=10,
            product_name="External Hard Drive",
            product_category="Electronics",
            product_price=89.99,
            product_quantity=15,
            product_status=ProductStatus.IN_STOCK
        )
        session.add(product)
        session.commit()

    # Mock order_pb2.Order with non-existent product_id
    with patch("app.main.order_pb2.Order") as mock_order:
        mock_order_instance = mock_order.return_value

        # Define side effect for ParseFromString to set attributes
        def parse_from_string_side_effect(data):
            mock_order_instance.product_id = 999  # Non-existent
            mock_order_instance.order_quantity = 5
            mock_order_instance.item_action = 0  # CREATE

        mock_order_instance.ParseFromString.side_effect = parse_from_string_side_effect

        # Define an async generator to yield the mocked message
        async def mock_async_gen():
            yield Mock(topic="order_data", value=b"dummy_bytes")

        with patch("app.main.AIOKafkaConsumer.__aiter__", new=mock_async_gen):
            # Start the consumer
            consumer_task = asyncio.create_task(consume_messages())

            # Allow some time for the consumer to process
            await asyncio.sleep(0.2)

            # Stop the consumer
            consumer_task.cancel()
            try:
                await consumer_task
            except asyncio.CancelledError:
                pass

    # Verify that no product was added or altered
    with Session(engine) as session:
        product = session.get(Products, 999)
        assert product is None
        existing_product = session.get(Products, 10)
        assert existing_product is not None
        assert existing_product.product_quantity == 15  # Should remain unchanged


# -------------------- Exception Handling Tests --------------------


@pytest.mark.asyncio
async def test_consume_stock_items_exception(create_test_database):
    """
    Tests consuming a 'stock_items' message that raises an exception.
    Ensures that the database remains consistent.
    """
    # Pre-populate the database with a product
    with Session(engine) as session:
        product = Products(
            product_id=11,
            product_name="Gaming Keyboard",
            product_category="Accessories",
            product_price=59.99,
            product_quantity=25,
            product_status=ProductStatus.IN_STOCK
        )
        session.add(product)
        session.commit()

    # Mock inventory_pb2.Item for UPDATE with invalid status
    with patch("app.main.inventory_pb2.Item") as mock_item:
        mock_item_instance = mock_item.return_value

        # Define side effect for ParseFromString to set attributes
        def parse_from_string_side_effect(data):
            mock_item_instance.item_id = 11
            mock_item_instance.item_name = "Gaming Keyboard Pro"
            mock_item_instance.item_category = "Accessories"
            mock_item_instance.item_price = 79.99
            mock_item_instance.item_quantity = 20
            mock_item_instance.item_status = 99  # Invalid status
            mock_item_instance.item_action = 1  # UPDATE

        mock_item_instance.ParseFromString.side_effect = parse_from_string_side_effect

        # Define an async generator to yield the mocked message
        async def mock_async_gen():
            yield Mock(topic="stock_items", value=b"dummy_bytes")

        with patch("app.main.AIOKafkaConsumer.__aiter__", new=mock_async_gen):
            # Start the consumer
            consumer_task = asyncio.create_task(consume_messages())

            # Allow some time for the consumer to process
            await asyncio.sleep(0.2)

            # Stop the consumer
            consumer_task.cancel()
            try:
                await consumer_task
            except asyncio.CancelledError:
                pass

    # Verify that the product was not altered due to exception
    with Session(engine) as session:
        product = session.get(Products, 11)
        assert product is not None
        assert product.product_name == "Gaming Keyboard"
        assert product.product_price == 59.99
        assert product.product_quantity == 25  # Should remain unchanged


@pytest.mark.asyncio
async def test_consume_order_data_exception(create_test_database):
    """
    Tests consuming an 'order_data' message that raises an exception.
    Ensures that the database remains consistent.
    """
    # Pre-populate the database with a product
    with Session(engine) as session:
        product = Products(
            product_id=12,
            product_name="LED Monitor",
            product_category="Electronics",
            product_price=199.99,
            product_quantity=10,
            product_status=ProductStatus.IN_STOCK
        )
        session.add(product)
        session.commit()

    # Mock order_pb2.Order and simulate exception during processing
    with patch("app.main.order_pb2.Order") as mock_order:
        mock_order_instance = mock_order.return_value

        # Define side effect for ParseFromString to set attributes
        def parse_from_string_side_effect(data):
            mock_order_instance.product_id = 12
            mock_order_instance.order_quantity = 5
            mock_order_instance.item_action = 0  # CREATE

        mock_order_instance.ParseFromString.side_effect = parse_from_string_side_effect

        # Define an async generator to yield the mocked message and then raise an exception
        async def mock_async_gen():
            yield Mock(topic="order_data", value=b"dummy_bytes")
            raise Exception("Processing Error")

        with patch("app.main.AIOKafkaConsumer.__aiter__", new=mock_async_gen):
            # Start the consumer
            consumer_task = asyncio.create_task(consume_messages())

            # Allow some time for the consumer to process and raise exception
            await asyncio.sleep(0.2)

            # Stop the consumer
            consumer_task.cancel()
            try:
                await consumer_task
            except asyncio.CancelledError:
                pass

    # Verify that the product was not altered due to exception
    with Session(engine) as session:
        product = session.get(Products, 12)
        assert product is not None
        assert product.product_quantity == 10  # Should remain unchanged


# -------------------- Coverage and Quality --------------------

# To generate a coverage report, ensure you have pytest-cov installed and run:
# pytest --cov=app tests/

# You can also add a pytest.ini file to configure coverage settings if needed.
