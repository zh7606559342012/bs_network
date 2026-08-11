#!/usr/bin/env python
# tests/test_anomaly_calc.py
"""
不依赖 pytest 的纯 Python 单元测试
运行方式（在项目根目录）：
    python tests/test_anomaly_calc.py
"""
import sys
import tempfile
import traceback
from pathlib import Path
from datetime import datetime, timedelta

# 添加项目根目录到 Python 路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from app.modules.anomaly import (
    HourlyRecord,
    aggregate_to_hourly,
    compute_single_station,
    check_continuous_fail,
    parse_log_file,
    mean,
    std_dev,
    percentile_95,
)

# =========================================================
# 工具：生成合成数据
# =========================================================
def make_records(
    station_id: int,
    start: datetime,
    days: int = 5,
    hours_per_day=range(0, 24),
    base_rtt: float = 2.0,
    loss_rate: float = 0.0,
    rtt_spike_hour=None,
    spike_day_offset=None,
    continuous_fail_last_n: int = 0,
):
    """每小时生成 4 条 ping（模拟分钟级数据）"""
    records = []
    seq = 0
    total_hours = days * 24
    for d in range(days):
        for h in hours_per_day:
            ts_base = start + timedelta(days=d, hours=h)
            is_spike = (
                spike_day_offset is not None
                and d == spike_day_offset
                and h == rtt_spike_hour
            )
            hours_from_end = total_hours - (d * 24 + h)
            force_fail = continuous_fail_last_n > 0 and hours_from_end <= continuous_fail_last_n

            for m in (0, 15, 30, 45):
                seq += 1
                ts = ts_base.replace(minute=m, second=0, microsecond=0)
                if force_fail:
                    ok = False
                    rtt = 0.0
                else:
                    ok = (seq % 100) >= int(loss_rate * 100)
                    if is_spike and ok:
                        rtt = base_rtt * 5.0
                    else:
                        rtt = base_rtt + (seq % 5) * 0.1
                records.append(
                    HourlyRecord(
                        station_id=station_id,
                        timestamp=ts,
                        rtt=rtt,
                        is_ok=ok,
                    )
                )
    return records

# =========================================================
# 简单测试运行器
# =========================================================
_passed = 0
_failed = 0

def run_test(name, func):
    global _passed, _failed
    try:
        func()
        print(f"  ✅ PASS  {name}")
        _passed += 1
    except Exception as e:
        print(f"  ❌ FAIL  {name}")
        print(f"         {type(e).__name__}: {e}")
        traceback.print_exc()
        _failed += 1

# =========================================================
# 1. 基础统计函数
# =========================================================
def test_mean_std_percentile():
    assert mean([]) == 0.0
    assert mean([1, 2, 3]) == 2.0
    assert abs(std_dev([1, 2, 3]) - 1.0) < 1e-6
    assert std_dev([5]) == 0.0
    assert percentile_95([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]) >= 9

# =========================================================
# 2. 小时聚合
# =========================================================
def test_aggregate_to_hourly_basic():
    start = datetime(2026, 7, 20, 0, 0, 0)
    records = make_records(station_id=101, start=start, days=2, base_rtt=2.0)
    hourly_map = aggregate_to_hourly(records)
    assert 101 in hourly_map
    hours = hourly_map[101]
    assert len(hours) == 48  # 2天 * 24小时
    h0 = hours[0]
    assert h0.station_id == 101
    assert h0.total_count == 4
    assert h0.ok_count == 4
    assert h0.loss_rate == 0.0
    assert abs(h0.rtt_mean - 2.0) < 1.0

def test_aggregate_with_loss():
    start = datetime(2026, 7, 20, 0, 0, 0)
    records = make_records(
        station_id=102, start=start, days=1,
        hours_per_day=range(0, 1), loss_rate=0.5
    )
    hourly_map = aggregate_to_hourly(records)
    h = hourly_map[102][0]
    assert 0.4 <= h.loss_rate <= 0.6

# =========================================================
# 3. 正常情况：历史足够，当前无异常 → 分数应较低
# =========================================================
def test_compute_normal_low_score():
    start = datetime(2026, 7, 20, 0, 0, 0)
    records = make_records(station_id=201, start=start, days=5, base_rtt=2.0)
    hourly_map = aggregate_to_hourly(records)
    result = compute_single_station(201, hourly_map[201], records)

    assert result.station_id == 201
    assert result.history_days >= 3
    assert result.anomaly_score < 50, f"正常数据分数应 <50，实际 {result.anomaly_score}"
    assert result.alert_level == "正常"
    assert result.continuous_fail_hours == 0

# =========================================================
# 4. 突变场景：最新一天某小时 RTT 暴涨 → 应触发高分
# =========================================================
def test_compute_rtt_spike_high_score():
    start = datetime(2026, 7, 20, 0, 0, 0)
    records = make_records(
        station_id=202,
        start=start,
        days=5,
        base_rtt=2.0,
        rtt_spike_hour=14,
        spike_day_offset=4,
    )
    hourly_map = aggregate_to_hourly(records)
    result = compute_single_station(202, hourly_map[202], records)

    print(f"         [spike] score={result.anomaly_score}, level={result.alert_level}, "
          f"change={result.max_change_ratio}%, rtt={result.rtt_mean}")

    assert result.history_days >= 3
    assert result.anomaly_score >= 50, f"RTT突变应 >=50，实际 {result.anomaly_score}"
    assert result.max_change_ratio > 30

# =========================================================
# 5. 连续 12 小时不通 → 强制 100 分
# =========================================================
def test_continuous_fail_12h():
    now = datetime.now().replace(minute=0, second=0, microsecond=0)

    # 最近 15 小时数据：前 3 小时正常，后 12 小时全失败
    recent_records = []
    for i in range(15):
        ts = now - timedelta(hours=14 - i)
        is_fail = i >= 3
        for m in (0, 15, 30, 45):
            recent_records.append(
                HourlyRecord(
                    station_id=203,
                    timestamp=ts.replace(minute=m),
                    rtt=0.0 if is_fail else 2.0,
                    is_ok=not is_fail,
                )
            )

    fail_hours = check_continuous_fail(203, recent_records, hours=12)
    assert fail_hours >= 12, f"应检测到 >=12 小时连续失败，实际 {fail_hours}"

    # 补 4 天历史正常数据
    hist = []
    for d in range(1, 5):
        for h in range(24):
            ts = now - timedelta(days=d, hours=h)
            for m in (0, 15, 30, 45):
                hist.append(
                    HourlyRecord(
                        station_id=203,
                        timestamp=ts.replace(minute=m),
                        rtt=2.0,
                        is_ok=True,
                    )
                )

    all_recs = hist + recent_records
    hourly_map = aggregate_to_hourly(all_recs)
    result = compute_single_station(203, hourly_map[203], all_recs)

    assert result.anomaly_score == 100.0
    assert "连续12小时不通" in result.alert_level
    assert result.continuous_fail_hours >= 12

# =========================================================
# 6. 历史不足 → 数据不足
# =========================================================
def test_insufficient_history():
    start = datetime(2026, 7, 25, 0, 0, 0)
    records = make_records(station_id=204, start=start, days=2, base_rtt=2.0)
    hourly_map = aggregate_to_hourly(records)
    result = compute_single_station(204, hourly_map[204], records)

    assert result.anomaly_score == 0.0
    assert "数据不足" in result.alert_level
    assert result.history_days < 3

# =========================================================
# 7. 日志解析（用你真实格式，写临时文件）
# =========================================================
def test_parse_log_file_format():
    log_content = (
        "2026-07-26 11:10:00 | seq=000001 | OK | rtt=0.29 ms\n"
        "2026-07-26 11:11:00 | seq=000002 | OK | rtt=1.50 ms\n"
        "2026-07-26 11:12:00 | seq=000003 | OK | rtt=0.75 ms\n"
        "2026-07-26 11:13:00 | seq=000004 | FAIL | rtt=0.00 ms\n"
        "2026-07-26 11:14:00 | seq=000005 | OK | rtt=1.69 ms\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        log_file = Path(tmp) / "ping_17216123201.log"
        log_file.write_text(log_content, encoding="utf-8")

        cutoff = datetime(2026, 7, 26, 0, 0, 0)
        records = parse_log_file(str(log_file), station_id=999, cutoff=cutoff)

        assert len(records) == 5
        assert records[0].is_ok is True
        assert abs(records[0].rtt - 0.29) < 1e-6
        assert records[3].is_ok is False
        assert records[3].rtt == 0.0

# =========================================================
# 主入口
# =========================================================
if __name__ == "__main__":
    print("=" * 60)
    print("开始运行 anomaly 核心逻辑测试（无 pytest）")
    print("=" * 60)

    run_test("test_mean_std_percentile", test_mean_std_percentile)
    run_test("test_aggregate_to_hourly_basic", test_aggregate_to_hourly_basic)
    run_test("test_aggregate_with_loss", test_aggregate_with_loss)
    run_test("test_compute_normal_low_score", test_compute_normal_low_score)
    run_test("test_compute_rtt_spike_high_score", test_compute_rtt_spike_high_score)
    run_test("test_continuous_fail_12h", test_continuous_fail_12h)
    run_test("test_insufficient_history", test_insufficient_history)
    run_test("test_parse_log_file_format", test_parse_log_file_format)

    print("=" * 60)
    print(f"结果: {_passed} 通过, {_failed} 失败")
    print("=" * 60)

    if _failed > 0:
        sys.exit(1)
    else:
        print("全部通过 ✅")
        sys.exit(0)