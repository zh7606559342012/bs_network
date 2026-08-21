# 创建一个临时测试脚本 test_anomaly_save_cleanup.py
from datetime import datetime, timedelta
from pathlib import Path
from typing import List
from dataclasses import dataclass
import sys
import pandas as pd

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent  # 获取项目根目录
sys.path.insert(0, str(project_root))

# 现在可以正常导入
from app.modules.anomaly import save_anomaly_results   # 根据你实际路径调整


# ====================== 模拟 AnomalyResult ======================
@dataclass
class AnomalyResult:
    station_id: str
    date: str
    anomaly_score: float
    alert_level: str
    rtt_mean: float
    baseline_rtt: float
    max_change_ratio: float
    loss_contrib: float
    continuous_fail_hours: int
    history_days: int


def create_test_anomaly_files():
    """创建测试用的 anomaly 文件：包含过期和未过期的文件"""
    log_dir = Path("/var/log/monitor_agent")
    log_dir.mkdir(parents=True, exist_ok=True)

    # 1. 今天的文件（应该保留）
    today = datetime.now()
    today_file = log_dir / f"anomaly_{today.strftime('%Y%m%d')}.csv"
    pd.DataFrame([{"station_id": "test_today", "date": today.strftime("%Y-%m-%d")}]).to_csv(
        today_file, index=False, encoding="utf-8"
    )
    print(f"✅ 创建今天文件: {today_file.name}")

    # 2. 29 天前的文件（应该保留）
    day29 = today - timedelta(days=29)
    file29 = log_dir / f"anomaly_{day29.strftime('%Y%m%d')}.csv"
    pd.DataFrame([{"station_id": "test_29d", "date": day29.strftime("%Y-%m-%d")}]).to_csv(
        file29, index=False, encoding="utf-8"
    )
    print(f"✅ 创建 29 天前文件: {file29.name}")

    # 3. 30 天前的文件（应该被删除）
    day30 = today - timedelta(days=30)
    file30 = log_dir / f"anomaly_{day30.strftime('%Y%m%d')}.csv"
    pd.DataFrame([{"station_id": "test_30d", "date": day30.strftime("%Y-%m-%d")}]).to_csv(
        file30, index=False, encoding="utf-8"
    )
    print(f"✅ 创建 30 天前文件: {file30.name}")

    # 4. 35 天前的文件（应该被删除）
    day35 = today - timedelta(days=35)
    file35 = log_dir / f"anomaly_{day35.strftime('%Y%m%d')}.csv"
    pd.DataFrame([{"station_id": "test_35d", "date": day35.strftime("%Y-%m-%d")}]).to_csv(
        file35, index=False, encoding="utf-8"
    )
    print(f"✅ 创建 35 天前文件: {file35.name}")

    print("\n📊 初始文件状态：")
    print("   - 今天 + 29天前 → 应保留")
    print("   - 30天前 + 35天前 → 应删除")

def test_save_and_cleanup():
    """测试落盘 + 清理逻辑"""
    log_dir = Path("/var/log/monitor_agent")

    # ========== 1. 准备测试数据 ==========
    results: List[AnomalyResult] = [
        AnomalyResult(
            station_id="STA001",
            date=datetime.now().strftime("%Y-%m-%d"),
            anomaly_score=0.85,
            alert_level="high",
            rtt_mean=12.5,
            baseline_rtt=8.0,
            max_change_ratio=1.56,
            loss_contrib=0.3,
            continuous_fail_hours=2,
            history_days=14,
        ),
        AnomalyResult(
            station_id="STA002",
            date=datetime.now().strftime("%Y-%m-%d"),
            anomaly_score=0.42,
            alert_level="medium",
            rtt_mean=9.1,
            baseline_rtt=8.2,
            max_change_ratio=1.11,
            loss_contrib=0.1,
            continuous_fail_hours=0,
            history_days=14,
        ),
    ]

    print("\n" + "=" * 60)
    print("1️⃣  执行 save_anomaly_results 落盘...")
    save_anomaly_results(results)

    # ========== 2. 验证今天文件是否正确写入 ==========
    today_str = datetime.now().strftime("%Y%m%d")
    today_file = log_dir / f"anomaly_{today_str}.csv"

    if today_file.exists():
        df = pd.read_csv(today_file)
        print(f"✅ 今天文件已生成: {today_file.name}")
        print(f"   行数: {len(df)} （期望 2 行）")
        print(f"   列名: {list(df.columns)}")
        print("\n📄 文件内容预览:")
        print(df.to_string(index=False))

        assert len(df) == 2, "今天文件行数不正确"
        assert list(df.columns) == [
            "station_id", "date", "anomaly_score", "alert_level",
            "rtt_mean", "baseline_rtt", "max_change_ratio",
            "loss_contrib", "continuous_fail_hours", "history_days"
        ]
    else:
        raise AssertionError("❌ 今天文件未生成！")

    # ========== 3. 验证清理逻辑（只关心我们创建的测试文件） ==========
    print("\n" + "=" * 60)
    print("2️⃣  验证清理结果...")

    day29_str = (datetime.now() - timedelta(days=29)).strftime("%Y%m%d")
    day30_str = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
    day35_str = (datetime.now() - timedelta(days=35)).strftime("%Y%m%d")

    # 应该保留的
    assert (log_dir / f"anomaly_{today_str}.csv").exists(), "今天文件应保留"
    assert (log_dir / f"anomaly_{day29_str}.csv").exists(), "29天前文件应保留"

    # 应该删除的
    assert not (log_dir / f"anomaly_{day30_str}.csv").exists(), "30天前文件应被删除"
    assert not (log_dir / f"anomaly_{day35_str}.csv").exists(), "35天前文件应被删除"

    print("✅ 清理逻辑正确！")
    print("   - 今天文件 → 保留")
    print("   - 29天前文件 → 保留")
    print("   - 30天前文件 → 已删除")
    print("   - 35天前文件 → 已删除")


def cleanup_test_files():
    """测试结束后清理所有测试文件（可选）"""
    log_dir = Path("/var/log/monitor_agent")
    for f in log_dir.glob("anomaly_*.csv"):
        f.unlink()
        print(f"🗑️  已删除测试文件: {f.name}")


if __name__ == "__main__":
    print("🚀 开始测试 save_anomaly_results 落盘 & 清理逻辑\n")

    # 1. 创建各种日期的测试文件
    create_test_anomaly_files()

    # 2. 执行落盘 + 验证清理
    test_save_and_cleanup()

    # 3. （可选）清理测试产生的文件
    # print("\n" + "=" * 60)
    # print("3️⃣  清理测试文件...")
    # cleanup_test_files()

    print("\n✅ 测试完成")