import asyncio
import time
import httpx
from typing import Dict, List
from datetime import datetime

from app.core.logger import log
from app.core.config import settings
from app.schemas.alarm import (
    AlarmData, NagiosAlarm, AlarmParam, OamAlarm, AlarmType
)
from app.utils.helpers import gen_id


# ====================== 全局状态 ======================
AlarmQueue: asyncio.Queue = asyncio.Queue(maxsize=30)

AlarmRec: Dict[str, str] = {}
AgwAlarmIdCacheMap: Dict[str, bool] = {}

AgwAlarmIdMap: Dict[str, str] = {
    "bs detect anomaly abnormal": "50004000",
}

AlarmIdentifierMap: Dict[str, str] = {}


def init_alarm_maps():
    AlarmIdentifierMap.clear()
    for original_name, alarm_id in AgwAlarmIdMap.items():
        AlarmIdentifierMap[alarm_id] = original_name
    log.info(f"Alarm maps initialized, total {len(AgwAlarmIdMap)} mappings")


# ====================== 后台消费协程 ======================
async def handler_alarm():
    log.info("Alarm handler started")
    while True:
        try:
            alarm: AlarmData = await AlarmQueue.get()
            info = alarm.alarm_info

            # 产生告警
            current = AlarmRec.get(info.alarm_id)
            if info.alarm_type == AlarmType.GENERATE and (not current or info.send_anyway):
                info.zabbix_event_id = str(gen_id())
                try:
                    await send_alarm_data_to_web(alarm)
                    AlarmRec[info.alarm_id] = info.zabbix_event_id
                except Exception as e:
                    log.error(f"SendAlarmDataToWeb failed: {e}")

            # 恢复告警
            elif info.alarm_type == AlarmType.RECOVER and (AlarmRec.get(info.alarm_id) or info.send_anyway):
                info.zabbix_event_id = AlarmRec.get(info.alarm_id, "")
                try:
                    await send_alarm_data_to_web(alarm)
                    AlarmRec[info.alarm_id] = ""
                except Exception as e:
                    log.error(f"SendAlarmDataToWeb failed: {e}")

            AlarmQueue.task_done()
        except Exception as e:
            log.exception(f"handler_alarm error: {e}")
            await asyncio.sleep(1)


# ====================== 发送到 OMS ======================
async def send_alarm_data_to_web(data: AlarmData) -> None:
    log.debug("SendAlarmDataToWeb Entry")
    info = data.alarm_info

    oam = OamAlarm(
        _id=str(gen_id()),
        _name="om_alarm",
        alarm_id=info.alarm_id,
        alarm_identifier=info.alarm_location,
        alarm_param=", ".join([f"{p.name}{p.value}" for p in info.param]),
        alarm_type="report" if info.alarm_type == AlarmType.GENERATE else "clear",
        eventTime=int(time.time() * 1000),
        hostname=settings.app.hostname,
        instance_id=info.instance_id,
        mo_id="system",
    )

    url = f"{settings.oms.oms_proto}://{settings.oms.oms_ip}:{settings.oms.oms_port}/oamalarm/agent"
    log.debug(f"send url: {url}, data: {oam.model_dump()}")

    async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
        resp = await client.post(url, json=oam.model_dump())
        resp.raise_for_status()

# ====================== 对外暴露的发送接口 ======================
async def send_alarm(
    alarm_id: str,
    alarm_type: str,
    alarm_identifier: str,
    instance_id: str,
    extra_para: List[AlarmParam] = None,
    send_anyway: bool = False
):
    if extra_para is None:
        extra_para = []

    data = AlarmData(
        alarm_info=NagiosAlarm(
            nf_ip=settings.app.nfip,
            alarm_id=alarm_id,
            nf_type=alarm_identifier,
            alarm_type=alarm_type,
            alarm_location=alarm_identifier,
            instance_id = instance_id,
            send_anyway=send_anyway,
            alarm_start_time=datetime.now().strftime("%Y.%m.%d %H:%M:%S"),
            param=extra_para,
        )
    )

    try:
        await asyncio.wait_for(AlarmQueue.put(data), timeout=5.0)
    except asyncio.TimeoutError:
        log.error(f"SendAlarm: Timeout putting data to AlarmQueue, data={data}")


async def send_alarm_anyway(
    alarm_id: str,
    alarm_type: str,
    alarm_identifier: str,
    extra_para: List[AlarmParam] = None
):
    await send_alarm(alarm_id, alarm_type, alarm_identifier, extra_para, send_anyway=True)