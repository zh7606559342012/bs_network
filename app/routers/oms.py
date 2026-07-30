from fastapi import APIRouter, Depends, Query
from app.core.logger import log
from app.core.database import get_redis
from app.core.config import settings
from app.models.entity import Response
from typing import Optional
from app.utils.helpers import success_response, error_response
import redis
import asyncio

router = APIRouter(prefix="/common", tags=["OMS"])

# 使用线程锁保证并发安全
_heartbeat_lock = asyncio.Lock()
heartbeat_count = 0


@router.get("/heartbeat", response_model=Response)
async def handler_heartbeat(
        ip: Optional[str] = Query(None, description="Client IP"),
        rds: redis.Redis = Depends(get_redis)
):
    """
    心跳接口 - 自动发现OMS IP

    工作流程：
    1. 如果OMS IP已配置，直接返回成功
    2. 如果未配置，累计3次心跳后自动设置OMS IP
    3. 将IP持久化到Redis
    """
    global heartbeat_count
    log.debug("HandlerHeartbeat called")

    # 场景1：OMS IP已配置（使用你的 settings.oms.oms_ip）
    if settings.oms.oms_ip:
        log.debug(f"OMS IP already configured: {settings.oms.oms_ip}")
        # 重置计数器
        async with _heartbeat_lock:
            heartbeat_count = 0
        return success_response(message="ok")

    # 场景2：尝试发现并设置OMS IP
    async with _heartbeat_lock:
        heartbeat_count += 1
        log.debug(f"Current heartbeat count: {heartbeat_count}")

        # 检查Redis连接
        try:
            await rds.ping()
        except Exception as e:
            log.error(f"Redis connection error: {e}")
            # 尝试重连（对应Go代码的InitRedisClient）
            try:
                # 如果你有重连逻辑，在这里调用
                # 例如：await reinit_redis_client()
                log.warning("Attempting to reconnect Redis...")
            except Exception as reconnect_error:
                log.error(f"Redis reconnect failed: {reconnect_error}")
                return error_response(
                    message="Redis connection failed",
                    code="500"
                )

        # 心跳计数达到3次且有IP参数，设置OMS IP
        if heartbeat_count >= 3 and ip:
            try:
                # 更新内存配置（使用你的 settings.oms）
                settings.oms.oms_ip = ip
                log.info(f"OMS IP auto-discovered and set: {ip}")

                # 持久化到Redis
                await rds.set("omsIp", ip)
                log.debug(f"OMS IP saved to Redis: {ip}")

                # 重置计数器
                heartbeat_count = 0

                return success_response(
                    message=f"OMS IP updated: {ip}",
                    data={"oms_ip": ip}
                )

            except Exception as e:
                log.error(f"Failed to save OMS IP to Redis: {e}")
                return error_response(
                    message=f"Failed to save OMS IP: {str(e)}",
                    code="500"
                )

    # 默认返回（心跳次数不足或未提供IP）
    return success_response(message="ok")


@router.get("/health", response_model=Response)
async def health_check():
    """
    健康检查接口

    用于检查服务是否正常运行，返回服务状态和版本信息
    """
    return success_response(
        message="ok",
        data={
            "status": "ok",
            "version": settings.app.version,
            "addr": settings.app.addr,
            "port": settings.app.port
        }
    )