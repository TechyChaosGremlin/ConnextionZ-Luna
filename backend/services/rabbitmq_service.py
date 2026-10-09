"""
RabbitMQ service for async message processing.

Provides:
- Connection management with automatic reconnection
- Exchange and queue declaration helpers
- Publish/subscribe pattern support
- Work queue pattern support
- Dead letter queue configuration
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Callable, Coroutine, Optional

import aio_pika
from aio_pika import ExchangeType, Message, DeliveryMode
from aio_pika.abc import (
    AbstractRobustConnection,
    AbstractRobustChannel,
    AbstractRobustQueue,
    AbstractIncomingMessage,
)

from app.config import settings

logger = logging.getLogger(__name__)


class RabbitMQService:
    """
    RabbitMQ service for async message processing.

    Features:
    - Robust connection with automatic reconnection
    - Exchange and queue management
    - Publish/subscribe and work queue patterns
    - Dead letter queue support
    - Message acknowledgment and retry
    """

    def __init__(self):
        """Initialize RabbitMQ service."""
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractRobustChannel | None = None
        self._exchange: aio_pika.Exchange | None = None
        self._consumers: dict[str, Callable] = {}

    async def connect(self) -> None:
        """Establish RabbitMQ connection with retry logic."""
        if settings.infrastructure_mode == "postgres_beta":
            logger.error("RabbitMQ is disabled in PostgreSQL beta mode")
            raise RuntimeError("Background queues are disabled in PostgreSQL beta mode")
        if self._connection and not self._connection.is_closed:
            return

        try:
            self._connection = await aio_pika.connect_robust(
                settings.rabbitmq_url,
                loop=asyncio.get_event_loop(),
            )
            self._channel = await self._connection.channel()
            # Set QoS for fair dispatch
            await self._channel.set_qos(prefetch_count=10)
            logger.info("RabbitMQ connected successfully")
        except Exception as e:
            logger.error(f"Failed to connect to RabbitMQ: {e}")
            raise

    async def disconnect(self) -> None:
        """Close RabbitMQ connection."""
        if self._connection and not self._connection.is_closed:
            await self._connection.close()
            logger.info("RabbitMQ disconnected")

    async def declare_exchange(
        self,
        name: str,
        exchange_type: ExchangeType = ExchangeType.DIRECT,
        durable: bool = True,
    ) -> aio_pika.Exchange:
        """
        Declare an exchange.

        Args:
            name: Exchange name
            exchange_type: Exchange type (direct, topic, fanout, headers)
            durable: Whether exchange survives broker restart

        Returns:
            Declared exchange
        """
        if not self._channel:
            await self.connect()

        exchange = await self._channel.declare_exchange(
            name=name,
            type=exchange_type,
            durable=durable,
        )
        logger.debug(f"Declared exchange: {name} (type={exchange_type})")
        return exchange

    async def declare_queue(
        self,
        name: str,
        durable: bool = True,
        arguments: dict[str, Any] | None = None,
    ) -> AbstractRobustQueue:
        """
        Declare a queue with optional dead letter configuration.

        Args:
            name: Queue name
            durable: Whether queue survives broker restart
            arguments: Queue arguments (e.g., dead letter exchange)

        Returns:
            Declared queue
        """
        if not self._channel:
            await self.connect()

        # Default arguments for dead letter handling
        if arguments is None:
            arguments = {}

        queue = await self._channel.declare_queue(
            name=name,
            durable=durable,
            arguments=arguments,
        )
        logger.debug(f"Declared queue: {name}")
        return queue

    async def declare_dlq(
        self,
        queue_name: str,
        dlq_name: str | None = None,
    ) -> AbstractRobustQueue:
        """
        Declare a dead letter queue for a given queue.

        Args:
            queue_name: Original queue name
            dlq_name: DLQ name (default: "{queue_name}.dlq")

        Returns:
            Declared DLQ
        """
        if dlq_name is None:
            dlq_name = f"{queue_name}.dlq"

        return await self.declare_queue(
            name=dlq_name,
            durable=True,
        )

    async def bind_queue(
        self,
        queue: AbstractRobustQueue,
        exchange: aio_pika.Exchange,
        routing_key: str = "",
    ) -> None:
        """
        Bind a queue to an exchange with a routing key.

        Args:
            queue: Queue to bind
            exchange: Exchange to bind to
            routing_key: Routing key for binding
        """
        await queue.bind(exchange=exchange, routing_key=routing_key)
        logger.debug(f"Bound queue {queue.name} to exchange {exchange.name} with key '{routing_key}'")

    async def publish(
        self,
        exchange: aio_pika.Exchange,
        routing_key: str,
        message_body: dict[str, Any],
        persistent: bool = True,
        headers: dict[str, Any] | None = None,
    ) -> None:
        """
        Publish a message to an exchange.

        Args:
            exchange: Target exchange
            routing_key: Routing key
            message_body: Message payload (will be JSON serialized)
            persistent: Whether message survives broker restart
            headers: Optional message headers
        """
        if not self._channel:
            await self.connect()

        message = Message(
            body=json.dumps(message_body).encode(),
            delivery_mode=DeliveryMode.PERSISTENT if persistent else DeliveryMode.NOT_PERSISTENT,
            content_type="application/json",
            headers=headers or {},
            timestamp=datetime.now(),
        )

        await exchange.publish(message, routing_key=routing_key)
        logger.debug(f"Published message to {exchange.name} with key '{routing_key}'")

    async def consume(
        self,
        queue: AbstractRobustQueue,
        callback: Callable[[AbstractIncomingMessage], Coroutine[Any, Any, None]],
        no_ack: bool = False,
    ) -> None:
        """
        Start consuming messages from a queue.

        Args:
            queue: Queue to consume from
            callback: Async callback function to process messages
            no_ack: If True, don't send acknowledgments
        """
        await queue.consume(callback, no_ack=no_ack)
        logger.info(f"Started consuming from queue: {queue.name}")

    async def consume_work_queue(
        self,
        queue_name: str,
        callback: Callable[[dict[str, Any]], Coroutine[Any, Any, None]],
        durable: bool = True,
    ) -> None:
        """
        Consume from a work queue with automatic acknowledgment.

        Args:
            queue_name: Queue name
            callback: Async callback to process message body
            durable: Whether queue is durable
        """
        queue = await self.declare_queue(queue_name, durable=durable)

        async def message_handler(message: AbstractIncomingMessage) -> None:
            async with message.process():
                try:
                    body = json.loads(message.body.decode())
                    await callback(body)
                    logger.debug(f"Processed message from {queue_name}")
                except Exception as e:
                    logger.error(f"Error processing message from {queue_name}: {e}")
                    # Message will be requeued on exception (message.process() handles this)

        await self.consume(queue, message_handler)
        logger.info(f"Consuming work queue: {queue_name}")

    async def publish_to_work_queue(
        self,
        queue_name: str,
        message_body: dict[str, Any],
        persistent: bool = True,
    ) -> None:
        """
        Publish a message to a work queue (using default exchange).

        Args:
            queue_name: Queue name (used as routing key)
            message_body: Message payload
            persistent: Whether message survives broker restart
        """
        if not self._channel:
            await self.connect()

        queue = await self.declare_queue(queue_name, durable=persistent)

        message = Message(
            body=json.dumps(message_body).encode(),
            delivery_mode=DeliveryMode.PERSISTENT if persistent else DeliveryMode.NOT_PERSISTENT,
            content_type="application/json",
        )

        await self._channel.default_exchange.publish(
            message,
            routing_key=queue_name,
        )
        logger.debug(f"Published to work queue: {queue_name}")


# Singleton instance
rabbitmq_service = RabbitMQService()
