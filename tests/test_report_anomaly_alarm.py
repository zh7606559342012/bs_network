# tests/test_report_anomaly_alarm.py
import sys
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# ==================== 路径处理 ====================
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# ==================== 导入（请确认路径正确） ====================
from app.modules.anomaly import _report_anomaly_alarm, AnomalyResult, AlarmParam, AlarmType


def create_mock_result():
    """构造一个假的 AnomalyResult"""
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


def test_success():
    """测试正常路径"""
    print("\n" + "=" * 50)
    print("【测试1】正常路径：验证参数构造和调用")

    mock_result = create_mock_result()

    with patch("app.modules.anomaly.send_alarm", new_callable=AsyncMock) as mock_send_alarm, \
         patch("app.modules.anomaly.asyncio.create_task") as mock_create_task, \
         patch("app.modules.anomaly.log") as mock_log:

        _report_anomaly_alarm(mock_result)

        assert mock_create_task.called, "create_task 没有被调用"
        print("✅ create_task 已被调用")

        # 运行协程
        coro = mock_create_task.call_args[0][0]
        asyncio.run(coro)

        # 检查参数
        mock_send_alarm.assert_called_once()
        call_kwargs = mock_send_alarm.call_args.kwargs

        assert call_kwargs["alarm_id"] == "50004000"
        assert call_kwargs["alarm_type"] == AlarmType.GENERATE
        assert call_kwargs["alarm_identifier"] == "bs location anomaly station:10086"
        assert call_kwargs["send_anyway"] is True

        extra = call_kwargs["extra_para"]
        assert len(extra) == 8
        assert extra[0].name == "station_id" and extra[0].value == "10086"
        assert extra[1].name == "anomaly_score" and extra[1].value == "85.7"
        assert extra[2].name == "alert_level" and extra[2].value == "critical"
        assert extra[3].name == "rtt_mean" and extra[3].value == "120.46"
        assert extra[4].name == "baseline_rtt" and extra[4].value == "80.12"
        assert extra[5].name == "max_change_ratio" and extra[5].value == "45.8"
        assert extra[6].name == "continuous_fail_hours" and extra[6].value == "3"
        assert extra[7].name == "history_days" and extra[7].value == "7"
        print("✅ send_alarm 参数完全正确")

        mock_log.info.assert_called_once_with("已强制提交基站 10086 异常告警到上报队列")
        mock_log.error.assert_not_called()
        print("✅ 成功日志正确")

    print("【测试1】通过 ✅")


def test_exception():
    """测试异常路径（同步异常）"""
    print("\n" + "=" * 50)
    print("【测试2】异常路径：验证错误捕获和日志")

    mock_result = create_mock_result()

    with patch("app.modules.anomaly.send_alarm", new_callable=AsyncMock) as mock_send_alarm, \
         patch("app.modules.anomaly.asyncio.create_task") as mock_create_task, \
         patch("app.modules.anomaly.log") as mock_log:

        # 模拟在创建任务时就出错（同步异常）
        mock_create_task.side_effect = Exception("创建任务失败")

        _report_anomaly_alarm(mock_result)

        # 验证错误日志被记录
        mock_log.error.assert_called_once()
        error_msg = mock_log.error.call_args[0][0]
        assert "上报基站 10086 异常告警失败" in error_msg
        assert "创建任务失败" in error_msg
        print("✅ 错误日志正确")

        mock_log.info.assert_not_called()
        print("✅ 没有输出成功日志")

    print("【测试2】通过 ✅")


if __name__ == "__main__":
    print("开始测试 _report_anomaly_alarm ...")

    try:
        test_success()
        test_exception()
        print("\n" + "=" * 50)
        print("🎉 全部测试通过！")
    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
    except Exception as e:
        print(f"\n❌ 运行出错: {e}")
        import traceback
        traceback.print_exc()