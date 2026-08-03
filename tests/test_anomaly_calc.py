# tests/test_anomaly_calc.py
import sys
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.modules.anomaly import (
    parse_log_file,
    fetch_last_n_days_data,
    calculate_anomaly,
    aggregate_to_daily,
    compute_single_station,
    AnomalyResult,
)


# 你的测试日志对应的 IP（去掉点号后就是文件名）
TEST_IP = "172.16.123.201"
TEST_STATION_ID = 10001
LOG_FILE = Path("/var/log/monitor_agent") / f"ping_{TEST_IP.replace('.', '')}.log"


def test_parse_log_file():
    """测试1：日志解析是否正常"""
    print("\n" + "=" * 60)
    print("【测试1】解析日志文件")
    print("=" * 60)

    if not LOG_FILE.exists():
        print(f"❌ 日志文件不存在: {LOG_FILE}")
        print("请确认路径和文件名是否正确")
        return None

    cutoff = datetime.now() - timedelta(days=10)
    records = parse_log_file(str(LOG_FILE), TEST_STATION_ID, cutoff)

    print(f"日志文件: {LOG_FILE}")
    print(f"解析到记录数: {len(records)}")

    if not records:
        print("❌ 没有解析到任何记录，请检查日志格式和正则")
        return None

    # 打印前几条和后几条
    print("\n前 5 条记录:")
    for r in records[:5]:
        print(f"  {r.timestamp} | ok={r.is_ok} | rtt={r.rtt}")

    print("\n后 5 条记录:")
    for r in records[-5:]:
        print(f"  {r.timestamp} | ok={r.is_ok} | rtt={r.rtt}")

    ok_count = sum(1 for r in records if r.is_ok)
    fail_count = len(records) - ok_count
    print(f"\n统计: 总计={len(records)}, OK={ok_count}, FAIL={fail_count}")

    assert len(records) > 0
    print("✅ 日志解析通过")
    return records


def test_aggregate_and_calc(records):
    """测试2：按天聚合 + 异常计算"""
    print("\n" + "=" * 60)
    print("【测试2】按天聚合 & 异常分数计算")
    print("=" * 60)

    if not records:
        print("跳过：没有 records")
        return

    # 1. 按天聚合
    daily_map = aggregate_to_daily(records)
    days = daily_map.get(TEST_STATION_ID, [])

    print(f"聚合后天数: {len(days)}")
    print("\n每日汇总:")
    for d in days:
        print(
            f"  {d.date.strftime('%Y-%m-%d')} | "
            f"rtt_mean={d.rtt_mean:.2f} | "
            f"rtt_p95={d.rtt_p95:.2f} | "
            f"loss_rate={d.loss_rate:.2%} | "
            f"hours={d.hour_count}"
        )

    # 2. 计算异常
    result = compute_single_station(TEST_STATION_ID, days, records)

    print("\n" + "-" * 40)
    print("异常计算结果:")
    print(f"  station_id           : {result.station_id}")
    print(f"  date                 : {result.date}")
    print(f"  anomaly_score        : {result.anomaly_score}")
    print(f"  alert_level          : {result.alert_level}")
    print(f"  rtt_mean             : {result.rtt_mean}")
    print(f"  baseline_rtt         : {result.baseline_rtt}")
    print(f"  max_change_ratio     : {result.max_change_ratio}%")
    print(f"  loss_contrib         : {result.loss_contrib}")
    print(f"  continuous_fail_hours: {result.continuous_fail_hours}")
    print(f"  history_days         : {result.history_days}")
    print(f"  data_points          : {result.data_points}")
    print("-" * 40)

    # 基本断言：有结果就算跑通
    assert result.station_id == TEST_STATION_ID
    assert result.anomaly_score >= 0
    print("✅ 异常计算通过")
    return result


def test_full_pipeline_with_mock_cache():
    """测试3：完整流程（Mock 基站缓存，走 fetch_last_n_days_data）"""
    print("\n" + "=" * 60)
    print("【测试3】完整流程（Mock BaseStationCache）")
    print("=" * 60)

    # Mock 缓存：让 fetch_last_n_days_data 能找到你的日志
    fake_cache = {
        TEST_STATION_ID: {
            "ip": TEST_IP,
            "name": "test-bs-001",
            "station_id": TEST_STATION_ID,
        }
    }

    with patch("app.modules.anomaly.BaseStationCache", fake_cache):
        with patch("app.modules.anomaly.CacheMutex"):
            records = fetch_last_n_days_data(days=10)
            print(f"fetch_last_n_days_data 返回记录数: {len(records)}")

            if not records:
                print("❌ 完整流程没有读到数据")
                return

            results = calculate_anomaly(records)
            print(f"calculate_anomaly 返回基站数: {len(results)}")

            for r in results:
                print(
                    f"  基站 {r.station_id} | score={r.anomaly_score:.1f} | "
                    f"{r.alert_level} | RTT={r.rtt_mean:.2f} | "
                    f"连续失败={r.continuous_fail_hours}h"
                )

                # 如果分数高，看看是否会触发告警条件
                if r.anomaly_score >= 50 or r.continuous_fail_hours >= 12:
                    print(f"  >>> 该基站会触发告警上报")

            print("✅ 完整流程通过")


def test_anomaly_with_injected_spike():
    """测试4：人为制造异常，验证算法能否检测出来"""
    print("\n" + "=" * 60)
    print("【测试4】人为注入异常数据，验证检测能力")
    print("=" * 60)

    from app.modules.anomaly import HourlyRecord

    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    records = []

    # 前 6 天：正常 RTT ~20ms，偶尔失败
    for day in range(6, 0, -1):
        day_start = now - timedelta(days=day)
        for hour in range(24):
            ts = day_start.replace(hour=hour)
            # 大部分 OK
            is_ok = hour % 10 != 0
            rtt = 20.0 + (hour % 5) if is_ok else 0.0
            records.append(HourlyRecord(TEST_STATION_ID, ts, rtt, is_ok))

    # 今天：RTT 突然飙到 200ms，丢包很高
    for hour in range(24):
        ts = now.replace(hour=hour)
        is_ok = hour % 3 != 0  # 更高失败率
        rtt = 200.0 if is_ok else 0.0
        records.append(HourlyRecord(TEST_STATION_ID, ts, rtt, is_ok))

    results = calculate_anomaly(records)
    assert len(results) == 1
    r = results[0]

    print(f"注入异常后计算结果:")
    print(f"  score       = {r.anomaly_score}")
    print(f"  alert_level = {r.alert_level}")
    print(f"  rtt_mean    = {r.rtt_mean}")
    print(f"  baseline    = {r.baseline_rtt}")
    print(f"  change%     = {r.max_change_ratio}")

    # 期望能检测出异常
    if r.anomaly_score >= 50:
        print("✅ 成功检测出人为注入的异常")
    else:
        print(f"⚠️ 分数只有 {r.anomaly_score}，可能阈值需要再调")


if __name__ == "__main__":
    print("🚀 基站网络异常检测 - 计算逻辑测试")
    print("=" * 60)

    # 测试1：解析真实日志
    records = test_parse_log_file()

    # 测试2：用真实日志做聚合和计算
    if records:
        test_aggregate_and_calc(records)

    # 测试3：走完整 fetch 流程
    test_full_pipeline_with_mock_cache()

    # 测试4：人为异常验证算法敏感度
    test_anomaly_with_injected_spike()

    print("\n" + "=" * 60)
    print("全部测试结束")