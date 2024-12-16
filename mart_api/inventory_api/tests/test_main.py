# inventory_api/tests/test_main.py

import os

# Set the TESTING environment variable to use the test database
os.environ["TESTING"] = "1"

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session
from unittest.mock import AsyncMock, patch
from app.db import engine, get_session
from app.main import app, produce_message
from app.utils import get_current_admin_user, get_current_user
from app.models import Inventory, InventoryCreate, InventoryUpdate

client = TestClient(app)

# Fixture to create the test database and tables
@pytest.fixture(name="create_test_database")
def create_test_database_fixture():
    # Override the get_session dependency to use the test database session
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
    yield mock_get_current_admin_user # Yield the mock function
    # Remove only the specific dependency override
    app.dependency_overrides.pop(get_current_admin_user, None)

# Fixture to mock the get_current_user dependency for non-admin users
@pytest.fixture(name="mock_get_current_non_admin_user")
def mock_get_current_non_admin_user_fixture():
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

# Helper function to obtain a fake admin token (since we are mocking the dependency)
def get_fake_admin_token():
    return "fake-admin-token"

# Helper function to obtain a fake regular user token
def get_fake_regular_token():
    return "fake-regular-token"

# Test the GET /inventory/ endpoint
def test_get_inventory_items(create_test_database, mock_kafka_producer, mock_get_current_admin_user):
    access_token = get_fake_admin_token()

    response = client.get(
        "/inventory/",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    assert response.status_code == 200
    assert isinstance(response.json(), list)

# Test the POST /inventory/ endpoint to create a new inventory item
def test_create_inventory_item(create_test_database, mock_kafka_producer, mock_get_current_admin_user):
    access_token = get_fake_admin_token()

    item_data = {
        "item_name": "Test Item",
        "item_category": "Test Category",
        "item_price": 99.99,
        "item_quantity": 50,
        "item_status": "In Stock"
    }

    response = client.post(
        "/inventory/",
        json=item_data,
        headers={"Authorization": f"Bearer {access_token}"}
    )
    if response.status_code != 200:
        print("Error Response:", response.json())  # Debugging line
    assert response.status_code == 200
    response_data = response.json()
    assert response_data["item_name"] == "Test Item"
    assert response_data["item_category"] == "Test Category"
    assert response_data["item_price"] == 99.99
    assert response_data["item_quantity"] == 50
    assert response_data["item_status"] == "In Stock"

    # Verify that the Kafka producer's send_and_wait was called
    mock_kafka_producer.send_and_wait.assert_called_once()

# Test the PATCH /inventory/{item_id} endpoint to update an inventory item
def test_update_inventory_item(create_test_database, mock_kafka_producer, mock_get_current_admin_user):
    access_token = get_fake_admin_token()

    # First, create an inventory item
    item_data = {
        "item_name": "Update Test Item",
        "item_category": "Update Category",
        "item_price": 150.00,
        "item_quantity": 30,
        "item_status": "In Stock"
    }

    create_response = client.post(
        "/inventory/",
        json=item_data,
        headers={"Authorization": f"Bearer {access_token}"}
    )
    if create_response.status_code != 200:
        print("Error Response:", create_response.json())  # Debugging line
    assert create_response.status_code == 200
    created_item = create_response.json()
    item_id = created_item["item_id"]

    # Now, update the inventory item
    updated_data = {
        "item_price": 120.00,
        "item_quantity": 0, # Item_quantity needs to be zero for Out of Stock items
        "item_status": "Out of Stock"
    }

    update_response = client.patch(
        f"/inventory/{item_id}",
        json=updated_data,
        headers={"Authorization": f"Bearer {access_token}"}
    )
    assert update_response.status_code == 200
    updated_item = update_response.json()
    assert updated_item["item_price"] == 120.00
    assert updated_item["item_quantity"] == 0
    assert updated_item["item_status"] == "Out of Stock"

    # Verify that the Kafka producer's send_and_wait was called
    mock_kafka_producer.send_and_wait.assert_called()

# Test the DELETE /inventory/{item_id} endpoint to delete an inventory item
def test_delete_inventory_item(create_test_database, mock_kafka_producer, mock_get_current_admin_user):
    access_token = get_fake_admin_token()

    # First, create an inventory item
    item_data = {
        "item_name": "Delete Test Item",
        "item_category": "Delete Category",
        "item_price": 200.00,
        "item_quantity": 10,
        "item_status": "In Stock"
    }

    create_response = client.post(
        "/inventory/",
        json=item_data,
        headers={"Authorization": f"Bearer {access_token}"}
    )

    if create_response.status_code != 200:
        print("Error Response:", create_response.json())  # Debugging line

    assert create_response.status_code == 200
    created_item = create_response.json()
    item_id = created_item["item_id"]

    # Now, delete the inventory item
    delete_response = client.delete(
        f"/inventory/{item_id}",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    assert delete_response.status_code == 204

    # Verify that the Kafka producer's send_and_wait was called
    mock_kafka_producer.send_and_wait.assert_called()

    # Verify the item has been deleted
    get_response = client.get(
        "/inventory/",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    assert get_response.status_code == 200
    items = get_response.json()
    assert all(item["item_id"] != item_id for item in items)

# Test creating an inventory item with invalid data
def test_create_inventory_item_invalid(create_test_database, mock_kafka_producer, mock_get_current_admin_user):
    access_token = get_fake_admin_token()

    # Missing required fields
    invalid_item_data = {
        "item_name": "Invalid Item"
        # Missing other required fields
    }

    response = client.post(
        "/inventory/",
        json=invalid_item_data,
        headers={"Authorization": f"Bearer {access_token}"}
    )

    if response.status_code != 200:
        print("Error Response:", response.json())  # Debugging line
    
    assert response.status_code == 422  # Unprocessable Entity

# Test updating a non-existent inventory item
def test_update_nonexistent_inventory_item(create_test_database, mock_kafka_producer, mock_get_current_admin_user):
    access_token = get_fake_admin_token()

    updated_data = {
        "item_price": 100.00
    }

    response = client.patch(
        "/inventory/9999",  # Assuming 9999 does not exist
        json=updated_data,
        headers={"Authorization": f"Bearer {access_token}"}
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Item not found"

# Test deleting a non-existent inventory item
def test_delete_nonexistent_inventory_item(create_test_database, mock_kafka_producer, mock_get_current_admin_user):
    access_token = get_fake_admin_token()

    response = client.delete(
        "/inventory/9999",  # Assuming 9999 does not exist
        headers={"Authorization": f"Bearer {access_token}"}
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Item not found"

# Test unauthorized access to inventory endpoints
def test_unauthorized_access_inventory(create_test_database, mock_kafka_producer):
    # No access token provided
    invalid_token = "invalid-token"
    response = client.get(
        f"/inventory/?token={invalid_token}"
    )
    #response = client.get("/inventory/")

    if response.status_code != 401:
        print("Error Response:", response.json())  # Debugging line

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid authentication credentials"



# Test forbidden access to inventory endpoints(Non-admin user)
def test_forbidden_access_inventory(create_test_database, mock_kafka_producer, mock_get_current_non_admin_user):
    
        access_token = "fake-regular-token"
        response = client.get(
        f"/inventory/?token={access_token}"
    )
        # response = client.get(
        #     "/inventory/",
        #     headers={"Authorization": f"Bearer {access_token}"}
        # )
        
        if response.status_code != 403:
            print("Error Response:", response.json())  # Debugging line

        assert response.status_code == 403
        assert response.json()["detail"] == "Insufficient permissions."


# Test that non-admin users cannot perform create, update, delete operations
def test_non_admin_operations_inventory(create_test_database, mock_kafka_producer, mock_get_current_non_admin_user):
   
        access_token = "fake-regular-token"

        # Attempt to create an inventory item
        item_data = {
            "item_name": "Unauthorized Item",
            "item_category": "Unauthorized Category",
            "item_price": 50.00,
            "item_quantity": 20,
            "item_status": "In Stock"
        }

        response = client.post(
            "/inventory/",
            json=item_data,
            headers={"Authorization": f"Bearer {access_token}"}
        )

        response = client.get(
        f"/inventory/?token={access_token}"
    )

        if response.status_code != 403:
            print("Error Response:", response.json())  # Debugging line

        assert response.status_code == 403
        assert response.json()["detail"] == "Insufficient permissions."

        # Attempt to update an inventory item
        response = client.patch(
            "/inventory/1",  # Assuming item_id 1 exists
            json={"item_price": 60.00},
            headers={"Authorization": f"Bearer {access_token}"}
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "Insufficient permissions."

        # Attempt to delete an inventory item
        response = client.delete(
            "/inventory/1",  # Assuming item_id 1 exists
            headers={"Authorization": f"Bearer {access_token}"}
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "Insufficient permissions."
