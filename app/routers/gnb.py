from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.core.logger import log
from app.core.database import get_redis
import redis

router = APIRouter()


# 与 Go 结构体完全一致
class BaseStation(BaseModel):
    station_id: int
    ip: str
    name: str
    region: str


class GnbResponse(BaseModel):
    code: str = "200"
    message: str = "success"
    timeStamp: str
    data: Optional[dict] = None


# 模拟存储（生产环境建议换成数据库）
stations_db = {}  # key: station_id, value: BaseStation


@router.post("")
async def handler_add_gnb(station: BaseStation, rds: redis.Redis = Depends(get_redis)):
    """新增基站"""
    log.info(f"HandlerAddGnb: {station.station_id} - {station.ip} - {station.name}")

    if station.station_id in stations_db:
        raise HTTPException(status_code=400, detail="Station already exists")

    stations_db[station.station_id] = station.dict()

    # 同步到 Redis（可选持久化）
    try:
        rds.hset(f"bs:{station.station_id}", mapping=station.dict())
    except Exception as e:
        log.warning(f"Redis save failed: {e}")

    return GnbResponse(
        timeStamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        data={"station_id": station.station_id}
    )


@router.delete("")
async def handler_del_gnb(
        station_id: int = Query(..., description="基站ID"),
        rds: redis.Redis = Depends(get_redis)
):
    """删除基站"""
    log.info(f"HandlerDelGnb: {station_id}")

    # 检查内存中是否存在
    if station_id not in stations_db:
        raise HTTPException(status_code=404, detail="Station not found")

    # 1. 从内存中删除
    del stations_db[station_id]

    # 2. 从 Redis 中删除
    try:
        redis_key = f"bs:{station_id}"
        deleted_count = rds.delete(redis_key)
        if deleted_count > 0:
            log.info(f"Redis key {redis_key} deleted successfully")
        else:
            log.warning(f"Redis key {redis_key} not found")
    except Exception as e:
        log.error(f"Redis delete failed: {e}")
        # 注意：这里如果 Redis 删除失败，但内存已删除，可能导致数据不一致
        # 可以考虑回滚或记录异常

    return GnbResponse(
        timeStamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        message=f"Station {station_id} deleted successfully"
    )


@router.get("/list")
async def handler_get_gnb_list(
        page: int = Query(1, ge=1, description="页码"),
        page_size: int = Query(10, ge=1, le=100, description="每页数量"),
        rds: redis.Redis = Depends(get_redis)
):
    """批量查询基站（直接从Redis读取）"""
    log.debug(f"HandlerGetGnbList page={page}, size={page_size}")

    try:
        # 1. 获取所有基站键
        keys = rds.keys("bs:*")

        if not keys:
            return GnbResponse(
                timeStamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                data={
                    "list": [],
                    "total": 0,
                    "page": page,
                    "page_size": page_size,
                    "total_pages": 0
                }
            )

        # 2. 批量读取所有基站数据
        stations = []
        for key in keys:
            station_data = rds.hgetall(key)
            if station_data:
                # 从键名提取 station_id
                station_id = int(key.split(':')[1])
                station = {
                    "station_id": station_id,
                    "ip": station_data.get("ip", ""),
                    "name": station_data.get("name", ""),
                    "region": station_data.get("region", "")
                }
                stations.append(station)

        # 3. 分页处理
        total = len(stations)
        start = (page - 1) * page_size
        end = start + page_size
        page_items = stations[start:end]

        return GnbResponse(
            timeStamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            data={
                "list": page_items,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size if total > 0 else 0
            }
        )
    except Exception as e:
        log.error(f"查询基站列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"Query failed: {str(e)}")