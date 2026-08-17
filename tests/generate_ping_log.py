#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成基站 Ping 日志模拟数据
成功日志示例：
2026-08-10 14:16:10 | seq=101192 | OK   | rtt=15.9 ms

失败日志示例：
2026-08-11 10:37:02 | seq=027969 | FAIL | rtt=- (timeout/unreachable)
"""

from datetime import datetime, timedelta
import random
from pathlib import Path


def generate_ping_logs(
        days: int = 10,                    # 生成最近多少天的数据
        interval_seconds: int = 30,        # 每30秒一条（匹配示例）
        start_seq: int = 1,
        output_file: str = "ping_17216123202.log",
        fail_rate: float = 0.015           # 失败率
):
    """
    生成模拟 Ping 日志
    默认从当前时间往前推 days 天开始
    """
    # 根据当前时间自动计算起始时间（对齐到整秒）
    end = datetime.now().replace(microsecond=0)
    start = end - timedelta(days=days)

    current = start
    seq = start_seq
    lines = []

    while current <= end:
        # 随机决定成功或失败
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

    return output_path


if __name__ == "__main__":
    # ==================== 可修改参数 ====================
    generate_ping_logs(
        days=10,                           # 生成当前时间往前10天的数据
        interval_seconds=30,               # 间隔（秒）
        start_seq=1,                       # 起始序号
        output_file="ping_17216123202.log",# 输出文件名
        fail_rate=0.015                    # 失败比例
    )