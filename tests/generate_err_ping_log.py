#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成基站 Ping 日志模拟数据（含连续断档，断档放在最近时段）
成功日志示例：
2026-08-10 14:16:10 | seq=101192 | OK   | rtt=15.9 ms

失败/断档日志示例：
2026-08-05 00:51:29 | seq=096703 | FAIL | rtt=- (timeout/unreachable)
"""

from datetime import datetime, timedelta
import random
from pathlib import Path


def generate_ping_logs(
        days: int = 10,                    # 生成最近多少天的数据
        interval_seconds: int = 30,        # 每30秒一条
        start_seq: int = 1,
        output_file: str = "ping_17216123202.log",
        fail_rate: float = 0.015,          # 正常时段的随机失败率
        gap_hours: int = 15                # 连续断档小时数
):
    """
    生成模拟 Ping 日志
    默认从当前时间往前推 days 天开始
    断档放在结尾附近，保证落在最近 12h 检测窗口内
    """
    # 根据当前时间自动计算起始/结束时间（对齐到整秒）
    end = datetime.now().replace(microsecond=0)
    start = end - timedelta(days=days)

    # 断档放到「结束前 1 小时再往前 gap_hours」——保证落在最近检测窗口内
    gap_end = end - timedelta(hours=1)
    gap_start = gap_end - timedelta(hours=gap_hours)

    # 对齐到整秒
    gap_start = gap_start.replace(microsecond=0)
    gap_end = gap_end.replace(microsecond=0)

    current = start
    seq = start_seq
    lines = []

    while current <= end:
        # 判断是否处于断档区间
        in_gap = gap_start <= current < gap_end

        if in_gap:
            # 断档期间：强制生成 FAIL 日志
            status = "FAIL"
            rtt_str = "rtt=- (timeout/unreachable)"
        else:
            # 正常时段：按失败率随机生成
            if random.random() < fail_rate:
                status = "FAIL"
                rtt_str = "rtt=- (timeout/unreachable)"
            else:
                status = "OK  "          # 右侧补空格保持对齐
                rtt = round(random.uniform(13.0, 16.5), 1)
                rtt_str = f"rtt={rtt} ms"

        line = (
            f"{current.strftime('%Y-%m-%d %H:%M:%S')} | "
            f"seq={seq:06d} | "
            f"{status} | "
            f"{rtt_str}"
        )
        lines.append(line)

        current += timedelta(seconds=interval_seconds)
        seq += 1

    # 写入文件
    output_path = Path(output_file)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"✅ 生成完成！")
    print(f"   文件路径 : {output_path.absolute()}")
    print(f"   总记录数 : {len(lines)}")
    print(f"   时间范围 : {start} → {end}")
    print(f"   起始序号 : {start_seq:06d}")
    print(f"   结束序号 : {seq - 1:06d}")
    print(f"   断档时间 : {gap_start} → {gap_end} （连续 {gap_hours} 小时全部为 FAIL，位于最近时段）")

    return output_path


if __name__ == "__main__":
    # ==================== 可修改参数 ====================
    generate_ping_logs(
        days=10,                           # 生成当前时间往前10天的数据
        interval_seconds=30,               # 间隔（秒）
        start_seq=1,                       # 起始序号
        output_file="ping_17216123202.log",# 输出文件名
        fail_rate=0.015,                   # 正常时段失败比例
        gap_hours=15                       # 连续15小时断档（全部FAIL）
    )