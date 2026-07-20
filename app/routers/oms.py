from fastapi import APIRouter, Depends, Query
from datetime import datetime
from app.core.logger import log
from app.core.database import get_redis
import redis

router = APIRouter()

heartbeat_count = 0


@router.get("/common/heartbeat")
async def handler_heartbeat(
    ip: str = Query(None, description="Client IP"),
    rds: redis.Redis = Depends(get_redis)
):
    global heartbeat_count
    log.debug("HandlerHeartbeat called")

    heartbeat_count += 1

    if settings.oms.oms_ip:  # 已设置 OMS IP
        heartbeat_count = 0
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return {"timeStamp": timestamp, "code": "200", "message": "ok"}

    if heartbeat_count >= 3 and ip:
        settings.oms.oms_ip = ip  # 注意：实际应持久化
        heartbeat_count = 0
        try:
            rds.set("omsIp", ip)
            log.info(f"OMS IP updated: {ip}")
        except Exception as e:
            log.error(f"Redis error: {e}")

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return {"timeStamp": timestamp, "code": "200", "message": "ok"}