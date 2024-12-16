# payment_service/tests/test_main.py

import os

# Set the TESTING environment variable to use the test database
os.environ["TESTING"] = "1"

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session
from unittest.mock import AsyncMock, patch, Mock, ANY
from app.db import engine, get_session
from app.main import app, produce_message
from app.utils import get_current_user, get_admin_user
from app.models import (
    Payment,
    PaymentCreate,
    PaymentRead,
    PaymentStatus,
    PaymentMethod,
    OrderPayment,
)
from datetime import datetime, timezone

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


# Fixture to mock Stripe's PaymentIntent.create method
@pytest.fixture(name="mock_stripe_payment_intent_create")
def mock_stripe_payment_intent_create_fixture():
    with patch('stripe.PaymentIntent.create') as mock_create:
        yield mock_create


# Fixture to mock the get_current_admin_user dependency
@pytest.fixture(name="mock_get_current_admin_user")
def mock_get_current_admin_user_fixture():
    async def mock_get_current_admin_user():
        return {
            'id': 1,
            'role': 'ADMIN',
            'user_name': 'adminuser',
            'user_email': 'admin@example.com',
            'user_cellno': '0000000000',
            'user_address': 'Admin Address'
        }
    app.dependency_overrides[get_admin_user] = mock_get_current_admin_user
    yield mock_get_current_admin_user  # Yield the mock function
    # Remove only the specific dependency override
    app.dependency_overrides.pop(get_admin_user, None)


# Fixture to mock the get_current_user dependency for non-admin users
@pytest.fixture(name="mock_get_current_non_admin_user")
def mock_get_current_non_admin_user_fixture():
    async def mock_get_current_non_admin_user():
        return {
            'id': 2,
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


# Test the GET /payments/ endpoint for admin users
def test_get_payments_admin(create_test_database, mock_kafka_producer, mock_get_current_admin_user):
    access_token = get_fake_admin_token()

    # Create some payments in the database
    with Session(engine) as session:
        payment1 = Payment(
            payment_id=1,
            order_id=1,
            user_id=2,
            amount=100.0,
            currency="usd",
            status=PaymentStatus.SUCCESS,
            payment_method=PaymentMethod.STRIPE,
            transaction_id="pi_12345",
            timestamp=datetime.now(timezone.utc),
        )
        payment2 = Payment(
            payment_id=2,
            order_id=2,
            user_id=3,
            amount=200.0,
            currency="usd",
            status=PaymentStatus.FAILURE,
            payment_method=PaymentMethod.STRIPE,
            transaction_id="pi_67890",
            timestamp=datetime.now(timezone.utc),
        )
        session.add(payment1)
        session.add(payment2)
        session.commit()

    response = client.get(
        "/payments/",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert len(response.json()) == 2


# Test the POST /payments/ endpoint to create a new payment
def test_create_payment_success(create_test_database, mock_kafka_producer, mock_stripe_payment_intent_create, mock_get_current_non_admin_user):
    access_token = get_fake_regular_token()

    # First, create an eligible order for the user in the database
    with Session(engine) as session:
        eligible_order = OrderPayment(
            order_id=1,
            user_id=2,  # Assuming user_id=2 is the regular user
            order_total=100.0,
            currency="usd",
            customer_address="Regular Address",
            status="New",
            timestamp=datetime.now(timezone.utc),
        )
        session.add(eligible_order)
        session.commit()

    # Mock Stripe PaymentIntent.create to return a successful payment intent
    mock_payment_intent = Mock()
    mock_payment_intent.id = "pi_12345"
    mock_payment_intent.status = "succeeded"
    mock_stripe_payment_intent_create.return_value = mock_payment_intent

      # Add the user to the user_sessions to simulate that the user is logged in
    from app.main import user_sessions
    user_sessions[2] = {
        'role': 'CUSTOMER',
        'user_name': 'regularuser',
        'user_email': 'regular@example.com',
        'user_cellno': '1111111111',
        'user_address': 'Regular Address'
    }

    # Prepare the payment data
    payment_data = {
        "payment_method_id": "pm_card_visa",  # Use a test PaymentMethod ID
    }

    response = client.post(
        "/payments/",
        json=payment_data,
        headers={"Authorization": f"Bearer {access_token}"}
    )
    if response.status_code != 200:
        print("Error Response:", response.json())  # Debugging line
    assert response.status_code == 200
    response_data = response.json()
    assert response_data["order_id"] == 1
    assert response_data["user_id"] == 2
    assert response_data["status"] == "Success"

    # Verify that the Kafka producer's send_and_wait was called
    mock_kafka_producer.send_and_wait.assert_called_once()

    # Clean up user_sessions
    user_sessions.pop(2, None)


# Test the GET /payments/{payment_id} endpoint
def test_get_payment(create_test_database, mock_get_current_non_admin_user):
    access_token = get_fake_regular_token()

    # First, create a payment in the database
    with Session(engine) as session:
        payment = Payment(
            payment_id=1,
            order_id=1,
            user_id=2,
            amount=100.0,
            currency="usd",
            status=PaymentStatus.SUCCESS,
            payment_method=PaymentMethod.STRIPE,
            transaction_id="pi_12345",
            timestamp=datetime.now(timezone.utc),
        )
        session.add(payment)
        session.commit()

    response = client.get(
        "/payments/1",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    assert response.status_code == 200
    response_data = response.json()
    assert response_data["payment_id"] == 1
    assert response_data["user_id"] == 2
    assert response_data["status"] == "Success"


# Test unauthorized access to payments endpoints
def test_unauthorized_access_payments(create_test_database, mock_kafka_producer):
    # No access token provided
    response = client.get("/payments/?token")
    if response.status_code != 401:
        print("Error Response:", response.json())  # Debugging line
    assert response.status_code == 401
    assert response.json()["detail"] == "Could not validate credentials."


# Test forbidden access to payments endpoints (Non-admin user trying to access admin-only endpoints)
def test_forbidden_access_payments_admin(create_test_database, mock_kafka_producer, mock_get_current_non_admin_user):
    access_token = get_fake_regular_token()

    response = client.get(
        "/payments/",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    if response.status_code != 403:
        print("Error Response:", response.json())  # Debugging line
    assert response.status_code == 403
    assert response.json()["detail"] == "Insufficient permissions."


# Test that non-admin users cannot access other users' payments
def test_non_admin_access_other_user_payment(create_test_database, mock_get_current_non_admin_user):
    access_token = get_fake_regular_token()

    # Create a payment for a different user
    with Session(engine) as session:
        payment = Payment(
            payment_id=1,
            order_id=1,
            user_id=3,  # Different user_id
            amount=100.0,
            currency="usd",
            status=PaymentStatus.SUCCESS,
            payment_method=PaymentMethod.STRIPE,
            transaction_id="pi_12345",
            timestamp=datetime.now(timezone.utc),
        )
        session.add(payment)
        session.commit()

    response = client.get(
        "/payments/1",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    if response.status_code != 403:
        print("Error Response:", response.json())  # Debugging line
    assert response.status_code == 403
    assert response.json()["detail"] == "Not authorized to view this payment"


# Test creating a payment with no eligible orders
def test_create_payment_no_eligible_order(create_test_database, mock_kafka_producer, mock_stripe_payment_intent_create, mock_get_current_non_admin_user):
    access_token = get_fake_regular_token()

    # Add the user to the user_sessions to simulate that the user is logged in
    from app.main import user_sessions
    user_sessions[2] = {
        'role': 'CUSTOMER',
        'user_name': 'regularuser',
        'user_email': 'regular@example.com',
        'user_cellno': '1111111111',
        'user_address': 'Regular Address'
    }

    # No eligible orders in the database for the user

    # Prepare the payment data
    payment_data = {
        "payment_method_id": "pm_card_visa",  # Use a test PaymentMethod ID
    }

    response = client.post(
        "/payments/",
        json=payment_data,
        headers={"Authorization": f"Bearer {access_token}"}
    )
    if response.status_code != 404:
        print("Error Response:", response.json())  # Debugging line
    assert response.status_code == 404
    assert response.json()["detail"] == "No eligible orders found for payment."

    # Clean up user_sessions
    user_sessions.pop(2, None)


# Test creating a payment when the order does not belong to the user
def test_create_payment_order_not_belong_to_user(create_test_database, mock_kafka_producer, mock_stripe_payment_intent_create, mock_get_current_non_admin_user):
    access_token = get_fake_regular_token()

    # Add the user to the user_sessions to simulate that the user is logged in
    from app.main import user_sessions
    user_sessions[2] = {
        'role': 'CUSTOMER',
        'user_name': 'regularuser',
        'user_email': 'regular@example.com',
        'user_cellno': '1111111111',
        'user_address': 'Regular Address'
    }

    # Create an eligible order for a different user
    with Session(engine) as session:
        eligible_order = OrderPayment(
            order_id=1,
            user_id=3,  # Different user_id
            order_total=100.0,
            currency="usd",
            customer_address="Another Address",
            status="Confirmed",
            timestamp=datetime.now(timezone.utc),
        )
        session.add(eligible_order)
        session.commit()

    # Prepare the payment data
    payment_data = {
        "payment_method_id": "pm_card_visa",  # Use a test PaymentMethod ID
    }

    response = client.post(
        "/payments/",
        json=payment_data,
        headers={"Authorization": f"Bearer {access_token}"}
    )
    if response.status_code != 404:
        print("Error Response:", response.json())  # Debugging line
    assert response.status_code == 404
    assert response.json()["detail"] == "No eligible orders found for payment."
    # Clean up user_sessions
    user_sessions.pop(2, None)

# Test creating a payment when the user is not logged in
def test_create_payment_user_not_logged_in(create_test_database, mock_kafka_producer, mock_stripe_payment_intent_create):
    # No user in the session store

    invalid_token = "invalid-token"
    # Prepare the payment data
    payment_data = {
        "payment_method_id": "pm_card_visa",  # Use a test PaymentMethod ID
    }

    # Provide an invalid token
    response = client.post(
        "/payments/",
        json=payment_data,
        params={"token": "invalid-token"}
    )
    if response.status_code != 401:
        print("Error Response:", response.json())  # Debugging line
    assert response.status_code == 401
    assert response.json()["detail"] == "Could not validate credentials."


# Test the GET /payments/{payment_id} endpoint for non-existent payment
def test_get_payment_not_found(create_test_database, mock_get_current_non_admin_user):
    access_token = get_fake_regular_token()

    response = client.get(
        "/payments/9999",  # Assuming this payment_id does not exist
        headers={"Authorization": f"Bearer {access_token}"}
    )
    if response.status_code != 404:
        print("Error Response:", response.json())  # Debugging line
    assert response.status_code == 404
    assert response.json()["detail"] == "Payment not found"


# -------------------------------------------------------------------------------------------------
# -------------------------------------------------------------------------------------------------

# Test Handling Successful Stripe Webhook Event
@patch('app.main.handle_successful_payment')
@patch('stripe.Webhook.construct_event')
def test_stripe_webhook_success(mock_construct_event, mock_handle_success, create_test_database, mock_kafka_producer):
    # Create a mock Stripe event payload for payment_intent.succeeded
    mock_event = {
        "id": "evt_test",
        "type": "payment_intent.succeeded",
        "data": {
            "object": {
                "id": "pi_12345",
                "metadata": {
                    "order_id": "1"
                }
            }
        }
    }

    # Mock the construct_event to return the mock_event
    mock_construct_event.return_value = mock_event

    response = client.post(
        "/stripe/webhook/",
        json=mock_event,
        headers={"stripe-signature": "test_signature"}
    )

    if response.status_code != 200:
        print("Error Response:", response.json())  # Debugging line

    assert response.status_code == 200
    assert response.json()["status"] == "success"

    # Verify that handle_successful_payment was called with correct data
    mock_handle_success.assert_called_once_with(mock_event["data"]["object"])


# Test Handling Failed Stripe Webhook Event
@patch('stripe.Webhook.construct_event')
@patch('app.main.handle_failed_payment')
def test_stripe_webhook_failure(mock_handle_failure, mock_construct_event, create_test_database, mock_kafka_producer):

    # Create a mock Stripe event payload for payment_intent.payment_failed
    mock_event = {
        "id": "evt_test",
        "type": "payment_intent.payment_failed",
        "data": {
            "object": {
                "id": "pi_12345",
                "last_payment_error": {
                    "message": "Card was declined."
                },
                "metadata": {
                    "order_id": "1"
                }
            }
        }
    }

    # Mock the construct_event to return the mock_event
    mock_construct_event.return_value = mock_event

    response = client.post(
        "/stripe/webhook/",
        json=mock_event,
        headers={"stripe-signature": "test_signature"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "success"

    # Verify that handle_failed_payment was called with correct data
    mock_handle_failure.assert_called_once_with(mock_event["data"]["object"])



# Test Handling Unhandled Stripe Webhook Event Type
@patch('stripe.Webhook.construct_event')
def test_stripe_webhook_unhandled_event(mock_construct_event, create_test_database, mock_kafka_producer):
    
    # Create a mock Stripe event payload for an unhandled event type
    mock_event = {
        "id": "evt_test",
        "type": "payment_intent.refunded",  # Unhandled event type
        "data": {
            "object": {
                "id": "pi_12345",
                "metadata": {
                    "order_id": "1"
                }
            }
        }
    }

    # Mock the construct_event to return the mock_event
    mock_construct_event.return_value = mock_event

    response = client.post(
        "/stripe/webhook/",
        json=mock_event,
        headers={"stripe-signature": "test_signature"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    # Optionally, check logs or ensure no Kafka message was sent


# Test Handling Invalid Stripe Signature in Webhook
@patch('stripe.Webhook.construct_event')
def test_stripe_webhook_invalid_signature(mock_construct_event, create_test_database, mock_kafka_producer):
    import stripe
    # Mock the construct_event to raise a SignatureVerificationError
    mock_construct_event.side_effect = stripe.error.SignatureVerificationError(
        message="Invalid signature",
        sig_header="invalid_signature"
    )

    # Create a mock Stripe event payload
    mock_event = {
        "id": "evt_test",
        "type": "payment_intent.succeeded",
        "data": {
            "object": {
                "id": "pi_12345",
                "metadata": {
                    "order_id": "1"
                }
            }
        }
    }

    response = client.post(
        "/stripe/webhook/",
        json=mock_event,
        headers={"stripe-signature": "invalid_signature"}
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid signature"


# Test Handling Exception During Kafka Message Sending
def test_payment_processing_kafka_failure(create_test_database, mock_kafka_producer, mock_stripe_payment_intent_create, mock_get_current_non_admin_user):
    access_token = get_fake_regular_token()

    # Create an eligible order for the user in the database
    with Session(engine) as session:
        eligible_order = OrderPayment(
            order_id=1,
            user_id=2,  # Regular user
            order_total=500.0,
            currency="usd",
            customer_address="Regular Address",
            status="New",
            timestamp=datetime.now(timezone.utc),
        )
        session.add(eligible_order)
        session.commit()

    # Mock Stripe PaymentIntent.create to return a successful payment intent
    mock_payment_intent = Mock()
    mock_payment_intent.id = "pi_98765"
    mock_payment_intent.status = "succeeded"
    mock_stripe_payment_intent_create.return_value = mock_payment_intent

      # Add the user to the user_sessions to simulate that the user is logged in
    from app.main import user_sessions
    user_sessions[2] = {
        'role': 'CUSTOMER',
        'user_name': 'regularuser',
        'user_email': 'regular@example.com',
        'user_cellno': '1111111111',
        'user_address': 'Regular Address'
    }

    # Mock Kafka producer to raise an exception when sending
    mock_kafka_producer.send_and_wait.side_effect = Exception("Kafka send failure")

    # Prepare the payment data
    payment_data = {
        "payment_method_id": "pm_card_visa",
    }

    response = client.post(
        "/payments/",
        json=payment_data,
        params={"token": access_token}
    )
    # Depending on your implementation, the response might still be 200 with failure details
    # Adjust the assertion based on actual behavior
    assert response.status_code == 200
    response_data = response.json()
    assert response_data["status"] == "Success"  # Or "Failure" based on implementation

    # Verify that the Kafka producer's send_and_wait was called
    mock_kafka_producer.send_and_wait.assert_called_once_with("payment_data", ANY)