#!/usr/bin/env python
# tests/test_anomaly_calc.py
"""
用真实 ping 日志测试 anomaly 核心逻辑（pandas 版）
运行：
    python tests/test_anomaly_calc.py
"""
import sys
from pathlib import Path
from datetime import datetime, timedelta

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from app.modules.anomaly import (
    aggregate_to_hourly,
    compute_single_station,
    check_continuous_fail,
    parse_log_file,
)

# ====================== 配置 ======================
LOG_PATH = "/var/log/monitor_agent/ping_17216123201.log"
STATION_ID = 17216123201
FETCH_DAYS = 7

def main():
    print("=" * 70)
    print("用真实日志测试 anomaly 算法（pandas 版）")
    print("=" * 70)

    log_file = Path(LOG_PATH)
    if not log_file.exists():
        print(f"❌ 日志文件不存在: {LOG_PATH}")
        sys.exit(1)

    # ---------- 1. 解析日志 ----------
    cutoff = datetime.now() - timedelta(days=FETCH_DAYS)
    print(f"\n[1] 解析日志: {LOG_PATH}")
    print(f"    截止时间: {cutoff} 之后的数据")

    df = parse_log_file(str(log_file), station_id=STATION_ID, cutoff=cutoff)
    print(f"    解析到 {len(df)} 条 ping 记录")

    if df.empty:
        print("❌ 没有解析到任何记录，请检查日志格式或 cutoff 时间")
        sys.exit(1)

    print(f"    时间范围: {df['timestamp'].min()} ~ {df['timestamp'].max()}")
    ok_cnt = int(df["is_ok"].sum())
    total = len(df)
    print(f"    成功/总数: {ok_cnt}/{total}  丢包率≈{(1 - ok_cnt / total) * 100:.1f}%")

    # ---------- 2. 按小时聚合 ----------
    print(f"\n[2] 按小时聚合")
    hourly = aggregate_to_hourly(df)
    # 只保留当前基站（parse 时已是单站，这里再滤一层更稳妥）
    hourly = hourly[hourly["station_id"] == STATION_ID].copy()
    print(f"    聚合得到 {len(hourly)} 个小时点")

    if hourly.empty:
        print("❌ 聚合结果为空")
        sys.exit(1)

    hourly["date"] = hourly["hour"].dt.date
    by_date = hourly.groupby("date")
    print(f"    覆盖天数: {by_date.ngroups}")
    for d, g in sorted(by_date, key=lambda x: x[0]):
        hs = sorted(g["hour"].dt.hour.tolist())
        print(f"      {d}: {len(hs)} 小时  ({min(hs):02d}:00 ~ {max(hs):02d}:00)")

    print(f"\n    最近数据预览（小时级）:")
    print(f"    {'日期':<12} {'小时':<6} {'rtt_mean':>10} {'rtt_p95':>10} {'loss':>8} {'count':>6}")
    print("    " + "-" * 60)
    tail = hourly.tail(48)
    for _, h in tail.iterrows():
        print(
            f"    {h['hour'].strftime('%Y-%m-%d'):<12} {h['hour'].hour:02d}:00  "
            f"{h['rtt_mean']:10.2f} {h['rtt_p95']:10.2f} {h['loss_rate']:8.2%} {int(h['total_count']):6d}"
        )

    # ---------- 3. 连续失败检查 ----------
    print(f"\n[3] 连续失败检查（最近 12 小时）")
    fail_hours = check_continuous_fail(STATION_ID, df, hours=12)
    print(f"    连续失败小时数: {fail_hours}")

    # ---------- 4. 跑异常计算 ----------
    print(f"\n[4] 执行 compute_single_station")
    result = compute_single_station(STATION_ID, hourly, df)

    print("\n" + "=" * 70)
    print("【最终结果】")
    print("=" * 70)
    print(f"  station_id           : {result.station_id}")
    print(f"  date                 : {result.date}")
    print(f"  anomaly_score        : {result.anomaly_score}")
    print(f"  alert_level          : {result.alert_level}")
    print(f"  rtt_mean (当天)      : {result.rtt_mean} ms")
    print(f"  baseline_rtt (历史)  : {result.baseline_rtt} ms")
    print(f"  max_change_ratio     : {result.max_change_ratio} %")
    print(f"  rtt_contrib          : {result.rtt_contrib}")
    print(f"  rtt_p95_contrib      : {result.rtt_p95_contrib}")
    print(f"  loss_contrib         : {result.loss_contrib}")
    print(f"  data_points          : {result.data_points}")
    print(f"  history_days         : {result.history_days}")
    print(f"  continuous_fail_hours: {result.continuous_fail_hours}")
    print("=" * 70)

    # ---------- 5. 解读 ----------
    print("\n【解读提示】")
    if "数据不足" in str(result.alert_level):
        print("  → 历史天数不足 3 天，或「同一小时」的历史样本 < 3。")
        print("  → 算法需要至少 3 天「同一小时」的数据才能做同比。")
    elif result.continuous_fail_hours >= 12:
        print("  → 触发了「连续 12 小时不通」强制 100 分逻辑。")
    elif result.anomaly_score >= 50:
        print("  → 分数较高，请结合 max_change_ratio / loss_contrib 看原因。")
    else:
        print("  → 当前判定为正常或关注级别较低。")

    print("\n测试完成。")

if __name__ == "__main__":
    main()