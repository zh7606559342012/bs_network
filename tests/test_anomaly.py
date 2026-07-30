#!/usr/bin/env python
# tests/test_anomaly.py

import sys
from pathlib import Path
from datetime import datetime, timedelta
import re

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.core.database import BaseStationCache, CacheMutex


def create_test_logs():
    """创建测试用的 Ping 日志，模拟不同场景"""
    log_dir = Path("/var/log/monitor_agent")
    log_dir.mkdir(parents=True, exist_ok=True)

    # 基站IP和ID映射
    stations = {
        1001: {"ip": "192.168.1.1", "name": "正常基站"},
        1002: {"ip": "192.168.1.2", "name": "高延迟基站"},
        1003: {"ip": "192.168.1.3", "name": "丢包基站"},
    }

    # 更新内存缓存
    with CacheMutex:
        BaseStationCache.clear()
        for sid, info in stations.items():
            BaseStationCache[sid] = {
                "station_id": sid,
                "ip": info["ip"],
                "name": info["name"],
                "region": "测试区域"
            }

    now = datetime.now()
    scenarios = [
        # 场景1: 正常基站 (1001) - RTT稳定在 1-2ms
        (1001, "19216811", 1.5, 0.3, 7, "OK"),
        # 场景2: 高延迟基站 (1002) - RTT从2ms飙升到50ms
        (1002, "19216812", 2.0, 0.5, 3, "OK"),  # 前3天正常
        (1002, "19216812", 50.0, 10.0, 4, "OK"),  # 后4天高延迟
        # 场景3: 丢包基站 (1003) - 50%丢包率
        (1003, "19216813", 2.0, 0.5, 7, "OK"),  # 正常时期
    ]

    # 生成场景1和3的日志（正常+稳定）
    for sid, ip_prefix, rtt, variation, days, status in scenarios:
        for day in range(days):
            day_dt = now - timedelta(days=days - 1 - day)
            for hour in range(8, 22):  # 每天8点到22点，每小时一条
                # 加入随机变化
                import random
                current_rtt = rtt + random.uniform(-variation, variation)
                current_rtt = max(0.1, current_rtt)

                log_time = day_dt.replace(hour=hour, minute=random.randint(0, 59))
                if log_time > now:
                    continue

                log_file = log_dir / f"ping_{ip_prefix}.log"
                with open(log_file, "a", encoding="utf-8") as f:
                    seq = day * 24 + hour + 1
                    f.write(
                        f"{log_time.strftime('%Y-%m-%d %H:%M:%S')} | seq={seq:06d} | {status:4} | rtt={current_rtt:.2f} ms\n")

    # 场景3特殊处理：最近3天加丢包
    for day in range(3, 7):
        day_dt = now - timedelta(days=6 - day)
        for hour in range(8, 22):
            # 50%概率丢包
            import random
            if random.random() < 0.5:
                status = "FAIL"
                rtt = -1.0
            else:
                status = "OK"
                rtt = 2.0 + random.uniform(-0.3, 0.3)

            log_time = day_dt.replace(hour=hour, minute=random.randint(0, 59))
            if log_time > now:
                continue

            log_file = log_dir / "ping_19216813.log"
            with open(log_file, "a", encoding="utf-8") as f:
                seq = day * 24 + hour + 1
                f.write(f"{log_time.strftime('%Y-%m-%d %H:%M:%S')} | seq={seq:06d} | {status:4} | rtt={rtt:.2f} ms\n")

    print("✅ 测试日志文件创建完成！")
    print("📊 场景说明:")
    print("   - 基站1001 (192.168.1.1): 正常，RTT稳定")
    print("   - 基站1002 (192.168.1.2): 高延迟，RTT从2ms飙升到50ms")
    print("   - 基站1003 (192.168.1.3): 丢包率50%")


def test_anomaly_detection():
    """测试异常检测功能"""
    from app.modules.anomaly import detect_network_anomaly

    print("\n" + "=" * 60)
    print("开始执行异常检测...")
    print("=" * 60)

    # 执行检测
    detect_network_anomaly()

    # 检查结果文件
    log_dir = Path("/var/log/monitor_agent")
    anomaly_files = sorted(log_dir.glob("anomaly_*.csv"), reverse=True)

    if anomaly_files:
        latest = anomaly_files[0]
        print(f"\n📄 最新检测结果: {latest.name}")
        print("-" * 60)
        with open(latest, "r", encoding="utf-8") as f:
            content = f.read()
            print(content)
    else:
        print("❌ 未找到检测结果文件")


def cleanup_test_logs():
    """清理测试日志"""
    log_dir = Path("/var/log/monitor_agent")
    for f in log_dir.glob("ping_192168*.log"):
        f.unlink()
        print(f"🗑️ 删除: {f.name}")
    print("✅ 测试日志已清理")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--cleanup", action="store_true", help="清理测试日志")
    args = parser.parse_args()

    if args.cleanup:
        cleanup_test_logs()
    else:
        # 1. 创建测试数据
        create_test_logs()

        # 2. 执行检测
        test_anomaly_detection()

        print("\n💡 提示: 运行 python test_anomaly.py --cleanup 清理测试日志")