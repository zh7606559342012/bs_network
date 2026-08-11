#!/usr/bin/env python3
"""
把 ping 日志的时间整体平移到以当前时间为基准
使用方法：
    python shift_log_time.py ping_2021.log
"""

import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

def shift_log_timestamps(input_file: str, output_file: str = None):
    input_path = Path(input_file)
    if not input_path.exists():
        print(f"❌ 文件不存在: {input_file}")
        return

    if output_file is None:
        output_file = input_path.stem + "_shifted.log"

    # 匹配日志行的时间戳
    pattern = re.compile(
        r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*\|\s*(.*)$"
    )

    lines = input_path.read_text(encoding="utf-8").splitlines()

    # 1. 先找出最后一条有效时间戳
    last_ts = None
    for line in reversed(lines):
        m = pattern.match(line.strip())
        if m:
            last_ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            break

    if last_ts is None:
        print("❌ 没有解析到任何时间戳")
        return

    # 2. 计算需要平移的天数（让最后一条变成今天，保留原来的时分秒）
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    last_date = last_ts.replace(hour=0, minute=0, second=0, microsecond=0)
    delta_days = (today - last_date).days

    print(f"原始最后时间 : {last_ts}")
    print(f"平移天数     : {delta_days} 天")
    print(f"平移后最后时间: {last_ts + timedelta(days=delta_days)}")
    print(f"输出文件     : {output_file}")
    print("-" * 60)

    # 3. 逐行替换时间
    new_lines = []
    for line in lines:
        m = pattern.match(line.strip())
        if m:
            old_ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            new_ts = old_ts + timedelta(days=delta_days)
            new_line = f"{new_ts.strftime('%Y-%m-%d %H:%M:%S')} | {m.group(2)}"
            new_lines.append(new_line)
        else:
            new_lines.append(line)  # 保留空行或格式不对的行

    # 4. 写入新文件
    Path(output_file).write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print(f"✅ 转换完成！共处理 {len(new_lines)} 行")
    print(f"新文件已保存为: {output_file}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python shift_log_time.py <日志文件>")
        print("示例: python shift_log_time.py ping_2021.log")
        sys.exit(1)

    shift_log_timestamps(sys.argv[1])