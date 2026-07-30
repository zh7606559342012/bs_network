# tests/test_report_anomaly_alarm.py
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import asyncio

# ========== 项目路径处理（和你 log_cleanup 测试保持一致） ==========
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# ========== 导入真实模块（请根据实际路径调整） ==========
# 假设函数在 app.modules.anomaly 中，请改成你真实的路径
from app.modules.anomaly import _report_anomaly_alarm
from app.modules.anomaly import AnomalyResult, AlarmParam, AlarmType   # 如果这些类在其他地方，也一起调整


# ========== Fixture：构造假的 AnomalyResult ==========
@pytest.fixture
def mock_anomaly_result():
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


@pytest.mark.asyncio
async def test_report_anomaly_alarm_success(mock_anomaly_result):
    """正常路径：验证参数构造 + 正确调用 send_alarm + 正确日志"""

    # 注意：patch 路径必须是「函数所在模块」里的名字
    with patch("app.modules.anomaly.send_alarm", new_callable=AsyncMock) as mock_send_alarm, \
         patch("app.modules.anomaly.asyncio.create_task") as mock_create_task, \
         patch("app.modules.anomaly.log") as mock_log:

        # 执行被测函数
        _report_anomaly_alarm(mock_anomaly_result)

        # 1. 验证 create_task 被调用了一次
        mock_create_task.assert_called_once()

        # 2. 拿到 create_task 传入的协程
        coro = mock_create_task.call_args[0][0]

        # 3. 真正 await 这个协程，触发 send_alarm
        await coro

        # 4. 验证 send_alarm 调用参数完全正确
        expected_extra_para = [
            AlarmParam(name="station_id", value="10086"),
            AlarmParam(name="anomaly_score", value="85.7"),
            AlarmParam(name="alert_level", value="critical"),
            AlarmParam(name="rtt_mean", value="120.46"),
            AlarmParam(name="baseline_rtt", value="80.12"),
            AlarmParam(name="max_change_ratio", value="45.8"),
            AlarmParam(name="continuous_fail_hours", value="3"),
            AlarmParam(name="history_days", value="7"),
        ]

        mock_send_alarm.assert_called_once_with(
            alarm_id="50004000",
            alarm_type=AlarmType.GENERATE,
            alarm_identifier="bs location anomaly station:10086",
            extra_para=expected_extra_para,
            send_anyway=True
        )

        # 5. 验证成功日志
        mock_log.info.assert_called_once_with(
            "已强制提交基站 10086 异常告警到上报队列"
        )
        mock_log.error.assert_not_called()


@pytest.mark.asyncio
async def test_report_anomaly_alarm_exception(mock_anomaly_result):
    """异常路径：send_alarm 抛异常时，应捕获并记录 error 日志"""

    with patch("app.modules.anomaly.send_alarm", new_callable=AsyncMock) as mock_send_alarm, \
         patch("app.modules.anomaly.asyncio.create_task") as mock_create_task, \
         patch("app.modules.anomaly.log") as mock_log:

        # 模拟 send_alarm 抛异常
        mock_send_alarm.side_effect = Exception("网络错误")

        _report_anomaly_alarm(mock_anomaly_result)

        # 运行协程触发异常
        coro = mock_create_task.call_args[0][0]
        await coro

        # 验证错误日志
        mock_log.error.assert_called_once()
        error_msg = mock_log.error.call_args[0][0]
        assert "上报基站 10086 异常告警失败" in error_msg
        assert "网络错误" in error_msg

        # 成功日志不应出现
        mock_log.info.assert_not_called()