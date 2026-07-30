from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from app.modules.anomaly import detect_network_anomaly
from app.modules.alarm import handler_alarm, init_alarm_maps
import re
from datetime import datetime, timedelta
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


def get_next_seq(station_id, ip, log_path):
    """从日志文件中读取最后的序列号，并返回下一个"""
    seq = 1  # 默认从1开始
    try:
        if log_path.exists():
            with open(log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                if lines:
                    # 读取最后一行，提取 seq 值
                    last_line = lines[-1]
                    # 格式: 2026-07-23 16:20:25 | seq=000001 | OK | rtt=0.75 ms
                    if "seq=" in last_line:
                        seq_part = last_line.split("seq=")[1].split(" |")[0]
                        seq = int(seq_part) + 1
    except Exception as e:
        log.warning(f"读取序列号失败: {e}")
    return seq


def write_ping_log(ping_result):
    """按 IP 写日志文件（带序列号持久化）"""
    if not ping_result:
        return

    ip = ping_result["ip"].replace(".", "")
    log_dir = Path("/var/log/monitor_agent")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"ping_{ip}.log"

    # ✅ 从日志文件读取下一个序列号
    seq = get_next_seq(ping_result["station_id"], ip, log_path)

    log_line = f"{ping_result['time']} | seq={seq:06d} | {ping_result['status']:4} | rtt={ping_result['rtt_ms']:.2f} ms\n"

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
            # ✅ 修改点1: 使用 hgetall 获取 Hash 数据
            station_data = rds.hgetall(key)
            if station_data:
                try:
                    # ✅ 修改点2: 因为 decode_responses=True，数据已经是字符串
                    # 从键名提取 station_id
                    station_id = int(key.split(':')[1])
                    # 构建 station 字典，确保包含所有字段
                    station = {
                        "station_id": station_id,
                        "ip": station_data.get("ip", ""),
                        "name": station_data.get("name", ""),
                        "region": station_data.get("region", "")
                    }
                    redis_data[station_id] = station
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


def clean_old_ping_logs():
    """清理超过10天的Ping日志"""
    log_dir = Path("/var/log/monitor_agent")
    if not log_dir.exists():
        log.warning(f"日志目录不存在: {log_dir}")
        return

    cutoff_time = datetime.now() - timedelta(days=10)
    total_cleaned = 0
    total_files = 0

    log.info(f"=== 开始清理超过10天的Ping日志 (截止时间: {cutoff_time.strftime('%Y-%m-%d %H:%M:%S')}) ===")

    # 查找所有 ping_*.log 文件
    ping_files = list(log_dir.glob("ping_*.log"))

    for log_file in ping_files:
        try:
            cleaned_lines = []
            kept_count = 0
            removed_count = 0

            with open(log_file, "r", encoding="utf-8") as f:
                lines = f.readlines()

            for line in lines:
                # 解析时间戳（格式: 2026-07-23 17:21:09）
                match = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
                if match:
                    try:
                        line_time = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
                        if line_time >= cutoff_time:
                            cleaned_lines.append(line)
                            kept_count += 1
                        else:
                            removed_count += 1
                    except ValueError:
                        # 时间解析失败，保留该行（防止误删）
                        cleaned_lines.append(line)
                        kept_count += 1
                else:
                    # 格式不匹配的行（如空行、标题行）保留
                    cleaned_lines.append(line)
                    kept_count += 1

            # 如果有删除，重写文件
            if removed_count > 0:
                with open(log_file, "w", encoding="utf-8") as f:
                    f.writelines(cleaned_lines)
                total_cleaned += removed_count
                total_files += 1
                log.info(f"📝 清理文件: {log_file.name} | 删除 {removed_count} 行, 保留 {kept_count} 行")
            else:
                log.debug(f"✅ 文件无需清理: {log_file.name} (共 {kept_count} 行)")

        except Exception as e:
            log.error(f"处理文件 {log_file} 失败: {e}")

    log.info(f"✅ 日志清理完成！共处理 {len(ping_files)} 个文件，清理 {total_cleaned} 条过期记录")
    return total_cleaned, total_files


def start_modules():
    """启动后台模块"""
    log.info("Starting background modules...")

    # 1. 先初始化映射表
    init_alarm_maps()

    # 2. 启动告警处理协程
    asyncio.create_task(handler_alarm())

    # 1. 每 60 秒执行一次 Ping 监控
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

    # 🆕 4. 每天凌晨5点清理超过10天的日志
    scheduler.add_job(
        clean_old_ping_logs,
        CronTrigger(hour=5, minute=0),
        id="clean_old_ping_logs",
        replace_existing=True
    )

    scheduler.start()
    log.info("✅ All background tasks started (Ping + 5min sync + 2AM anomaly detection + 5AM log cleanup)")