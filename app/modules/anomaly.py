# app/modules/anomaly.py
import re
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict
from collections import defaultdict
from app.core.logger import log
from app.core.database import BaseStationCache, CacheMutex


# ====================== 数据结构 ======================
class HourlyRecord:
    def __init__(self, station_id: int, timestamp: datetime, rtt: float, is_ok: bool):
        self.station_id = station_id
        self.timestamp = timestamp
        self.rtt = rtt
        self.is_ok = is_ok


class DailyRecord:
    def __init__(self, station_id: int, date: datetime, rtt_mean: float, rtt_p95: float,
                 hour_count: int, loss_rate: float = 0.0):
        self.station_id = station_id
        self.date = date
        self.rtt_mean = rtt_mean
        self.rtt_p95 = rtt_p95
        self.hour_count = hour_count
        self.loss_rate = loss_rate  # 0.0 ~ 1.0


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
    """每天凌晨2点执行基站网络异常检测"""
    start = datetime.now()
    log.info("=== 开始执行基站网络异常检测任务（7天/3天窗口 + 丢包率） ===")

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

    log.info(
        f"异常检测完成！共检测 {len(results)} 个基站，发现 {alert_count} 个异常/关注，"
        f"耗时 {datetime.now() - start}"
    )
    save_anomaly_results(results)


# ====================== 核心计算逻辑 ======================
def calculate_anomaly(raw_data: List[HourlyRecord]) -> List[AnomalyResult]:
    daily_map = aggregate_to_daily(raw_data)
    results = []

    for station_id, days in daily_map.items():
        if not days:
            continue
        res = compute_single_station(station_id, days, raw_data)
        results.append(res)

    results.sort(key=lambda x: x.anomaly_score, reverse=True)
    return results


def aggregate_to_daily(records: List[HourlyRecord]) -> Dict[int, List[DailyRecord]]:
    """按天聚合，同时计算丢包率"""
    daily_map = defaultdict(lambda: defaultdict(lambda: {
        "rtt_sum": 0.0, "rtt_max": 0.0, "ok_count": 0, "total_count": 0, "date": None
    }))

    for r in records:
        date_str = r.timestamp.strftime("%Y-%m-%d")
        d = daily_map[r.station_id][date_str]
        d["total_count"] += 1
        if r.is_ok and r.rtt > 0:
            d["ok_count"] += 1
            d["rtt_sum"] += r.rtt
            d["rtt_max"] = max(d["rtt_max"], r.rtt)
        if d["date"] is None:
            d["date"] = r.timestamp.replace(hour=0, minute=0, second=0, microsecond=0)

    result = {}
    for sid, day_dict in daily_map.items():
        lst = []
        for date_str, d in day_dict.items():
            if d["total_count"] == 0:
                continue
            loss_rate = 1.0 - (d["ok_count"] / d["total_count"])
            rtt_mean = d["rtt_sum"] / d["ok_count"] if d["ok_count"] > 0 else 0.0
            lst.append(DailyRecord(
                station_id=sid,
                date=d["date"],
                rtt_mean=rtt_mean,
                rtt_p95=d["rtt_max"],
                hour_count=d["total_count"],
                loss_rate=loss_rate
            ))
        lst.sort(key=lambda x: x.date)
        result[sid] = lst
    return result


def compute_single_station(station_id: int, days: List[DailyRecord],
                           all_records: List[HourlyRecord]) -> AnomalyResult:
    current = days[-1]

    # 优先使用 7 天历史，不足则降级到 3 天
    hist_len = min(7, len(days) - 1)
    if hist_len < 3:
        return AnomalyResult(
            station_id=station_id,
            date=current.date.strftime("%Y-%m-%d"),
            anomaly_score=0.0,
            alert_level="数据不足",
            rtt_mean=round(current.rtt_mean, 2),
            data_points=current.hour_count,
            history_days=hist_len
        )

    history = days[-(hist_len + 1):-1]  # 最近 hist_len 天（排除当天）

    # ========== 连续 12 小时不通检测 ==========
    continuous_fail = check_continuous_fail(station_id, all_records, hours=12)
    if continuous_fail >= 12:
        return AnomalyResult(
            station_id=station_id,
            date=current.date.strftime("%Y-%m-%d"),
            anomaly_score=100.0,
            alert_level="🚨 连续12小时不通",
            rtt_mean=0.0,
            baseline_rtt=0.0,
            max_change_ratio=100.0,
            data_points=current.hour_count,
            history_days=len(history),
            continuous_fail_hours=continuous_fail
        )

    # ========== 评分参数 ==========
    AnomalyThreshold = 70.0
    WarningThreshold = 50.0
    ChangeThreshold = 0.30
    weights = {
        "rtt_mean": 0.30,
        "rtt_p95": 0.25,
        "loss_rate": 0.45          # 丢包率权重最高
    }

    total_score = 0.0
    max_change = 0.0
    contribs = {}

    features = ["rtt_mean", "rtt_p95", "loss_rate"]
    for feat in features:
        vals = []
        for d in history:
            if feat == "rtt_mean":
                vals.append(d.rtt_mean)
            elif feat == "rtt_p95":
                vals.append(d.rtt_p95)
            else:
                vals.append(d.loss_rate)

        hist_mean = mean(vals)
        hist_std = std_dev(vals)
        hist_p95 = percentile_95(vals)

        if feat == "rtt_mean":
            cur_val = current.rtt_mean
        elif feat == "rtt_p95":
            cur_val = current.rtt_p95
        else:
            cur_val = current.loss_rate

        change = abs(cur_val - hist_mean) / (hist_mean + 1e-6)
        if feat == "rtt_mean":
            max_change = change

        z = abs(cur_val - hist_mean) / hist_std if hist_std > 0 else 0.0
        percent_score = max(0, (cur_val - hist_p95) / (hist_p95 + 1e-6))
        score = min(100, z * 15 + percent_score * 40)

        # 丢包率特殊加权：丢包率本身就很高时直接加重
        if feat == "loss_rate" and cur_val > 0.3:
            score = min(100, score + cur_val * 50)

        contribs[feat] = round(score, 2)
        total_score += score * weights[feat]

    score = min(100, round(total_score, 1))

    if continuous_fail >= 12:
        alert = "🚨 连续12小时不通"
        score = 100.0
    elif max_change > ChangeThreshold and score >= WarningThreshold:
        alert = "🚨 重大突变"
    elif score >= AnomalyThreshold:
        alert = "⚠️ 告警"
    elif score >= WarningThreshold:
        alert = "⚠️ 关注"
    else:
        alert = "正常"

    baseline = mean([d.rtt_mean for d in history]) if history else 0.0

    return AnomalyResult(
        station_id=station_id,
        date=current.date.strftime("%Y-%m-%d"),
        anomaly_score=score,
        alert_level=alert,
        rtt_mean=round(current.rtt_mean, 2),
        baseline_rtt=round(baseline, 2),
        max_change_ratio=round(max_change * 100, 1),
        rtt_contrib=contribs.get("rtt_mean", 0),
        rtt_p95_contrib=contribs.get("rtt_p95", 0),
        loss_contrib=contribs.get("loss_rate", 0),
        data_points=current.hour_count,
        history_days=len(history),
        continuous_fail_hours=continuous_fail
    )


def check_continuous_fail(station_id: int, records: List[HourlyRecord], hours: int = 12) -> int:
    """检查最近 N 小时是否全部失败，返回连续失败小时数"""
    cutoff = datetime.now() - timedelta(hours=hours)
    station_records = [r for r in records if r.station_id == station_id and r.timestamp >= cutoff]
    if not station_records:
        return 0

    # 按小时分组
    hour_status = defaultdict(list)
    for r in station_records:
        hour_key = r.timestamp.replace(minute=0, second=0, microsecond=0)
        hour_status[hour_key].append(r.is_ok)

    continuous = 0
    sorted_hours = sorted(hour_status.keys(), reverse=True)
    for h in sorted_hours:
        oks = hour_status[h]
        if all(not ok for ok in oks):  # 该小时全部失败
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
    filename = log_dir / f"anomaly_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"

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
        r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| seq=.* \| (\w+) \| rtt=([\d.]+) ms"
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