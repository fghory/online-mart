# order_api/tests/test_main.py

import os

# Set the TESTING environment variable to use the test database
os.environ["TESTING"] = "1"

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session
from unittest.mock import AsyncMock
from app.db import engine, get_session
from app.main import app, produce_message
from app.utils import get_current_admin_user, get_current_user
from app.models import (
    OrderCreate, Orders, OrderOutput, ProductInventory,
    OrderUpdate, OrderStatusUpdate, PaymentStatus
)

client = TestClient(app)

# ------------------------------ Fixtures ------------------------------

# Fixture to create the test database and tables
@pytest.fixture(name="create_test_database")
def create_test_database_fixture():
    """
    Overrides the get_session dependency to use the test database session.
    Creates all tables before tests and drops them after tests.
    """
    def override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    # Setup: create tables in the test DB
    SQLModel.metadata.create_all(engine)
    yield
    # Teardown: drop tables after tests
    SQLModel.metadata.drop_all(engine)

    # Remove only the specific dependency override
    app.dependency_overrides.pop(get_session, None)


# Fixture to mock the Kafka producer by overriding the produce_message dependency
@pytest.fixture(name="mock_kafka_producer")
def mock_kafka_producer_fixture():
    """
    Mocks the Kafka producer to prevent actual Kafka interactions during tests.
    Verifies that messages are being "sent" as expected.
    """
    mock_producer = AsyncMock()

    async def mock_produce():
        yield mock_producer

    app.dependency_overrides[produce_message] = mock_produce
    yield mock_producer
    # Remove only the specific dependency override
    app.dependency_overrides.pop(produce_message, None)


# Fixture to mock the get_current_admin_user dependency
@pytest.fixture(name="mock_get_current_admin_user")
def mock_get_current_admin_user_fixture():
    """
    Mocks the get_current_admin_user dependency to return a predefined admin user.
    Ensures that admin-specific endpoints recognize the user as an admin.
    """
    async def mock_get_current_admin_user():
        return {
            'user_id': 1,
            'role': 'ADMIN',
            'user_name': 'adminuser',
            'user_email': 'admin@example.com',
            'user_cellno': '0000000000',
            'user_address': 'Admin Address'
        }
    app.dependency_overrides[get_current_admin_user] = mock_get_current_admin_user
    yield mock_get_current_admin_user  # Yield the mock function
    # Remove only the specific dependency override
    app.dependency_overrides.pop(get_current_admin_user, None)


# Fixture to mock the get_current_user dependency for non-admin users
@pytest.fixture(name="mock_get_current_non_admin_user")
def mock_get_current_non_admin_user_fixture():
    """
    Mocks the get_current_user dependency to return a predefined regular (customer) user.
    Ensures that non-admin users are recognized correctly and permission checks are enforced.
    """
    async def mock_get_current_non_admin_user():
        return {
            'user_id': 2,
            'role': 'CUSTOMER',
            'user_name': 'regularuser',
            'user_email': 'regular@example.com',
            'user_cellno': '1111111111',
            'user_address': 'Regular Address'
        }
    app.dependency_overrides[get_current_user] = mock_get_current_non_admin_user
    yield
    # Remove only the specific dependency override
    app.dependency_overrides.pop(get_current_user, None)


# -------------------------- Helper Functions --------------------------

def get_fake_admin_token():
    """
    Returns a fake admin token. Since dependencies are mocked,
    the token's validity is irrelevant.
    """
    return "fake-admin-token"


def get_fake_regular_token():
    """
    Returns a fake regular user token. Since dependencies are mocked,
    the token's validity is irrelevant.
    """
    return "fake-regular-token"


# ------------------------------ Test Cases ------------------------------

# -------------------- Authentication and Authorization Tests --------------------

def test_unauthorized_access_orders(create_test_database, mock_kafka_producer):
    """
    Tests accessing the /orders/ endpoint without authentication and with invalid tokens.
    Expects 401 Unauthorized responses.
    """
    # # Scenario 1: Empty token provided
    response = client.get(
        f"/orders/?token"
    )

    if response.status_code != 401:
        print("Error Response:", response.json())  # Debugging line

    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"

    # Scenario 2: Invalid token provided
    invalid_token = "invalid-token"
    response = client.get(
        f"/orders/?token={invalid_token}"
    )
    

    if response.status_code != 401:
        print("Error Response:", response.json())  # Debugging line

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid authentication credentials"


def test_forbidden_access_orders(create_test_database, mock_kafka_producer, mock_get_current_non_admin_user):
    """
    Tests accessing admin-specific endpoints with a regular user.
    Expects 403 Forbidden responses.
    """
    access_token = get_fake_regular_token()

    # Attempt to access admin endpoint /orders/
    response = client.get(
        "/orders/",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Insufficient permissions."


def test_authorized_access_orders(create_test_database, mock_kafka_producer, mock_get_current_admin_user):
    """
    Tests accessing the /orders/ endpoint with an admin user.
    Expects 200 OK response and a list of orders.
    """
    access_token = get_fake_admin_token()

    response = client.get(
        "/orders/",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    assert response.status_code == 200
    assert isinstance(response.json(), list)


# -------------------- CRUD Operations Tests --------------------

def test_create_order(create_test_database, mock_kafka_producer, mock_get_current_non_admin_user):
    """
    Tests creating a new order as a regular user.
    Expects 200 OK response and verifies order creation.
    """
    access_token = get_fake_regular_token()

    # Ensure that the product exists in the inventory
    product = ProductInventory(
        product_id=1,
        product_price=50.00,
        product_quantity=100
    )
    with Session(engine) as session:
        session.add(product)
        session.commit()
        session.refresh(product)

    order_data = {
        "customer_name": "John Doe",
        "customer_email": "johndoe@example.com",
        "customer_cellno": "1234567890",
        "customer_address": "123 Main St",
        "product_id": 1,
        "order_quantity": 2
    }

    response = client.post(
        "/orders/",
        json=order_data,
        headers={"Authorization": f"Bearer {access_token}"}
    )
    if response.status_code != 200:
        print("Error Response:", response.json())  # Debugging line
    assert response.status_code == 200
    response_data = response.json()
    assert response_data["customer_name"] == "John Doe"
    assert response_data["customer_email"] == "johndoe@example.com"
    assert response_data["order_quantity"] == 2
    assert response_data["order_status"] == "Confirmed"

    # Verify that the Kafka producer's send_and_wait was called
    mock_kafka_producer.send_and_wait.assert_called_once()


def test_update_order(create_test_database, mock_kafka_producer, mock_get_current_non_admin_user):
    """
    Tests updating an existing order as the owner.
    Expects 200 OK response and verifies order updates.
    """
    access_token = get_fake_regular_token()

    # First, create an order
    with Session(engine) as session:
        # Ensure the product exists
        product = ProductInventory(
            product_id=2,
            product_price=75.00,
            product_quantity=50
        )
        session.add(product)
        session.commit()
        session.refresh(product)

        order = Orders(
            user_id=2,
            customer_name="Jane Smith",
            customer_email="janesmith@example.com",
            customer_cellno="0987654321",
            customer_address="456 Elm St",
            product_id=2,
            order_quantity=5,
            product_price=75.00,
            order_total=375.00,
            order_status='Confirmed'
        )
        session.add(order)
        # Deduct from inventory
        product.product_quantity -= order.order_quantity
        session.add(product)
        session.commit()
        session.refresh(order)

    # Now, update the order
    updated_data = {
        "order_quantity": 3
    }

    response = client.patch(
        f"/orders/{order.order_id}",
        json=updated_data,
        headers={"Authorization": f"Bearer {access_token}"}
    )
    assert response.status_code == 200
    updated_order = response.json()
    assert updated_order["order_quantity"] == 3

    # Verify that the Kafka producer's send_and_wait was called
    mock_kafka_producer.send_and_wait.assert_called()


def test_update_order_status(create_test_database, mock_kafka_producer, mock_get_current_admin_user):
    """
    Tests updating an existing order as the owner.
    Expects 200 OK response and verifies order updates.
    """
    access_token = get_fake_regular_token()

    # First, create an order
    with Session(engine) as session:
        # Ensure the product exists
        product = ProductInventory(
            product_id=2,
            product_price=75.00,
            product_quantity=50
        )
        session.add(product)
        session.commit()
        session.refresh(product)

        order = Orders(
            user_id=2,
            customer_name="Jane Smith",
            customer_email="janesmith@example.com",
            customer_cellno="0987654321",
            customer_address="456 Elm St",
            product_id=2,
            order_quantity=5,
            product_price=75.00,
            order_total=375.00,
            order_status='Confirmed'
        )
        session.add(order)
        # Deduct from inventory
        product.product_quantity -= order.order_quantity
        session.add(product)
        session.commit()
        session.refresh(order)

    # Now, update the order
    updated_data = {
        "order_status": "Shipped"
    }

    response = client.patch(
        f"/orders/{order.order_id}",
        json=updated_data,
        headers={"Authorization": f"Bearer {access_token}"}
    )
    assert response.status_code == 200
    updated_order = response.json()
    assert updated_order["order_status"] == "Shipped"

    # Verify that the Kafka producer's send_and_wait was called
    mock_kafka_producer.send_and_wait.assert_called()    


def test_delete_order(create_test_database, mock_kafka_producer, mock_get_current_non_admin_user):
    """
    Tests deleting an existing order as the owner.
    Expects 204 No Content response and verifies order deletion.
    """
    access_token = get_fake_regular_token()

    # First, create an order
    with Session(engine) as session:
        # Ensure the product exists
        product = ProductInventory(
            product_id=3,
            product_price=100.00,
            product_quantity=20
        )
        session.add(product)
        session.commit()
        session.refresh(product)

        order = Orders(
            user_id=2,
            customer_name="Alice Johnson",
            customer_email="alicej@example.com",
            customer_cellno="5555555555",
            customer_address="789 Pine St",
            product_id=3,
            order_quantity=2,
            product_price=100.00,
            order_total=200.00,
            order_status='Confirmed'
        )
        session.add(order)
        # Deduct from inventory
        product.product_quantity -= order.order_quantity
        session.add(product)
        session.commit()
        session.refresh(order)

    # Now, delete the order
    response = client.delete(
        f"/orders/{order.order_id}",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    assert response.status_code == 204

    # Verify that the Kafka producer's send_and_wait was called
    mock_kafka_producer.send_and_wait.assert_called()

    # Verify the order has been deleted
    with Session(engine) as session:
        deleted_order = session.get(Orders, order.order_id)
        assert deleted_order is None

        # Verify that the inventory has been updated
        updated_product = session.get(ProductInventory, 3)
        assert updated_product.product_quantity == 20  # Quantity should be restored


def test_update_order_status(create_test_database, mock_kafka_producer, mock_get_current_admin_user):
    """
    Tests updating the status of an existing order as an admin.
    Expects 200 OK response and verifies status update.
    """
    access_token = get_fake_admin_token()

    # First, create an order
    with Session(engine) as session:
        # Ensure the product exists
        product = ProductInventory(
            product_id=4,
            product_price=150.00,
            product_quantity=10
        )
        session.add(product)
        session.commit()
        session.refresh(product)

        order = Orders(
            user_id=2,
            customer_name="Bob Williams",
            customer_email="bobw@example.com",
            customer_cellno="6666666666",
            customer_address="321 Maple St",
            product_id=4,
            order_quantity=1,
            product_price=150.00,
            order_total=150.00,
            order_status='Confirmed'
        )
        session.add(order)
        # Deduct from inventory
        product.product_quantity -= order.order_quantity
        session.add(product)
        session.commit()
        session.refresh(order)

    # Now, update the order status
    updated_status = {
        "order_status": "Completed"
    }

    response = client.patch(
        f"/orders/status/{order.order_id}",
        json=updated_status,
        headers={"Authorization": f"Bearer {access_token}"}
    )
    assert response.status_code == 200
    updated_order = response.json()
    assert updated_order["order_status"] == "Completed"

    # Verify that the Kafka producer's send_and_wait was called
    mock_kafka_producer.send_and_wait.assert_called()


# -------------------- Invalid Data and Error Handling Tests --------------------

def test_create_order_invalid_data(create_test_database, mock_kafka_producer, mock_get_current_non_admin_user):
    """
    Tests creating an order with invalid data.
    Expects 422 Unprocessable Entity response.
    """
    access_token = get_fake_regular_token()

    # Missing required fields: customer_name and product_id
    invalid_order_data = {
        "customer_email": "invalid@example.com",
        "customer_cellno": "9999999999",
        "customer_address": "No Name St",
        "order_quantity": 1
    }

    response = client.post(
        "/orders/",
        json=invalid_order_data,
        headers={"Authorization": f"Bearer {access_token}"}
    )

    if response.status_code != 200:
        print("Error Response:", response.json())  # Debugging line

    assert response.status_code == 422  # Unprocessable Entity


def test_update_nonexistent_order(create_test_database, mock_kafka_producer, mock_get_current_non_admin_user):
    """
    Tests updating a non-existent order.
    Expects 404 Not Found response.
    """
    access_token = get_fake_regular_token()

    updated_data = {
        "order_quantity": 5
    }

    response = client.patch(
        "/orders/9999",  # Assuming 9999 does not exist
        json=updated_data,
        headers={"Authorization": f"Bearer {access_token}"}
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Order not found"


def test_delete_nonexistent_order(create_test_database, mock_kafka_producer, mock_get_current_non_admin_user):
    """
    Tests deleting a non-existent order.
    Expects 404 Not Found response.
    """
    access_token = get_fake_regular_token()

    response = client.delete(
        "/orders/9999",  # Assuming 9999 does not exist
        headers={"Authorization": f"Bearer {access_token}"}
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Order not found"


def test_create_order_insufficient_inventory(create_test_database, mock_kafka_producer, mock_get_current_non_admin_user):
    """
    Tests creating an order with a quantity exceeding available inventory.
    Expects 400 Bad Request response.
    """
    access_token = get_fake_regular_token()

    # Ensure that the product exists in the inventory with limited quantity
    product = ProductInventory(
        product_id=5,
        product_price=200.00,
        product_quantity=1
    )
    with Session(engine) as session:
        session.add(product)
        session.commit()
        session.refresh(product)

    order_data = {
        "customer_name": "Charlie Brown",
        "customer_email": "charlieb@example.com",
        "customer_cellno": "7777777777",
        "customer_address": "654 Oak St",
        "product_id": 5,
        "order_quantity": 2  # Exceeds available quantity
    }

    response = client.post(
        "/orders/",
        json=order_data,
        headers={"Authorization": f"Bearer {access_token}"}
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Insufficient product quantity in inventory"


def test_update_order_unauthorized(create_test_database, mock_kafka_producer, mock_get_current_non_admin_user):
    """
    Tests updating an order that does not belong to the current user.
    Expects 403 Forbidden response.
    """
    access_token = get_fake_regular_token()

    # First, create an order belonging to a different user
    with Session(engine) as session:
        # Ensure the product exists
        product = ProductInventory(
            product_id=6,
            product_price=80.00,
            product_quantity=10
        )
        session.add(product)
        session.commit()
        session.refresh(product)

        order = Orders(
            user_id=3,  # Different user
            customer_name="David Lee",
            customer_email="davidl@example.com",
            customer_cellno="8888888888",
            customer_address="987 Cedar St",
            product_id=6,
            order_quantity=1,
            product_price=80.00,
            order_total=80.00,
            order_status='Confirmed'
        )
        session.add(order)
        # Deduct from inventory
        product.product_quantity -= order.order_quantity
        session.add(product)
        session.commit()
        session.refresh(order)

    # Attempt to update the order as user_id=2
    updated_data = {
        "order_quantity": 3
    }

    response = client.patch(
        f"/orders/{order.order_id}",
        json=updated_data,
        headers={"Authorization": f"Bearer {access_token}"}
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "You are not authorized to update this order."


def test_delete_order_unauthorized(create_test_database, mock_kafka_producer, mock_get_current_non_admin_user):
    """
    Tests deleting an order that does not belong to the current user.
    Expects 403 Forbidden response.
    """
    access_token = get_fake_regular_token()

    # First, create an order belonging to a different user
    with Session(engine) as session:
        # Ensure the product exists
        product = ProductInventory(
            product_id=7,
            product_price=60.00,
            product_quantity=15
        )
        session.add(product)
        session.commit()
        session.refresh(product)

        order = Orders(
            user_id=4,  # Different user
            customer_name="Eve Adams",
            customer_email="evea@example.com",
            customer_cellno="9999999999",
            customer_address="321 Birch St",
            product_id=7,
            order_quantity=2,
            product_price=60.00,
            order_total=120.00,
            order_status='Confirmed'
        )
        session.add(order)
        # Deduct from inventory
        product.product_quantity -= order.order_quantity
        session.add(product)
        session.commit()
        session.refresh(order)

    # Attempt to delete the order as user_id=2
    response = client.delete(
        f"/orders/{order.order_id}",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "You are not authorized to delete this order."

