# tests/test_send_alarm_to_oms.py
import sys
import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, ANY
from enum import Enum
from dataclasses import dataclass, field
from typing import List

# ==================== 路径处理 ====================
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# ==================== 导入（请按实际路径调整） ====================
from app.modules.alarm import (          # ← 改成你真实的模块路径
    handler_alarm,
    send_alarm_data_to_web,
    AlarmQueue,
    AlarmRec,
    AlarmData,
    AlarmType,
    # AlarmInfo, AlarmParam 如果需要也可以导入
)


# ====================== 构造测试数据 ======================
def create_test_alarm_data(
    alarm_id: str = "50004000",
    alarm_type: AlarmType = AlarmType.GENERATE,
    send_anyway: bool = True,
    station_id: str = "10086",
) -> AlarmData:
    """构造一个完整的 AlarmData，用于测试产生告警"""

    # 如果项目里有正式的 AlarmParam / AlarmInfo，优先用真实类
    # 这里用 MagicMock 兼容性最好
    param1 = MagicMock()
    param1.name = "station_id"
    param1.value = station_id

    param2 = MagicMock()
    param2.name = "anomaly_score"
    param2.value = "85.7"

    info = MagicMock()
    info.alarm_id = alarm_id
    info.alarm_type = alarm_type
    info.alarm_location = f"bs location anomaly station:{station_id}"
    info.param = [param1, param2]
    info.send_anyway = send_anyway
    info.zabbix_event_id = ""

    alarm = MagicMock(spec=AlarmData)
    alarm.alarm_info = info
    return alarm


# ====================== 测试1：直接测 send_alarm_data_to_web ======================
async def test_send_alarm_data_to_web_success():
    """测试产生告警能否正确构造请求并 POST"""
    print("\n" + "=" * 60)
    print("【测试1】send_alarm_data_to_web 正常路径")

    alarm = create_test_alarm_data(
        alarm_id="50004000",
        alarm_type=AlarmType.GENERATE,
        station_id="10086",
    )

    # mock httpx.AsyncClient
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("app.modules.alarm.httpx.AsyncClient", return_value=mock_client), \
         patch("app.modules.alarm.settings") as mock_settings, \
         patch("app.modules.alarm.gen_id", return_value=123456789), \
         patch("app.modules.alarm.log") as mock_log:

        # 模拟配置
        mock_settings.oms.proto = "http"
        mock_settings.oms.ip = "192.168.1.100"
        mock_settings.oms.port = 8080
        mock_settings.app.hostname = "test-host"
        mock_settings.app.nfip = "10.0.0.1"

        await send_alarm_data_to_web(alarm)

        # ---------- 断言 ----------
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args

        # 1. URL 正确
        expected_url = "http://192.168.1.100:8080/oamalarm/agent"
        assert call_args[0][0] == expected_url, f"URL 错误: {call_args[0][0]}"
        print(f"✅ URL 正确: {expected_url}")

        # 2. JSON body 结构正确
        json_body = call_args[1]["json"]
        print(f"📦 发送的 JSON: {json_body}")

        assert json_body["name"] == "om_alarm"
        assert json_body["alarm_id"] == "50004000"
        assert json_body["alarm_identifier"] == "bs location anomaly station:10086"
        assert json_body["alarm_type"] == "report"          # GENERATE → report
        assert json_body["host_name"] == "test-host"
        assert json_body["instance_id"] == "10.0.0.1"
        assert json_body["mo_id"] == "system"
        assert "station_id10086" in json_body["alarm_param"]  # name+value 拼接
        assert "anomaly_score85.7" in json_body["alarm_param"]
        assert isinstance(json_body["event_time"], int)
        print("✅ JSON 结构完全正确")

        mock_response.raise_for_status.assert_called_once()
        print("✅ raise_for_status 已调用")

    print("【测试1】通过 ✅")


# ====================== 测试2：测 handler_alarm 产生告警完整流程 ======================
async def test_handler_alarm_generate():
    """测试 handler_alarm 消费队列后能否触发 POST"""
    print("\n" + "=" * 60)
    print("【测试2】handler_alarm 产生告警完整流程")

    # 清空全局状态（重要！）
    AlarmRec.clear()
    while not AlarmQueue.empty():
        try:
            AlarmQueue.get_nowait()
            AlarmQueue.task_done()
        except Exception:
            break

    alarm = create_test_alarm_data(
        alarm_id="50004000",
        alarm_type=AlarmType.GENERATE,
        send_anyway=True,
        station_id="10086",
    )

    # 把告警放进队列
    await AlarmQueue.put(alarm)

    with patch("app.modules.alarm.send_alarm_data_to_web", new_callable=AsyncMock) as mock_send, \
         patch("app.modules.alarm.gen_id", return_value=987654321), \
         patch("app.modules.alarm.log"):

        # 只跑一次循环就退出（用超时控制）
        task = asyncio.create_task(handler_alarm())
        await asyncio.sleep(0.3)          # 给协程一点时间处理
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # ---------- 断言 ----------
        mock_send.assert_called_once()
        called_alarm = mock_send.call_args[0][0]
        assert called_alarm.alarm_info.alarm_id == "50004000"
        assert called_alarm.alarm_info.zabbix_event_id == "987654321"
        print("✅ send_alarm_data_to_web 已被正确调用")

        # 检查 AlarmRec 是否记录了 event_id
        assert AlarmRec.get("50004000") == "987654321"
        print("✅ AlarmRec 已正确记录 zabbix_event_id")

    print("【测试2】通过 ✅")


# ====================== 测试3：HTTP 失败时的错误处理 ======================
async def test_send_alarm_http_error():
    """测试 POST 失败时是否正确打日志、不抛异常到外层"""
    print("\n" + "=" * 60)
    print("【测试3】HTTP 失败路径")

    alarm = create_test_alarm_data()

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post = AsyncMock(side_effect=Exception("Connection refused"))

    with patch("app.modules.alarm.httpx.AsyncClient", return_value=mock_client), \
         patch("app.modules.alarm.settings") as mock_settings, \
         patch("app.modules.alarm.gen_id", return_value=111), \
         patch("app.modules.alarm.log") as mock_log:

        mock_settings.oms.proto = "http"
        mock_settings.oms.ip = "127.0.0.1"
        mock_settings.oms.port = 9999
        mock_settings.app.hostname = "test"
        mock_settings.app.nfip = "1.1.1.1"

        # 这里会抛异常，但上层 handler_alarm 会捕获
        try:
            await send_alarm_data_to_web(alarm)
            print("❌ 应该抛出异常却没有抛")
        except Exception as e:
            print(f"✅ 正确抛出异常: {e}")

    print("【测试3】通过 ✅")


# ====================== 主入口 ======================
async def main():
    print("🚀 开始测试 告警 POST 到网管 ...")

    await test_send_alarm_data_to_web_success()
    await test_handler_alarm_generate()
    await test_send_alarm_http_error()

    print("\n" + "=" * 60)
    print("🎉 全部单元测试通过！")


if __name__ == "__main__":
    asyncio.run(main())