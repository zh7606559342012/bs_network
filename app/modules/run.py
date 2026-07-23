from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from app.modules.anomaly import detect_network_anomaly
from datetime import datetime
import json
import asyncio
import os
from pathlib import Path
from app.core.logger import log
from app.core.database import (
    BaseStationCache,
    CacheMutex,
    get_redis,
    init_base_station_cache
)
import redis

scheduler = BackgroundScheduler()


async def ping_base_station(station):
    """执行单次 Ping"""
    ip = station.get("ip", "").strip()
    if not ip:
        return None

    start = datetime.now()
    try:
        # 使用 ping 命令（Linux 系统）
        proc = await asyncio.create_subprocess_shell(
            f"ping -c 1 -W 5 -n {ip}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        output = stdout.decode()

        status = "OK"
        rtt_ms = 0.0

        if "time=" in output:
            try:
                # 提取 rtt
                part = output.split("time=")[1].split(" ms")[0]
                rtt_ms = float(part.strip())
            except:
                rtt_ms = (datetime.now() - start).total_seconds() * 1000
        else:
            status = "FAIL"
            rtt_ms = -1
    except Exception:
        status = "FAIL"
        rtt_ms = -1

    return {
        "station_id": station["station_id"],
        "ip": ip,
        "status": status,
        "rtt_ms": round(rtt_ms, 2),
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }


def write_ping_log(ping_result):
    """按 IP 写日志文件"""
    if not ping_result:
        return
    ip = ping_result["ip"].replace(".", "")
    log_dir = Path("/var/log/monitor_agent")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"ping_{ip}.log"

    log_line = f"{ping_result['time']} | seq=000001 | {ping_result['status']:4} | rtt={ping_result['rtt_ms']:.2f} ms\n"

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(log_line)


async def network_monitor_task():
    """主监控任务"""
    try:
        log.info(f"=== Network Monitor Task Started, cached stations: {len(BaseStationCache)} ===")

        with CacheMutex:
            stations = list(BaseStationCache.values())

        tasks = [ping_base_station(station) for station in stations]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        online_count = 0
        for r in results:
            if isinstance(r, dict):
                write_ping_log(r)
                if r["status"] == "OK":
                    online_count += 1

        log.info(f"Ping completed: {len(results)} stations, online: {online_count}")

    except Exception as e:
        log.error(f"Monitor task error: {e}")


def sync_base_station_cache():
    """每 5 分钟从 Redis 同步到内存缓存（核心功能）"""
    start = datetime.now()
    log.info("=== 开始执行基站缓存同步任务 ===")

    rds = get_redis()
    redis_data = {}

    # 1. 从 Redis 读取所有 bs: 开头的基站
    with CacheMutex:
        keys = rds.keys("bs:*")
        for key in keys:
            data = rds.get(key)
            if data:
                try:
                    station = json.loads(data)
                    redis_data[station["station_id"]] = station
                except Exception as e:
                    log.warning(f"Parse {key} failed: {e}")

        # 2. 处理删除（Redis 中没有，但缓存中有的）
        for sid in list(BaseStationCache.keys()):
            if sid not in redis_data:
                del BaseStationCache[sid]
                log.info(f"定时同步：基站 {sid} 已从内存缓存删除")

        # 3. 处理新增和更新
        for sid, station in redis_data.items():
            old = BaseStationCache.get(sid)
            if not old:
                # 新增
                BaseStationCache[sid] = station
                log.info(f"定时同步：新增基站 {sid} ({station['ip']})")
            elif (old.get("ip") != station.get("ip") or
                  old.get("name") != station.get("name") or
                  old.get("region") != station.get("region")):
                # 更新
                BaseStationCache[sid] = station
                log.info(f"定时同步：更新基站 {sid} 信息")

    log.info(f"基站缓存同步完成，共 {len(BaseStationCache)} 个基站，耗时 {datetime.now() - start}")


def start_modules():
    """启动后台模块"""
    log.info("Starting background modules...")

    # 每 60 秒执行一次（接近 Go 的 1 分钟）
    scheduler.add_job(
        lambda: asyncio.run(network_monitor_task()),
        IntervalTrigger(seconds=60),
        id="base_station_ping",
        replace_existing=True
    )

    # 2. 每 5 分钟同步 Redis → 内存缓存
    scheduler.add_job(
        sync_base_station_cache,
        IntervalTrigger(minutes=5),
        id="sync_base_station_cache",
        replace_existing=True
    )

    # 3. 每天凌晨2点执行异常检测
    scheduler.add_job(
        detect_network_anomaly,
        CronTrigger(hour=2, minute=0),
        id="detect_network_anomaly",
        replace_existing=True
    )

    scheduler.start()
    log.info("✅ All background tasks started (Ping + 5min sync + 2AM anomaly detection)")