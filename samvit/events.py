"""
Redpanda (Kafka-compatible) event producer.

Decisions applied:
  - Publish failures log a WARNING but never fail the calling request (Decision #13)
  - Topics are created automatically by Redpanda (auto.create.topics.enable=true)
  - Producer is initialised at startup; if unavailable, server still starts
    but marks event bus as degraded
"""

from __future__ import annotations

import json
import logging
import os

from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaConnectionError

log = logging.getLogger(__name__)

_producer: AIOKafkaProducer | None = None
_degraded: bool = False   # True when Redpanda is unreachable at startup


async def init() -> None:
    """Start the Kafka producer. Sets _degraded=True if Redpanda is unreachable."""
    global _producer, _degraded
    brokers = os.environ.get("REDPANDA_BROKERS", "localhost:9092")
    _producer = AIOKafkaProducer(
        bootstrap_servers=brokers,
        value_serializer=lambda v: json.dumps(v).encode(),
        request_timeout_ms=3000,
        retry_backoff_ms=200,
    )
    try:
        await _producer.start()
        log.info("Redpanda producer connected to %s", brokers)
    except KafkaConnectionError as exc:
        _degraded = True
        log.warning(
            "Redpanda unavailable at startup (%s) — event publishing degraded", exc
        )


async def close() -> None:
    global _producer
    if _producer:
        try:
            await _producer.stop()
        except Exception:
            pass
        _producer = None


async def publish(topic: str, payload: dict) -> None:
    """
    Publish payload to topic.
    Decision #13: swallows all errors with a WARNING — DB is source of truth.
    """
    global _degraded
    if _degraded or _producer is None:
        log.warning("Event publish skipped (degraded): topic=%s payload=%s", topic, payload)
        return
    try:
        await _producer.send_and_wait(topic, payload)
        log.debug("Event published: topic=%s", topic)
    except Exception as exc:
        log.warning("Event publish failed (topic=%s): %s", topic, exc)
