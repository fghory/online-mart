# product_api/create_topic.py

import asyncio
import logging
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import (
    KafkaConnectionError,
    TopicAlreadyExistsError,
    NotControllerError,
    LeaderNotAvailableError,
)


# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def create_kafka_topic(
    topic_name: str, 
    num_partitions: int = 1,
    replication_factor: int = 1,
    bootstrap_servers: str = "broker:19092",
    max_retries: int = 5,
    retry_interval: int = 10
):
    """
    Creates a Kafka topic with the specified configurations.

    Args:
        topic_name (str): The name of the Kafka topic to create.
        bootstrap_servers (str): Kafka broker address.
        num_partitions (int): Number of partitions for the topic.
        replication_factor (int): Replication factor for the topic.
        max_retries (int): Maximum number of retries for transient errors.
        retry_interval (int): Seconds to wait between retries.

    Raises:
        Exception: If the topic cannot be created after max_retries.
    """
    admin_client = AIOKafkaAdminClient(bootstrap_servers=bootstrap_servers)
    retries = 0

    try:
        await admin_client.start()
        while retries < max_retries:
            try:
                topic_list = [
                    NewTopic(name=topic_name, num_partitions=num_partitions, replication_factor=replication_factor)
                ]
                await admin_client.create_topics(
                    new_topics=topic_list,
                    validate_only=False
                )
                logger.info(f"Topic '{topic_name}' created successfully.")
                break
            except TopicAlreadyExistsError:
                logger.info(f"Topic '{topic_name}' already exists.")
                break
            except (NotControllerError, LeaderNotAvailableError, KafkaConnectionError) as e:
                retries += 1
                logger.warning(f"Transient error ({e}), retrying {retries}/{max_retries} after {retry_interval} seconds...")
                await asyncio.sleep(retry_interval)
            except Exception as e:
                logger.error(f"Unexpected error during topic creation: {e}")
                raise
        else:
            raise Exception(f"Failed to create Kafka topic '{topic_name}' after {max_retries} retries.")
    finally:
        await admin_client.close()
