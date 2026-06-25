import json
import logging

import redis
import redis.asyncio as aioredis
from django.conf import settings

logger = logging.getLogger(__name__)

redis_client_async = aioredis.from_url(settings.REDIS_URL)
redis_client = redis.from_url(settings.REDIS_URL)


async def abroadcast_message(
    brand_id,
    user_ids: list[int],
    text: str,
    msg_type: str = "text",
    file_id: str = None,
    buttons_data: list = None,
    parse_mode: str = "HTML",
):
    """Async Publish a broadcast message to Redis channel for async processing"""

    payload = {
        "brand_id": str(brand_id),
        "user_ids": user_ids,
        "text": text,
        "msg_type": msg_type,
        "file_id": file_id,
        "buttons_data": buttons_data,
        "parse_mode": parse_mode,
    }

    try:
        await redis_client_async.publish(
            f"broadcast_channel:{brand_id}", json.dumps(payload)
        )
        logger.info(
            f"Async Broadcast published to {len(user_ids)} users for brand {brand_id}"
        )
    except Exception as e:
        logger.error(f"Failed to publish Async broadcast: {e}")


def broadcast_message(
    brand_id,
    user_ids: list[int],
    text: str,
    msg_type: str = "text",
    file_id: str = None,
    buttons_data: list = None,
    parse_mode: str = "HTML",
):
    """Sync Publish a broadcast message to Redis channel for async processing"""

    payload = {
        "brand_id": str(brand_id),
        "user_ids": user_ids,
        "text": text,
        "msg_type": msg_type,
        "file_id": file_id,
        "buttons_data": buttons_data,
        "parse_mode": parse_mode,
    }

    try:
        redis_client.publish(f"broadcast_channel:{brand_id}", json.dumps(payload))
        logger.info(
            f"Sync Broadcast published to {len(user_ids)} users for brand {brand_id}"
        )
    except Exception as e:
        logger.error(f"Failed to publish sync broadcast: {e}")
