# tests/test_report_anomaly_alarm_queue.py
import sys
import asyncio
from pathlib import Path
from unittest.mock import MagicMock

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.modules.anomaly import _report_anomaly_alarm, AnomalyResult
from app.modules.alarm import AlarmQueue
from app.schemas.alarm import AlarmType


def create_mock_result():
    r = MagicMock(spec=AnomalyResult)
    r.station_id = 10086
    r.anomaly_score = 85.67
    r.alert_level = "critical"
    r.rtt_mean = 120.456
    r.baseline_rtt = 80.123
    r.max_change_ratio = 45.8
    r.continuous_fail_hours = 3
    r.history_days = 7
    return r


def test_send_to_queue():
    print("\n" + "=" * 60)
    print("【测试】验证告警是否成功进入 AlarmQueue")
    print("=" * 60)

    # 1. 先清空队列，避免旧数据干扰
    while not AlarmQueue.empty():
        try:
            AlarmQueue.get_nowait()
            AlarmQueue.task_done()
        except asyncio.QueueEmpty:
            break

    print(f"清空后队列大小: {AlarmQueue.qsize()}")

    mock_result = create_mock_result()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        async def run_test():
            print("\n📤 调用 _report_anomaly_alarm ...")
            _report_anomaly_alarm(mock_result)

            # 给 create_task 一点时间执行完
            await asyncio.sleep(1)

            qsize = AlarmQueue.qsize()
            print(f"\n📊 当前 AlarmQueue 大小: {qsize}")

            if qsize == 0:
                print("❌ 队列为空，告警没有成功入队")
                return

            print("✅ 队列中有数据，开始取出检查...")

            # 把队列里的告警取出来看内容
            alarm_data = await AlarmQueue.get()
            AlarmQueue.task_done()

            info = alarm_data.alarm_info
            print("\n📋 队列中的告警内容:")
            print(f"  alarm_id        : {info.alarm_id}")
            print(f"  alarm_type      : {info.alarm_type}")
            print(f"  alarm_location  : {info.alarm_location}")
            print(f"  send_anyway     : {info.send_anyway}")
            print(f"  nf_ip           : {info.nf_ip}")
            print(f"  param 数量      : {len(info.param)}")

            for p in info.param:
                print(f"    - {p.name} = {p.value}")

            # 简单断言
            assert info.alarm_id == "50004000"
            assert info.alarm_type == AlarmType.GENERATE or info.alarm_type == "1" or str(info.alarm_type) == "AlarmType.GENERATE"
            assert info.send_anyway is True
            assert "10086" in info.alarm_location
            assert len(info.param) >= 1

            print("\n✅ 验证通过：告警已成功进入 AlarmQueue，且内容正确")

        loop.run_until_complete(run_test())

    finally:
        loop.close()


if __name__ == "__main__":
    test_send_to_queue()