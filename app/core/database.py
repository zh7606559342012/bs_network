import redis
import json
from threading import RLock
from app.core.config import settings
from app.core.logger import log

redis_client = None
BaseStationCache = {}  # 全局缓存: station_id -> dict
CacheMutex = RLock()  # 读写锁


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

        # 加载配置和基站缓存
        get_conf_from_redis()
        init_base_station_cache()

        return redis_client
    except Exception as e:
        log.error(f"Redis connection failed: {e}")
        raise


def get_conf_from_redis():
    """加载 OMS IP 等配置"""
    global redis_client
    if settings.oms.oms_ip:
        redis_client.set("omsIp", settings.oms.oms_ip)
    else:
        settings.oms.oms_ip = redis_client.get("omsIp") or ""


def init_base_station_cache():
    """启动时从 Redis 加载基站数据到内存缓存（适配 Hash 存储）"""
    global BaseStationCache, redis_client  # 声明使用全局变量

    try:
        # ✅ 直接使用全局 redis_client，它已经在 init_redis() 中被初始化了
        # 不需要再调用任何 get 函数
        if redis_client is None:
            log.warning("Redis client is not initialized, skip loading cache")
            return

        # 查找所有基站键
        keys = redis_client.keys("bs:*")

        for key in keys:
            # 使用 hgetall 获取 Hash 中所有字段
            station_data = redis_client.hgetall(key)
            if station_data:
                # 注意：如果 decode_responses=True，返回的数据已经是字符串，不需要再 decode
                # 从键名提取 station_id，例如从 "bs:1001" 提取 1001
                station_id = int(key.split(':')[1])
                BaseStationCache[station_id] = station_data

        log.info(f"Loaded {len(BaseStationCache)} base stations from Redis (Hash)")
    except Exception as e:
        log.error(f"Failed to load base station cache: {e}")


def get_redis():
    global redis_client
    if redis_client is None:
        init_redis()
    return redis_client