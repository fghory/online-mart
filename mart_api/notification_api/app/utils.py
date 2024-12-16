# notification_api/app/utils.py

import smtplib
from email.mime.text import MIMEText
from twilio.rest import Client
import logging, time
from app import settings
from app import order_pb2, inventory_pb2, payment_pb2
from app.models import OrderStatus as PythonOrderStatus # Python OrderStatus enum to match Protobuf OrderStatus
from app.models import ItemStatus as PythonItemStatus, PaymentStatus as PythonPaymentStatus  

# Configure logging
logger = logging.getLogger(__name__)

def send_email(to_email: str, subject: str, body: str, retries: int = 3) -> bool:
    """
    Sends an email to the specified recipient.

    Args:
        to_email (str): Recipient's email address.
        subject (str): Email subject.
        body (str): Email body.

    Returns:
        bool: True if email sent successfully, False otherwise.
    """

    attempt = 0
    while attempt < retries:
        try:
            msg = MIMEText(body)
            msg['Subject'] = subject
            msg['From'] = settings.USER_EMAIL  # Replace with your email
            msg['To'] = to_email

            with smtplib.SMTP('smtp.gmail.com', 587) as server:  # Replace with your SMTP server
                server.starttls()
                server.login(settings.USER_EMAIL, settings.USER_PASSWORD)  # Replace with your credentials
                server.send_message(msg)
            
            logger.info(f"Email sent to {to_email}")
            return True
        except Exception as e:
            attempt += 1
            logger.error(f"Failed to send email to {to_email}: {e}")
            time.sleep(2 ** attempt)  # Exponential backoff
    return False

def send_sms(to_number: str, message: str, retries: int = 3) -> bool:
    """
    Sends an SMS to the specified phone number.

    Args:
        to_number (str): Recipient's phone number.
        message (str): SMS message body.

    Returns:
        bool: True if SMS sent successfully, False otherwise.
    """

    attempt = 0
    while attempt < retries:

        try:
            account_sid = settings.TWILIO_ACCOUNT_SID      # Replace with your Twilio SID
            auth_token = settings.TWILIO_AUTH_TOKEN        # Replace with your Twilio Auth Token
            client = Client(account_sid, auth_token)

            message = client.messages.create(
                body=message,
                from_= settings.TWILIO_NUMBER,  # Replace with your Twilio number
                to=to_number
            )
            logger.info(f"SMS sent to {to_number}")
            return True
        except Exception as e:
            attempt += 1
            logger.error(f"Failed to send SMS to {to_number}: {e}")
            time.sleep(2 ** attempt)  # Exponential backoff
    return False

def map_protobuf_to_python_order_status(protobuf_status: order_pb2.OrderStatus) -> PythonOrderStatus:
    """
    Maps Protobuf OrderStatus enum to Python OrderStatus enum.

    Args:
        protobuf_status (OrderStatus): Protobuf OrderStatus enum.

    Returns:
        PythonOrderStatus: Mapped Python OrderStatus enum.
    """
    mapping = {
        order_pb2.OrderStatus.NEW: PythonOrderStatus.NEW,
        order_pb2.OrderStatus.PROCESSING: PythonOrderStatus.PROCESSING,
        order_pb2.OrderStatus.CONFIRMED: PythonOrderStatus.CONFIRMED,
        order_pb2.OrderStatus.SHIPPED: PythonOrderStatus.SHIPPED,
        order_pb2.OrderStatus.CANCELLED: PythonOrderStatus.CANCELLED,
        order_pb2.OrderStatus.COMPLETED: PythonOrderStatus.COMPLETED,
    }
    if protobuf_status in mapping:
        return mapping[protobuf_status]
    else:
        raise ValueError(f"Unknown Protobuf OrderStatus: {protobuf_status}")
    

def map_protobuf_to_python_item_status(protobuf_status: inventory_pb2.ItemStatus) -> PythonItemStatus:
    """
    Maps Protobuf ItemStatus enum to Python ItemStatus enum.
    """
    mapping = {
        inventory_pb2.ItemStatus.IN_STOCK: PythonItemStatus.IN_STOCK,
        inventory_pb2.ItemStatus.OUT_OF_STOCK: PythonItemStatus.OUT_OF_STOCK,
        inventory_pb2.ItemStatus.COMING_SOON: PythonItemStatus.COMING_SOON,
        inventory_pb2.ItemStatus.DISCONTINUED: PythonItemStatus.DISCONTINUED,
    }
    if protobuf_status in mapping:
        return mapping[protobuf_status]
    else:
        raise ValueError(f"Unknown Protobuf ItemStatus: {protobuf_status}") 
    

def map_protobuf_to_python_payment_status(protobuf_status: payment_pb2.PaymentStatus) -> PythonPaymentStatus:
    """
    Maps Protobuf PaymentStatus enum to Python PaymentStatus enum.
    """
    mapping = {
        payment_pb2.PaymentStatus.SUCCESS: PythonPaymentStatus.SUCCESS,
        payment_pb2.PaymentStatus.FAILURE: PythonPaymentStatus.FAILURE,
        payment_pb2.PaymentStatus.PENDING: PythonPaymentStatus.PENDING,
    }
    if protobuf_status in mapping:
        return mapping[protobuf_status]
    else:
        raise ValueError(f"Unknown Protobuf PaymentStatus: {protobuf_status}")