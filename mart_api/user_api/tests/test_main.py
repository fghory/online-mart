# user_api/tests/test_main.py

import os

# Set the TESTING environment variable to use the test database
os.environ["TESTING"] = "1"

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session
from unittest.mock import AsyncMock, patch
from app.db import engine, get_session
from app.main import app
from app.utils import get_password_hash
from app.models import User, Role


client = TestClient(app)

# Fixture to create the database and tables for testing
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
    # Uncomment the following line if you want to drop tables after tests
    SQLModel.metadata.drop_all(engine)

    app.dependency_overrides.clear()

# Fixture to mock the Kafka producer
@pytest.fixture
def mock_kafka_producer():
    with patch("app.main.producer") as mock_producer:
        mock_producer.send_and_wait = AsyncMock(return_value=None)
        yield mock_producer

# Test the /register/ POST endpoint to register a new user
def test_register_user(create_test_database, mock_kafka_producer):
    user_data = {
        "user_name": "testuser",
        "user_email": "testuser@example.com",
        "user_cellno": "1234567890",
        "user_address": "Test Address",
        "password": "testpassword"
    }

    response = client.post("/register/", json=user_data)
    assert response.status_code == 201
    response_data = response.json()
    assert response_data["user_name"] == "testuser"
    assert response_data["user_email"] == "testuser@example.com"
    assert "id" in response_data
    assert "hashed_password" not in response_data
    assert response_data["role"] == "CUSTOMER"  # Default role

    # Verify that the Kafka producer's send_and_wait was called
    mock_kafka_producer.send_and_wait.assert_called_once()

# Test user registration with missing fields
def test_register_user_incomplete(create_test_database):
    user_data = {
        "user_name": "testuser2",
        "user_email": "testuser2@example.com",
        "password": "testpassword"
    }

    response = client.post("/register/", json=user_data)
    assert response.status_code == 422  # Unprocessable Entity

# Test user registration with invalid data
def test_register_user_invalid(create_test_database):
    user_data = {
        "user_name": "testuser3",
        "user_email": 12345,  # Invalid email format
        "user_cellno": 111222333,  # Invalid cell number
        "user_address": "Test Address",
        "password": "testpassword"
    }

    response = client.post("/register/", json=user_data)
    assert response.status_code == 422  # Unprocessable Entity

# Test the /token/ POST endpoint for user login
def test_login_user(create_test_database, mock_kafka_producer):
    # First, register a user
    user_data = {
        "user_name": "testuser4",
        "user_email": "testuser4@example.com",
        "user_cellno": "4444444444",
        "user_address": "Test Address",
        "password": "testpassword4"
    }

    response = client.post("/register/", json=user_data)
    assert response.status_code == 201

    # Now, attempt to login
    response = client.post(
        "/token/",
        data={
            "username": "testuser4",
            "password": "testpassword4"
        }
    )

    assert response.status_code == 200
    response_data = response.json()
    assert "access_token" in response_data
    assert response_data["token_type"] == "bearer"

    # Verify that the Kafka producer's send_and_wait was called during login
    mock_kafka_producer.send_and_wait.assert_called()

# Test login with incorrect credentials
def test_login_user_incorrect_credentials(create_test_database):
    response = client.post(
        "/token/",
        data={
            "username": "nonexistentuser",
            "password": "wrongpassword"
        }
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Incorrect username."

# Test the /users/me/ GET endpoint to retrieve current user information
def test_get_current_user(create_test_database):
    # Register and login a user
    user_data = {
        "user_name": "testuser5",
        "user_email": "testuser5@example.com",
        "user_cellno": "5555555555",
        "user_address": "Test Address",
        "password": "testpassword5"
    }

    response = client.post("/register/", json=user_data)
    assert response.status_code == 201

    # Login to get the access token
    response = client.post(
        "/token/",
        data={
            "username": "testuser5",
            "password": "testpassword5"
        }
    )
    assert response.status_code == 200
    access_token = response.json()["access_token"]

    # Get current user information
    response = client.get(
        "/users/me/",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    assert response.status_code == 200
    response_data = response.json()
    assert response_data["user_name"] == "testuser5"
    assert response_data["user_email"] == "testuser5@example.com"
    assert response_data["user_cellno"] == "5555555555"
    assert response_data["user_address"] == "Test Address"
    assert response_data["role"] == "CUSTOMER"

# Test updating a user's information
def test_update_user(create_test_database, mock_kafka_producer):
    # Register and login a user
    user_data = {
        "user_name": "testuser6",
        "user_email": "testuser6@example.com",
        "user_cellno": "6666666666",
        "user_address": "Old Address",
        "password": "testpassword6"
    }

    response = client.post("/register/", json=user_data)
    assert response.status_code == 201
    user_id = response.json()["id"]

    # Login to get the access token
    response = client.post(
        "/token/",
        data={
            "username": "testuser6",
            "password": "testpassword6"
        }
    )
    assert response.status_code == 200
    access_token = response.json()["access_token"]

    # Update user information
    updated_data = {
        "user_address": "New Address",
        "user_cellno": "6666666667"
    }

    response = client.patch(
        f"/users/{user_id}/",
        json=updated_data,
        headers={"Authorization": f"Bearer {access_token}"}
    )
    assert response.status_code == 200
    response_data = response.json()
    assert response_data["user_address"] == "New Address"
    assert response_data["user_cellno"] == "6666666667"

    # Verify that the Kafka producer's send_and_wait was called during update
    mock_kafka_producer.send_and_wait.assert_called()

# Test deleting a user (admin only)
def test_delete_user(create_test_database, mock_kafka_producer):
    # Create an admin user directly in the database
    with Session(engine) as session:
        hashed_password = get_password_hash("adminpassword")
        admintest_user = User(
            user_name="admintestuser",
            user_email="admintest@example.com",
            user_cellno="0000000000",
            user_address="Admin Address",
            hashed_password=hashed_password,
            role=Role.ADMIN
        )
        session.add(admintest_user)
        session.commit()
        session.refresh(admintest_user)

    # Login as admin
    response = client.post(
        "/token/",
        data={
            "username": "admintestuser",
            "password": "adminpassword"
        }
    )
    assert response.status_code == 200
    admin_access_token = response.json()["access_token"]

    # Register a regular user to delete
    user_data = {
        "user_name": "testuser7",
        "user_email": "testuser7@example.com",
        "user_cellno": "7777777777",
        "user_address": "Test Address",
        "password": "testpassword7"
    }

    response = client.post("/register/", json=user_data)
    assert response.status_code == 201
    user_id = response.json()["id"]

    # Delete the user as admin
    response = client.delete(
        f"/users/{user_id}/",
        headers={"Authorization": f"Bearer {admin_access_token}"}
    )
    assert response.status_code == 204

    # Verify the user has been deleted
    response = client.get(
        f"/users/{user_id}/",
        headers={"Authorization": f"Bearer {admin_access_token}"}
    )
    assert response.status_code == 404

    # Verify that the Kafka producer's send_and_wait was called during delete
    mock_kafka_producer.send_and_wait.assert_called()

# Test that non-admin users cannot delete users
def test_delete_user_non_admin(create_test_database):
    # Register and login a regular user
    user_data = {
        "user_name": "testuser8",
        "user_email": "testuser8@example.com",
        "user_cellno": "8888888888",
        "user_address": "Test Address",
        "password": "testpassword8"
    }

    response = client.post("/register/", json=user_data)
    assert response.status_code == 201
    user_id = response.json()["id"]

    response = client.post(
        "/token/",
        data={
            "username": "testuser8",
            "password": "testpassword8"
        }
    )
    assert response.status_code == 200
    access_token = response.json()["access_token"]

    # Attempt to delete another user (should fail)
    response = client.delete(
        f"/users/{user_id}/",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Insufficient permissions."

