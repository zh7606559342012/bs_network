# app/modules/anomaly.py
import re
import math
import asyncio
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import List, Dict, Optional
from collections import defaultdict
from app.core.logger import log
from app.core.database import BaseStationCache, CacheMutex
from app.modules.alarm import send_alarm
from app.schemas.alarm import AlarmType, AlarmParam

# ====================== 数据结构 ======================
class HourlyRecord:
    """原始 ping 记录（分钟/秒级）"""
    def __init__(self, station_id: int, timestamp: datetime, rtt: float, is_ok: bool):
        self.station_id = station_id
        self.timestamp = timestamp
        self.rtt = rtt
        self.is_ok = is_ok

class HourlyAggregate:
    """按小时聚合后的数据"""
    def __init__(self, station_id: int, hour: datetime,
                 rtt_mean: float, rtt_p95: float,
                 total_count: int, ok_count: int, loss_rate: float):
        self.station_id = station_id
        self.hour = hour                  # 整点 datetime
        self.rtt_mean = rtt_mean
        self.rtt_p95 = rtt_p95            # 本小时最大 RTT（兼容原逻辑）
        self.total_count = total_count
        self.ok_count = ok_count
        self.loss_rate = loss_rate        # 0.0 ~ 1.0

class AnomalyResult:
    def __init__(self, **kwargs):
        self.station_id = kwargs.get("station_id")
        self.date = kwargs.get("date")
        self.anomaly_score = kwargs.get("anomaly_score", 0.0)
        self.alert_level = kwargs.get("alert_level", "正常")
        self.rtt_mean = kwargs.get("rtt_mean", 0.0)
        self.baseline_rtt = kwargs.get("baseline_rtt", 0.0)
        self.max_change_ratio = kwargs.get("max_change_ratio", 0.0)
        self.rtt_contrib = kwargs.get("rtt_contrib", 0.0)
        self.rtt_p95_contrib = kwargs.get("rtt_p95_contrib", 0.0)
        self.loss_contrib = kwargs.get("loss_contrib", 0.0)
        self.data_points = kwargs.get("data_points", 0)
        self.history_days = kwargs.get("history_days", 0)
        self.continuous_fail_hours = kwargs.get("continuous_fail_hours", 0)

# ====================== 主入口 ======================
def detect_network_anomaly():
    """每天凌晨2点执行基站网络异常检测（小时级同小时对比）"""
    start = datetime.now()
    log.info("=== 开始执行基站网络异常检测任务（小时级同小时历史均值/标准差 + 丢包率） ===")
    raw_data = fetch_last_n_days_data(days=7)
    if not raw_data:
        log.warning("未找到任何日志数据，跳过检测")
        return
    results = calculate_anomaly(raw_data)
    alert_count = 0
    for r in results:
        if r.anomaly_score >= 50 or r.continuous_fail_hours >= 12:
            alert_count += 1
            log.warning(
                f"【异常】基站 {r.station_id} | 分数 {r.anomaly_score:.1f} | {r.alert_level} | "
                f"当前RTT {r.rtt_mean:.2f}ms | 基线 {r.baseline_rtt:.2f}ms | "
                f"突变 {r.max_change_ratio:.1f}% | 连续失败 {r.continuous_fail_hours} 小时"
            )
            _report_anomaly_alarm(r)
    log.info(
        f"异常检测完成！共检测 {len(results)} 个基站，发现 {alert_count} 个异常/关注，"
        f"耗时 {datetime.now() - start}"
    )
    save_anomaly_results(results)

def _report_anomaly_alarm(r: AnomalyResult):
    """把单个基站的异常结果上报给网管"""
    try:
        extra_para = [
            AlarmParam(name="station_id", value=str(r.station_id)),
            AlarmParam(name="anomaly_score", value=f"{r.anomaly_score:.1f}"),
            AlarmParam(name="alert_level", value=r.alert_level),
            AlarmParam(name="rtt_mean", value=f"{r.rtt_mean:.2f}"),
            AlarmParam(name="baseline_rtt", value=f"{r.baseline_rtt:.2f}"),
            AlarmParam(name="max_change_ratio", value=f"{r.max_change_ratio:.1f}"),
            AlarmParam(name="continuous_fail_hours", value=str(r.continuous_fail_hours)),
            AlarmParam(name="history_days", value=str(r.history_days)),
        ]
        alarm_id = "50004000"
        alarm_identifier = f"bs location anomaly station:{r.station_id}"
        asyncio.create_task(
            send_alarm(
                alarm_id=alarm_id,
                alarm_type=AlarmType.GENERATE,
                alarm_identifier=alarm_identifier,
                extra_para=extra_para,
                send_anyway=True
            )
        )
        log.info(f"已强制提交基站 {r.station_id} 异常告警到上报队列")
    except Exception as e:
        log.error(f"上报基站 {r.station_id} 异常告警失败: {e}")

# ====================== 核心计算逻辑 ======================
def calculate_anomaly(raw_data: List[HourlyRecord]) -> List[AnomalyResult]:
    hourly_map = aggregate_to_hourly(raw_data)
    results = []
    for station_id, hours in hourly_map.items():
        if not hours:
            continue
        res = compute_single_station(station_id, hours, raw_data)
        results.append(res)
    results.sort(key=lambda x: x.anomaly_score, reverse=True)
    return results

def aggregate_to_hourly(records: List[HourlyRecord]) -> Dict[int, List[HourlyAggregate]]:
    """把分钟/秒级 ping 记录聚合到小时级"""
    hourly_map = defaultdict(lambda: defaultdict(lambda: {
        "rtt_sum": 0.0,
        "rtt_max": 0.0,
        "ok_count": 0,
        "total_count": 0,
        "hour": None
    }))

    for r in records:
        hour_key = r.timestamp.replace(minute=0, second=0, microsecond=0)
        d = hourly_map[r.station_id][hour_key]
        d["total_count"] += 1
        if r.is_ok and r.rtt > 0:
            d["ok_count"] += 1
            d["rtt_sum"] += r.rtt
            d["rtt_max"] = max(d["rtt_max"], r.rtt)
        if d["hour"] is None:
            d["hour"] = hour_key

    result = {}
    for sid, hour_dict in hourly_map.items():
        lst = []
        for _, d in hour_dict.items():
            if d["total_count"] == 0:
                continue
            loss_rate = 1.0 - (d["ok_count"] / d["total_count"])
            rtt_mean = d["rtt_sum"] / d["ok_count"] if d["ok_count"] > 0 else 0.0
            lst.append(HourlyAggregate(
                station_id=sid,
                hour=d["hour"],
                rtt_mean=rtt_mean,
                rtt_p95=d["rtt_max"],
                total_count=d["total_count"],
                ok_count=d["ok_count"],
                loss_rate=loss_rate
            ))
        lst.sort(key=lambda x: x.hour)
        result[sid] = lst
    return result

def compute_single_station(station_id: int, hours: List[HourlyAggregate],
                           all_records: List[HourlyRecord]) -> AnomalyResult:
    if not hours:
        return AnomalyResult(station_id=station_id, anomaly_score=0.0, alert_level="无数据")

    # 按日期拆分：最新一天 vs 历史
    all_dates = sorted({h.hour.date() for h in hours})
    latest_date = all_dates[-1]
    current_hours = [h for h in hours if h.hour.date() == latest_date]
    history_hours = [h for h in hours if h.hour.date() < latest_date]
    unique_hist_days = len({h.hour.date() for h in history_hours})

    # 数据不足（历史不足 3 天）
    if unique_hist_days < 3:
        cur_rtt = mean([h.rtt_mean for h in current_hours if h.ok_count > 0]) if current_hours else 0.0
        return AnomalyResult(
            station_id=station_id,
            date=latest_date.strftime("%Y-%m-%d"),
            anomaly_score=0.0,
            alert_level="数据不足",
            rtt_mean=round(cur_rtt, 2),
            data_points=sum(h.total_count for h in current_hours),
            history_days=unique_hist_days
        )

    # ========== 连续 12 小时不通检测（优先级最高） ==========
    continuous_fail = check_continuous_fail(station_id, all_records, hours=12)
    if continuous_fail >= 12:
        return AnomalyResult(
            station_id=station_id,
            date=latest_date.strftime("%Y-%m-%d"),
            anomaly_score=100.0,
            alert_level="🚨 连续12小时不通",
            rtt_mean=0.0,
            baseline_rtt=0.0,
            max_change_ratio=100.0,
            data_points=sum(h.total_count for h in current_hours),
            history_days=unique_hist_days,
            continuous_fail_hours=continuous_fail
        )

    # ========== 按小时（0-23）分组历史数据 ==========
    hist_by_hod: Dict[int, List[HourlyAggregate]] = defaultdict(list)
    for h in history_hours:
        hist_by_hod[h.hour.hour].append(h)

    # ========== 评分参数（保持原权重） ==========
    AnomalyThreshold = 70.0
    WarningThreshold = 50.0
    ChangeThreshold = 0.30
    weights = {
        "rtt_mean": 0.30,
        "rtt_p95": 0.25,
        "loss_rate": 0.45
    }

    hour_scores = []  # 每个可对比小时的评分结果

    for cur in current_hours:
        hod = cur.hour.hour
        hist = hist_by_hod.get(hod, [])
        if len(hist) < 3:  # 该小时历史样本不足 3 天，跳过
            continue

        total_score = 0.0
        this_max_change = 0.0
        contribs = {}

        for feat in ["rtt_mean", "rtt_p95", "loss_rate"]:
            if feat == "rtt_mean":
                vals = [h.rtt_mean for h in hist]
                cur_val = cur.rtt_mean
            elif feat == "rtt_p95":
                vals = [h.rtt_p95 for h in hist]
                cur_val = cur.rtt_p95
            else:
                vals = [h.loss_rate for h in hist]
                cur_val = cur.loss_rate

            hist_mean = mean(vals)
            hist_std = std_dev(vals)
            hist_p95 = percentile_95(vals)

            change = abs(cur_val - hist_mean) / (hist_mean + 1e-6)
            if feat == "rtt_mean":
                this_max_change = change

            z = abs(cur_val - hist_mean) / hist_std if hist_std > 0 else 0.0
            percent_score = max(0.0, (cur_val - hist_p95) / (hist_p95 + 1e-6))
            score = min(100.0, z * 15 + percent_score * 40)

            # 丢包率特殊加权
            if feat == "loss_rate" and cur_val > 0.3:
                score = min(100.0, score + cur_val * 50)

            contribs[feat] = round(score, 2)
            total_score += score * weights[feat]

        hour_score = min(100.0, round(total_score, 1))
        hour_scores.append({
            "score": hour_score,
            "max_change": this_max_change,
            "contribs": contribs,
            "hour": cur
        })

    # 没有任何小时有足够历史样本
    if not hour_scores:
        cur_rtt = mean([h.rtt_mean for h in current_hours if h.ok_count > 0]) if current_hours else 0.0
        return AnomalyResult(
            station_id=station_id,
            date=latest_date.strftime("%Y-%m-%d"),
            anomaly_score=0.0,
            alert_level="数据不足(小时历史不足)",
            rtt_mean=round(cur_rtt, 2),
            data_points=sum(h.total_count for h in current_hours),
            history_days=unique_hist_days,
            continuous_fail_hours=continuous_fail
        )

    # 取当天最差小时的分数作为最终分数（更能捕捉单小时异常）
    worst = max(hour_scores, key=lambda x: x["score"])
    score = worst["score"]
    max_change = worst["max_change"]
    contribs = worst["contribs"]

    # 当前天整体 RTT 均值 & 历史整体基线
    current_rtt_vals = [h.rtt_mean for h in current_hours if h.ok_count > 0]
    current_rtt = mean(current_rtt_vals) if current_rtt_vals else 0.0
    baseline_vals = [h.rtt_mean for h in history_hours if h.ok_count > 0]
    baseline = mean(baseline_vals) if baseline_vals else 0.0

    # 告警级别
    if max_change > ChangeThreshold and score >= WarningThreshold:
        alert = "🚨 重大突变"
    elif score >= AnomalyThreshold:
        alert = "⚠️ 告警"
    elif score >= WarningThreshold:
        alert = "⚠️ 关注"
    else:
        alert = "正常"

    return AnomalyResult(
        station_id=station_id,
        date=latest_date.strftime("%Y-%m-%d"),
        anomaly_score=score,
        alert_level=alert,
        rtt_mean=round(current_rtt, 2),
        baseline_rtt=round(baseline, 2),
        max_change_ratio=round(max_change * 100, 1),
        rtt_contrib=contribs.get("rtt_mean", 0),
        rtt_p95_contrib=contribs.get("rtt_p95", 0),
        loss_contrib=contribs.get("loss_rate", 0),
        data_points=sum(h.total_count for h in current_hours),
        history_days=unique_hist_days,
        continuous_fail_hours=continuous_fail
    )

def check_continuous_fail(station_id: int, records: List[HourlyRecord], hours: int = 12) -> int:
    """检查最近 N 小时是否全部失败，返回连续失败小时数"""
    cutoff = datetime.now() - timedelta(hours=hours)
    station_records = [r for r in records if r.station_id == station_id and r.timestamp >= cutoff]
    if not station_records:
        return 0

    hour_status = defaultdict(list)
    for r in station_records:
        hour_key = r.timestamp.replace(minute=0, second=0, microsecond=0)
        hour_status[hour_key].append(r.is_ok)

    continuous = 0
    sorted_hours = sorted(hour_status.keys(), reverse=True)
    for h in sorted_hours:
        oks = hour_status[h]
        if all(not ok for ok in oks):
            continuous += 1
        else:
            break
    return continuous

# ====================== 辅助函数 ======================
def mean(vals: List[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0

def std_dev(vals: List[float]) -> float:
    if len(vals) < 2:
        return 0.0
    m = mean(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1))

def percentile_95(vals: List[float]) -> float:
    if not vals:
        return 0.0
    sorted_vals = sorted(vals)
    idx = int(0.95 * (len(sorted_vals) - 1))
    return sorted_vals[idx]

def save_anomaly_results(results: List[AnomalyResult]):
    log_dir = Path("/var/log/monitor_agent")
    log_dir.mkdir(parents=True, exist_ok=True)
    filename = log_dir / f"anomaly_{datetime.now().strftime('%Y%m%d')}.csv"
    try:
        with open(filename, "w", encoding="utf-8") as f:
            f.write("station_id,date,anomaly_score,alert_level,rtt_mean,baseline_rtt,"
                    "max_change_ratio,loss_contrib,continuous_fail_hours,history_days\n")
            for r in results:
                f.write(
                    f"{r.station_id},{r.date},{r.anomaly_score},{r.alert_level},"
                    f"{r.rtt_mean},{r.baseline_rtt},{r.max_change_ratio},"
                    f"{r.loss_contrib},{r.continuous_fail_hours},{r.history_days}\n"
                )
        log.info(f"检测结果已保存为 {filename}")
    except Exception as e:
        log.error(f"保存异常结果失败: {e}")

    # 清理过期文件（只保留最近 30 天）
    try:
        cutoff = datetime.now() - timedelta(days=30)
        for f in log_dir.glob("anomaly_*.csv"):
            try:
                date_str = f.stem.split("_")[1]
                file_date = datetime.strptime(date_str, "%Y%m%d")
                if file_date < cutoff:
                    f.unlink()
                    log.info(f"已删除过期文件: {f.name}")
            except Exception:
                continue
    except Exception as e:
        log.warning(f"清理历史 anomaly 文件失败: {e}")

# ====================== 日志解析 ======================
def fetch_last_n_days_data(days: int = 7) -> List[HourlyRecord]:
    start = datetime.now()
    records = []
    cutoff = datetime.now() - timedelta(days=days)
    with CacheMutex:
        stations = list(BaseStationCache.items())
    for station_id, bs in stations:
        ip = bs.get("ip", "").replace(".", "")
        log_file = Path("/var/log/monitor_agent") / f"ping_{ip}.log"
        file_records = parse_log_file(str(log_file), station_id, cutoff)
        records.extend(file_records)
    log.info(f"日志解析完成，共读取 {len(records)} 条记录（最近 {days} 天），耗时 {datetime.now() - start}")
    return records

def parse_log_file(filename: str, station_id: int, cutoff: datetime) -> List[HourlyRecord]:
    records = []
    path = Path(filename)
    if not path.exists():
        return records
    pattern = re.compile(
        r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*\|\s*seq=\d+\s*\|\s*(\w+)\s*\|\s*rtt=([\d.]+)\s*ms"
    )
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                m = pattern.search(line)
                if not m:
                    continue
                ts_str, status, rtt_str = m.groups()
                try:
                    ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                if ts < cutoff:
                    continue
                rtt = float(rtt_str)
                records.append(HourlyRecord(
                    station_id=station_id,
                    timestamp=ts,
                    rtt=rtt,
                    is_ok=(status.upper() == "OK")
                ))
    except Exception as e:
        log.warning(f"解析日志文件失败 {filename}: {e}")
    return records