from pydantic import BaseModel, Field
from typing import List, Optional
from enum import Enum


class AlarmType(str, Enum):
    GENERATE = "1"      # 产生告警
    RECOVER = "0"       # 恢复告警


class AlarmParam(BaseModel):
    name: str
    value: str


class NagiosAlarm(BaseModel):
    nf_ip: str = ""
    alarm_id: str
    nf_type: str = ""
    alarm_type: str                 # "1" 产生 / "0" 恢复
    alarm_location: str = ""
    alarm_start_time: str = ""
    instance_id: str = ""
    send_anyway: bool = False
    param: List[AlarmParam] = Field(default_factory=list)
    zabbix_event_id: str = ""


class AlarmData(BaseModel):
    alarm_url: str = ""
    alarm_info: NagiosAlarm


class OamAlarm(BaseModel):
    """发送给 OMS 的最终告警结构"""
    _id: str = ""
    _name: str = "om_alarm"
    alarm_id: str = ""
    alarm_identifier: str = ""
    alarm_param: str = ""
    alarm_type: str = ""            # report / clear
    eventTime: int = 0
    hostname: str = ""
    instance_id: str = ""
    mo_id: str = "device"
    oui: str = ""
    sn: str = ""