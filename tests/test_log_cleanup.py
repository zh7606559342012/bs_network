# 创建一个临时测试脚本 test_log_cleanup.py
from datetime import datetime, timedelta
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent  # 获取项目根目录
sys.path.insert(0, str(project_root))

# 现在可以正常导入 app 模块了
from app.modules.run import clean_old_ping_logs


def create_test_logs():
    """创建测试日志文件，包含过期和未过期的数据"""
    log_dir = Path("/var/log/monitor_agent")
    log_dir.mkdir(parents=True, exist_ok=True)

    # 创建测试文件
    test_file = log_dir / "ping_test_cleanup.log"

    # 生成不同日期的日志行
    lines = []

    # 1. 12天前的日志（应该被删除）
    old_time = datetime.now() - timedelta(days=12)
    lines.append(f"{old_time.strftime('%Y-%m-%d %H:%M:%S')} | seq=000001 | OK   | rtt=1.00 ms\n")

    # 2. 11天前的日志（应该被删除）
    old_time2 = datetime.now() - timedelta(days=11)
    lines.append(f"{old_time2.strftime('%Y-%m-%d %H:%M:%S')} | seq=000002 | OK   | rtt=1.10 ms\n")

    # 3. 9天前的日志（应该保留）
    recent_time = datetime.now() - timedelta(days=9)
    lines.append(f"{recent_time.strftime('%Y-%m-%d %H:%M:%S')} | seq=000003 | OK   | rtt=1.20 ms\n")

    # 4. 今天的日志（应该保留）
    now = datetime.now()
    lines.append(f"{now.strftime('%Y-%m-%d %H:%M:%S')} | seq=000004 | OK   | rtt=1.30 ms\n")

    with open(test_file, "w", encoding="utf-8") as f:
        f.writelines(lines)

    print(f"✅ 测试日志文件已创建: {test_file}")
    print(f"📊 共 {len(lines)} 行日志")
    print("   - 2行过期（12天、11天前）")
    print("   - 2行保留（9天前、今天）")


def verify_cleanup():
    """验证清理后的结果"""
    from app.modules.run import clean_old_ping_logs

    # 执行清理
    clean_old_ping_logs()

    # 查看文件内容
    log_dir = Path("/var/log/monitor_agent")
    test_file = log_dir / "ping_test_cleanup.log"

    if test_file.exists():
        with open(test_file, "r", encoding="utf-8") as f:
            content = f.read()
            lines_count = len(content.strip().split('\n')) if content.strip() else 0

        print(f"\n📊 清理后文件行数: {lines_count}")
        print("应该保留 2 行（9天前 + 今天）")
        print("\n📄 文件内容:")
        print(content)
    else:
        print("❌ 测试文件不存在，请检查")


if __name__ == "__main__":
    # 1. 创建测试数据
    create_test_logs()

    # 2. 执行清理验证
    print("\n" + "=" * 50)
    print("执行清理验证...")
    verify_cleanup()