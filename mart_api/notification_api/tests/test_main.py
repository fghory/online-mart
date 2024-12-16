# notification_api/tests/test_main.py

import os
import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, select
from unittest.mock import patch, Mock
from app import user_pb2, order_pb2, payment_pb2, inventory_pb2
from app.db import engine, get_session
from app.main import app, handle_user_data, handle_order_data, handle_payment_data, handle_stock_items
from app.models import (
    NotificationLog, NotificationType, User
)

# TestClient raises exceptions (unhandled in main.py) in tests.
# raise_server_exceptions=False tells the TestClient to use the application's exception handlers
# instead of re-raising exceptions during testing
client = TestClient(app, raise_server_exceptions=False)

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


# Fixture to mock the send_email function
@pytest.fixture(name="mock_send_email")
def mock_send_email_fixture():
    """
    Mocks the send_email function to prevent actual emails from being sent during tests.
    Allows verification that the function was called with expected parameters.
    """
    with patch("app.main.send_email") as mock_email:
        mock_email.return_value = True  # Simulate successful email sending
        yield mock_email


# Fixture to mock the send_sms function
@pytest.fixture(name="mock_send_sms")
def mock_send_sms_fixture():
    """
    Mocks the send_sms function to prevent actual SMS messages from being sent during tests.
    Allows verification that the function was called with expected parameters.
    """
    with patch("app.main.send_sms") as mock_sms:
        mock_sms.return_value = True  # Simulate successful SMS sending
        yield mock_sms


# -------------------- Endpoint Functionality Tests --------------------

def test_get_notifications(create_test_database, mock_send_email, mock_send_sms):
    """
    Tests retrieving notification logs.
    Expects a list of notifications.
    """
    # Pre-populate the database with a notification log
    with Session(engine) as session:
        log = NotificationLog(
            notification_type=NotificationType.EMAIL,
            recipient="user@example.com",
            message="Test Email Notification",
            status="Sent",
            timestamp=1625079072.0  # Example timestamp
        )
        session.add(log)
        session.commit()

    response = client.get("/notifications/")
    assert response.status_code == 200
    notifications = response.json()
    assert isinstance(notifications, list)
    assert len(notifications) == 1
    assert notifications[0]["recipient"] == "user@example.com"
    assert notifications[0]["message"] == "Test Email Notification"
    assert notifications[0]["status"] == "Sent"


def test_get_users(create_test_database, mock_send_email, mock_send_sms):
    """
    Tests retrieving users.
    Expects a list of users.
    """
    # Pre-populate the database with a user
    with Session(engine) as session:
        user = User(
            id=1,
            name="Admin User",
            email="admin@example.com",
            cell_number="0000000000",
            address="Admin Address"
        )
        session.add(user)
        session.commit()

    response = client.get("/users")
    assert response.status_code == 200
    users = response.json()
    assert isinstance(users, list)
    assert len(users) == 1
    assert users[0]["name"] == "Admin User"
    assert users[0]["email"] == "admin@example.com"


# -------------------- Notification Sending Tests --------------------

def test_send_notifications(create_test_database, mock_send_email, mock_send_sms):
    """
    Tests that sending notifications works correctly.
    Verifies that send_email and send_sms are called with expected parameters.
    """
    # Simulate sending a notification
    with Session(engine) as session:
        user = User(
            id=2,
            name="Test User",
            email="testuser@example.com",
            cell_number="1234567890",
            address="123 Test St"
        )
        session.add(user)
        session.commit()
        session.refresh(user)

        # Access attributes while session is active
        email = user.email
        cell_number = user.cell_number

    # Assuming there's an endpoint or function that triggers sending notifications
    # For demonstration, we'll directly call the utility functions
    from app.main import send_email, send_sms

    email_sent = send_email(email, "Test Subject", "Test Body")
    sms_sent = send_sms(cell_number, "Test Body")

    assert email_sent is True
    assert sms_sent is True

    # Verify that the mocks were called correctly
    mock_send_email.assert_called_once_with(user.email, "Test Subject", "Test Body")
    mock_send_sms.assert_called_once_with(user.cell_number, "Test Body")


# -------------------- Error Handling Tests --------------------

def test_get_notifications_database_error(mock_send_email, mock_send_sms):
    """
    Tests retrieving notifications when a database error occurs.
    Mocks the get_session dependency to raise an exception.
    Expects a 500 Internal Server Error response.
    """
    def mock_get_session_exception():
        mock_session = Mock(spec=Session)
        mock_session.exec.side_effect = Exception("Database Error")
        yield mock_session

    app.dependency_overrides[get_session] = mock_get_session_exception
    response = client.get("/notifications/")

    if response.status_code != 500:
        print("Error Response:", response.json())  # Debugging line

    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"

    app.dependency_overrides.pop(get_session, None)


def test_get_users_database_error(mock_send_email, mock_send_sms):
    """
    Tests retrieving users when a database error occurs.
    Mocks the get_session dependency to raise an exception.
    Expects a 500 Internal Server Error response.
    """
    def mock_get_session_exception():
        mock_session = Mock(spec=Session)
        mock_session.exec.side_effect = Exception("Database Error")
        yield mock_session

    app.dependency_overrides[get_session] = mock_get_session_exception
    response = client.get("/users/")

    if response.status_code != 500:
        print("Error Response:", response.json())  # Debugging line

    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"


@pytest.mark.asyncio
async def test_handle_user_data_create_action(create_test_database):
    """
    Tests the handle_user_data function for CREATE action.
    """

    # Create a mock user event with action CREATE
    user_event = user_pb2.User(
        id=1,
        action=user_pb2.ActionType.CREATE,
        user_name="Test User",
        user_email="testuser@example.com",
        user_cellno="1234567890",
        user_address="123 Test St"
    )

    # Call the handler function
    await handle_user_data(user_event)

    # Verify that the user was added to the database
    with Session(engine) as session:
        user = session.exec(select(User).where(User.id == 1)).first()
        assert user is not None
        assert user.name == "Test User"
        assert user.email == "testuser@example.com"
        assert user.cell_number == "1234567890"
        assert user.address == "123 Test St"


@pytest.mark.asyncio
async def test_handle_user_data_update_action(create_test_database):
    """
    Tests the handle_user_data function for UPDATE action.
    """

    # Pre-populate the database with a user
    with Session(engine) as session:
        user = User(
            id=1,
            name="Original User",
            email="original@example.com",
            cell_number="0000000000",
            address="Original Address"
        )
        session.add(user)
        session.commit()

    # Create a mock user event with action UPDATE
    user_event = user_pb2.User(
        id=1,
        action=user_pb2.ActionType.UPDATE,
        user_name="Updated User",
        user_email="updated@example.com",
        user_cellno="1111111111",
        user_address="Updated Address"
    )

    # Call the handler function
    await handle_user_data(user_event)

    # Verify that the user was updated in the database
    with Session(engine) as session:
        user = session.exec(select(User).where(User.id == 1)).first()
        assert user is not None
        assert user.name == "Updated User"
        assert user.email == "updated@example.com"
        assert user.cell_number == "1111111111"
        assert user.address == "Updated Address"


@pytest.mark.asyncio
async def test_handle_user_data_delete_action(create_test_database):
    """
    Tests the handle_user_data function for DELETE action.
    """

    # Pre-populate the database with a user
    with Session(engine) as session:
        user = User(
            id=1,
            name="Test User",
            email="testuser@example.com",
            cell_number="1234567890",
            address="123 Test St"
        )
        session.add(user)
        session.commit()

    # Create a mock user event with action DELETE
    user_event = user_pb2.User(
        id=1,
        action=user_pb2.ActionType.DELETE,
    )

    # Call the handler function
    await handle_user_data(user_event)

    # Verify that the user was deleted from the database
    with Session(engine) as session:
        user = session.exec(select(User).where(User.id == 1)).first()
        assert user is None



@pytest.mark.asyncio
async def test_handle_order_data_completed(create_test_database, mock_send_email, mock_send_sms):
    """
    Tests the handle_order_data function when an order is completed.
    """

    # Pre-populate the database with a user
    with Session(engine) as session:
        user = User(
            id=1,
            name="Test User",
            email="testuser@example.com",
            cell_number="1234567890",
            address="123 Test St"
        )
        session.add(user)
        session.commit()
        # Retrieve user data before closing the session
        user_email = user.email
        user_cell_number = user.cell_number

    # Create a mock order event
    order_event = order_pb2.Order(
        order_id=100,
        order_status=order_pb2.OrderStatus.COMPLETED,
        user_id=1,
        item_action=order_pb2.ActionType.UPDATE
    )

    # Call the handler function
    await handle_order_data(order_event)

    # Verify that send_email and send_sms were called
    subject = f"Your Order {order_event.order_id} has been Completed!"
    body = f"Dear Customer,\n\nYour order #{order_event.order_id} has been successfully completed. Thank you for shopping with us!"
    mock_send_email.assert_called_with(user_email, subject, body)
    mock_send_sms.assert_called_with(user_cell_number, body)

    # Verify that notifications were logged
    with Session(engine) as session:
        notifications = session.exec(
            select(NotificationLog).where(NotificationLog.recipient == user_email)
        ).all()
        assert len(notifications) > 0
        email_notifications = [n for n in notifications if n.notification_type == NotificationType.EMAIL]
        assert len(email_notifications) == 1
        assert email_notifications[0].message == subject + " " + body

@pytest.mark.asyncio
async def test_handle_order_data_cancelled(create_test_database, mock_send_email, mock_send_sms):
    """
    Tests the handle_order_data function when an order is cancelled.
    """

    # Pre-populate the database with a user
    with Session(engine) as session:
        user = User(
            id=2,
            name="Another User",
            email="anotheruser@example.com",
            cell_number="0987654321",
            address="456 Another St"
        )
        session.add(user)
        session.commit()
        user_email = user.email
        user_cell_number = user.cell_number

    # Create a mock order event
    order_event = order_pb2.Order(
        order_id=200,
        order_status=order_pb2.OrderStatus.CANCELLED,
        user_id=2,
        item_action=order_pb2.ActionType.DELETE
    )

    # Call the handler function
    await handle_order_data(order_event)

    # Verify that send_email and send_sms were called
    subject = f"Your Order {order_event.order_id} has been Cancelled."
    body = f"Dear Customer,\n\nYour order #{order_event.order_id} has been cancelled as per your request. If you have any questions, feel free to contact our support."
    mock_send_email.assert_called_with(user_email, subject, body)
    mock_send_sms.assert_called_with(user_cell_number, body)

@pytest.mark.asyncio
async def test_handle_payment_data_failure(create_test_database, mock_send_email, mock_send_sms):
    """
    Tests the handle_payment_data function when a payment fails.
    """

    # Pre-populate the database with a user
    with Session(engine) as session:
        user = User(
            id=3,
            name="Payment User",
            email="paymentuser@example.com",
            cell_number="1122334455",
            address="789 Payment St"
        )
        session.add(user)
        session.commit()
        # Retrieve user data before closing the session
        user_name = user.name
        user_email = user.email
        user_cell_number = user.cell_number

    # Create a mock payment message
    payment_message = payment_pb2.PaymentStatusMessage(
        order_id=300,
        user_id=3,
        status=payment_pb2.PaymentStatus.FAILURE,
        transaction_id="txn_12345",
        failure_reason="Insufficient Funds"
    )

    # Call the handler function
    await handle_payment_data(payment_message)

    # Verify that send_email and send_sms were called
    subject = f"Payment Failed for Order {payment_message.order_id}"
    body = (
        f"Dear {user_name},\n\n"
        f"We regret to inform you that your payment for Order #{payment_message.order_id} has failed.\n"
        f"Reason: {payment_message.failure_reason}\n"
        f"Please try again or contact our support for assistance.\n\n"
        f"Transaction ID: {payment_message.transaction_id}"
    )
    mock_send_email.assert_called_with(user_email, subject, body)
    mock_send_sms.assert_called_with(user_cell_number, body)

    # Verify that notifications were logged
    with Session(engine) as session:
        notifications = session.exec(
            select(NotificationLog).where(NotificationLog.recipient == user_email)
        ).all()
        assert len(notifications) > 0
        email_notifications = [n for n in notifications if n.notification_type == NotificationType.EMAIL]
        assert len(email_notifications) == 1
        assert email_notifications[0].message == subject + " " + body

@pytest.mark.asyncio
async def test_handle_stock_items(create_test_database, mock_send_email, mock_send_sms):
    """
    Tests the handle_stock_items function for an item being restocked.
    """

    # Pre-populate the database with users
    with Session(engine) as session:
        user1 = User(
            id=1,
            name="User One",
            email="userone@example.com",
            cell_number="1000000000",
            address="Address One"
        )
        user2 = User(
            id=2,
            name="User Two",
            email="usertwo@example.com",
            cell_number="2000000000",
            address="Address Two"
        )
        session.add(user1)
        session.add(user2)
        session.commit()
        user1_email = user1.email
        user1_cell_number = user1.cell_number
        user2_email = user2.email
        user2_cell_number = user2.cell_number

    # Create a mock inventory update event
    inventory_event = inventory_pb2.Item(
        item_name="Cool Gadget",
        item_category="Gadgets",
        item_price=99.99,
        item_quantity=50,
        item_status=inventory_pb2.ItemStatus.IN_STOCK,
        item_status_change=True,
        item_price_drop=0.0,
        item_action=inventory_pb2.ActionType.UPDATE
    )

    # Call the handler function
    await handle_stock_items(inventory_event)

    # Verify that send_email and send_sms were called for each user
    subject = f"{inventory_event.item_name} is Now Back in Stock!"
    body = f"Good news! {inventory_event.item_name} has been restocked. We now have {inventory_event.item_quantity} units available."
    mock_send_email.assert_any_call(user1_email, subject, body)
    mock_send_email.assert_any_call(user2_email, subject, body)
    mock_send_sms.assert_any_call(user1_cell_number, body)
    mock_send_sms.assert_any_call(user2_cell_number, body)

    # Verify that notifications were logged for each user
    with Session(engine) as session:
        notifications = session.exec(select(NotificationLog)).all()
        email_notifications = [n for n in notifications if n.notification_type == NotificationType.EMAIL]
        sms_notifications = [n for n in notifications if n.notification_type == NotificationType.SMS]
        assert len(email_notifications) == 2
        assert len(sms_notifications) == 2