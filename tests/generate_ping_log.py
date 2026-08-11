#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成基站 Ping 日志模拟数据
格式示例：
2026-07-24 15:04:21 | seq=000274 | OK | rtt=1.38 ms
"""

from datetime import datetime, timedelta
import random
from pathlib import Path


def generate_ping_logs(
        days: int = 10,                    # 生成最近多少天的数据
        interval_seconds: int = 60,        # 每分钟一条
        start_seq: int = 1,
        output_file: str = "ping_17216123202.log",
        fail_rate: float = 0.02            # 2% 的失败率
):
    """
    生成模拟 Ping 日志
    默认从当前时间往前推 days 天开始
    """
    # 关键改动：根据当前时间自动计算起始时间
    end = datetime.now().replace(second=0, microsecond=0)  # 对齐到整分钟
    start = end - timedelta(days=days)

    current = start
    seq = start_seq
    lines = []

    while current <= end:  # 包含当前这一分钟
        # 随机生成 RTT
        if random.random() < fail_rate:
            status = "FAIL"
            rtt = 0.00
        else:
            status = "OK"
            rtt = round(random.uniform(0.25, 2.80), 2)

        line = (
            f"{current.strftime('%Y-%m-%d %H:%M:%S')} | "
            f"seq={seq:06d} | "
            f"{status:4} | "
            f"rtt={rtt:.2f} ms"
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
        interval_seconds=60,               # 间隔（秒），60=每分钟一条
        start_seq=1,                       # 起始序号
        output_file="ping_17216123202.log",# 输出文件名
        fail_rate=0.015                    # 失败比例（1.5%）
    )