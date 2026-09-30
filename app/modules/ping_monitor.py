from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from icmplib import SocketPermissionError, async_multiping, async_ping

from app.core.database import BaseStationCache, CacheMutex
from app.core.logger import log

PING_CONCURRENCY = 10
PING_COUNT = 1
PING_TIMEOUT = 3.0
PING_LOG_DIR = Path("/var/log/monitor_agent")
SLICE_COUNT = 1
USE_PRIVILEGED: bool | None = None

_tick = 0
_privileged: bool | None = None
_seq_cache: dict[str, int] = {}


def get_next_seq(station_id, log_path: Path) -> int:
    sid = str(station_id)
    if sid in _seq_cache:
        _seq_cache[sid] += 1
        return _seq_cache[sid]

    seq = 1
    try:
        if log_path.exists():
            with open(log_path, "r", encoding="utf-8") as f:
                last = ""
                for last in f:
                    pass
                if last and "seq=" in last:
                    seq = int(last.split("seq=")[1].split(" |")[0]) + 1
    except Exception as e:
        log.warning(f"读取序列号失败: {e}")
    _seq_cache[sid] = seq
    return seq


def write_ping_logs(results: list[dict[str, Any]]) -> None:
    if not results:
        return
    PING_LOG_DIR.mkdir(parents=True, exist_ok=True)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in results:
        grouped[str(r["station_id"])].append(r)

    for station_id, rows in grouped.items():
        log_path = PING_LOG_DIR / f"ping_{station_id}.log"
        lines = []
        for r in rows:
            seq = get_next_seq(station_id, log_path)
            if r["status"] == "OK":
                rtt_str = f"rtt={r['rtt_ms']:.2f} ms"
            else:
                rtt_str = "rtt=- (timeout/unreachable)"
            lines.append(
                f"{r['time']} | seq={seq:06d} | {r['status']:4} | {rtt_str}\n"
            )
        with open(log_path, "a", encoding="utf-8") as f:
            f.writelines(lines)


async def detect_privileged() -> bool:
    global _privileged
    if USE_PRIVILEGED is not None:
        _privileged = USE_PRIVILEGED
        return _privileged
    try:
        await async_ping("127.0.0.1", count=1, timeout=0.2, privileged=True)
        _privileged = True
        log.info("icmplib mode: privileged=True")
        return True
    except SocketPermissionError:
        await async_ping("127.0.0.1", count=1, timeout=0.2, privileged=False)
        _privileged = False
        log.warning("icmplib mode: privileged=False")
        return False


def _pick_slice(stations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    global _tick
    if SLICE_COUNT <= 1:
        _tick += 1
        return stations
    idx = _tick % SLICE_COUNT
    _tick += 1
    return [s for i, s in enumerate(stations) if i % SLICE_COUNT == idx]


async def network_monitor_task() -> None:
    try:
        if _privileged is None:
            await detect_privileged()

        with CacheMutex:
            stations = [
                s for s in BaseStationCache.values()
                if str(s.get("ip", "")).strip()
            ]
        targets = _pick_slice(stations)
        log.info(
            f"=== Network Monitor Task Started, cached={len(BaseStationCache)}, "
            f"this_round={len(targets)}, privileged={_privileged} ==="
        )
        if not targets:
            log.info("Ping completed: 0 stations, online: 0")
            return

        ip_to_stations: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for st in targets:
            ip_to_stations[str(st["ip"]).strip()].append(st)

        hosts = await async_multiping(
            list(ip_to_stations.keys()),
            count=PING_COUNT,
            interval=0.2,
            timeout=PING_TIMEOUT,
            concurrent_tasks=PING_CONCURRENCY,
            privileged=bool(_privileged),
        )

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        results: list[dict[str, Any]] = []
        got = set()
        for host in hosts:
            got.add(host.address)
            status = "OK" if host.is_alive else "FAIL"
            rtt = float(host.avg_rtt or 0.0) if host.is_alive else -1.0
            for st in ip_to_stations.get(host.address, []):
                results.append({
                    "station_id": st["station_id"],
                    "ip": host.address,
                    "status": status,
                    "rtt_ms": round(rtt, 2),
                    "time": now,
                })

        for ip, sts in ip_to_stations.items():
            if ip in got:
                continue
            for st in sts:
                results.append({
                    "station_id": st["station_id"],
                    "ip": ip,
                    "status": "FAIL",
                    "rtt_ms": -1.0,
                    "time": now,
                })

        write_ping_logs(results)
        online = sum(1 for r in results if r["status"] == "OK")
        log.info(f"Ping completed: {len(results)} stations, online: {online}")
    except Exception as e:
        log.exception(f"Monitor task error: {e}")