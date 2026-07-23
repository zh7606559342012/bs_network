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
    """从 Redis 加载所有 bs: 开头的基站到全局缓存"""
    global redis_client
    with CacheMutex:
        BaseStationCache.clear()
        keys = redis_client.keys("bs:*")
        for key in keys:
            data = redis_client.get(key)
            if data:
                try:
                    station = json.loads(data)
                    BaseStationCache[station["station_id"]] = station
                    log.debug(f"Loaded station: {station['station_id']} ({station['ip']}) {station['name']}")
                except Exception as e:
                    log.warning(f"Parse station {key} failed: {e}")
        log.info(f"BaseStationCache initialized with {len(BaseStationCache)} stations")


def get_redis():
    global redis_client
    if redis_client is None:
        init_redis()
    return redis_client