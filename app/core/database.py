import redis
from app.core.config import settings
from app.core.logger import log

redis_client = None

def init_redis() -> redis.Redis:
    global redis_client
    try:
        redis_client = redis.Redis(
            host=settings.redis.addr,
            port=settings.redis.port,
            password=settings.redis.password,
            decode_responses=True,
            socket_connect_timeout=10,
        )
        redis_client.ping()
        log.info("Redis connected successfully")
        return redis_client
    except Exception as e:
        log.error(f"Redis connection failed: {e}")
        raise


def get_redis():
    """FastAPI 依赖注入"""
    global redis_client
    if redis_client is None:
        init_redis()
    return redis_client