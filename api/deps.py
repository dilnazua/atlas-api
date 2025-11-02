"""
Dependencies for FastAPI endpoints

Provides shared dependencies like Redis client, database sessions, etc.
"""

import redis
from .settings import settings


def get_redis_client():
    """
    Get a Redis client connection.
    
    Redis is used as a message broker and result backend for Celery tasks.
    
    Returns:
        Redis client instance
    """
    return redis.from_url(settings.REDIS_URL, decode_responses=True)

