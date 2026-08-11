# app/modules/anomaly.py
import re
import math
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import pandas as pd
import numpy as np

from app.core.logger import log
from app.core.database import BaseStationCache, CacheMutex
from app.modules.alarm import send_alarm
from app.schemas.alarm import AlarmType, AlarmParam

# ====================== 结果对象（上报/落盘仍用） ======================
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
    log.info("=== 开始执行基站网络异常检测任务（pandas + 小时级同小时对比） ===")

    df = fetch_last_n_days_data(days=7)
    if df is None or df.empty:
        log.warning("未找到任何日志数据，跳过检测")
        return

    results = calculate_anomaly(df)
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
                send_anyway=True,
            )
        )
        log.info(f"已强制提交基站 {r.station_id} 异常告警到上报队列")
    except Exception as e:
        log.error(f"上报基站 {r.station_id} 异常告警失败: {e}")

# ====================== 核心计算 ======================
def calculate_anomaly(df: pd.DataFrame) -> List[AnomalyResult]:
    """
    df 列: station_id, timestamp, rtt, is_ok
    """
    hourly = aggregate_to_hourly(df)
    results = []
    for station_id, g in hourly.groupby("station_id", sort=False):
        res = compute_single_station(int(station_id), g, df)
        results.append(res)
    results.sort(key=lambda x: x.anomaly_score, reverse=True)
    return results

def aggregate_to_hourly(df: pd.DataFrame) -> pd.DataFrame:
    """分钟级 → 小时级聚合；rtt_p95 为真实 95 分位（样本不足时回退 max）"""
    if df.empty:
        return pd.DataFrame(columns=[
            "station_id", "hour", "rtt_mean", "rtt_p95",
            "total_count", "ok_count", "loss_rate"
        ])

    work = df.copy()
    work["hour"] = work["timestamp"].dt.floor("h")

    # 每小时总次数
    total = (
        work.groupby(["station_id", "hour"], as_index=False)
        .size()
        .rename(columns={"size": "total_count"})
    )

    # 成功样本（is_ok 且 rtt > 0）
    ok_mask = work["is_ok"] & (work["rtt"] > 0)
    ok_df = work.loc[ok_mask]

    def _safe_p95(s: pd.Series) -> float:
        """样本 < 5 时用 max，否则用真实 95 分位"""
        n = len(s)
        if n == 0:
            return 0.0
        if n < 5:
            return float(s.max())
        return float(np.percentile(s.to_numpy(dtype=float), 95))

    if ok_df.empty:
        ok_stats = pd.DataFrame(
            columns=["station_id", "hour", "ok_count", "rtt_mean", "rtt_p95"]
        )
    else:
        ok_stats = (
            ok_df.groupby(["station_id", "hour"], as_index=False)
            .agg(
                ok_count=("rtt", "size"),
                rtt_mean=("rtt", "mean"),
                rtt_p95=("rtt", _safe_p95),
            )
        )

    hourly = total.merge(ok_stats, on=["station_id", "hour"], how="left")
    hourly["ok_count"] = hourly["ok_count"].fillna(0).astype(int)
    hourly["rtt_mean"] = hourly["rtt_mean"].fillna(0.0)
    hourly["rtt_p95"] = hourly["rtt_p95"].fillna(0.0)
    hourly["loss_rate"] = 1.0 - (
        hourly["ok_count"] / hourly["total_count"].clip(lower=1)
    )
    hourly = hourly.sort_values(["station_id", "hour"]).reset_index(drop=True)
    return hourly

def compute_single_station(
    station_id: int,
    hourly: pd.DataFrame,
    all_df: pd.DataFrame,
) -> AnomalyResult:
    """
    hourly: 该基站的小时聚合表
    all_df: 原始 ping（用于连续失败检测）
    """
    if hourly.empty:
        return AnomalyResult(station_id=station_id, anomaly_score=0.0, alert_level="无数据")

    hourly = hourly.copy()
    hourly["date"] = hourly["hour"].dt.date
    all_dates = sorted(hourly["date"].unique())
    latest_date = all_dates[-1]

    current = hourly[hourly["date"] == latest_date].copy()
    history = hourly[hourly["date"] < latest_date].copy()
    unique_hist_days = history["date"].nunique()

    def _cur_rtt():
        vals = current.loc[current["ok_count"] > 0, "rtt_mean"]
        return float(vals.mean()) if len(vals) else 0.0

    # 历史不足 3 天
    if unique_hist_days < 3:
        return AnomalyResult(
            station_id=station_id,
            date=latest_date.strftime("%Y-%m-%d"),
            anomaly_score=0.0,
            alert_level="数据不足",
            rtt_mean=round(_cur_rtt(), 2),
            data_points=int(current["total_count"].sum()),
            history_days=int(unique_hist_days),
        )

    # 连续 12 小时不通
    continuous_fail = check_continuous_fail(station_id, all_df, hours=12)
    if continuous_fail >= 12:
        return AnomalyResult(
            station_id=station_id,
            date=latest_date.strftime("%Y-%m-%d"),
            anomaly_score=100.0,
            alert_level="🚨 连续12小时不通",
            rtt_mean=0.0,
            baseline_rtt=0.0,
            max_change_ratio=100.0,
            data_points=int(current["total_count"].sum()),
            history_days=int(unique_hist_days),
            continuous_fail_hours=continuous_fail,
        )

    history["hod"] = history["hour"].dt.hour

    # 历史每小时典型样本数，用于过滤「不完整当前小时」
    hist_count_by_hod = (
        history.groupby("hod")["total_count"].median().to_dict()
        if not history.empty else {}
    )

    AnomalyThreshold = 60.0
    WarningThreshold = 40.0
    ChangeThreshold = 0.30
    weights = {"rtt_mean": 0.30, "rtt_p95": 0.25, "loss_rate": 0.45}

    # RTT 绝对安全区：在此范围内大幅限制相对打分（按你的业务可调）
    RTT_SAFE_MEAN = 30.0   # ms
    RTT_SAFE_P95 = 50.0    # ms

    hour_scores = []

    for _, cur in current.iterrows():
        hod = int(cur["hour"].hour)
        hist = history[history["hod"] == hod]
        if len(hist) < 3:
            continue

        # 1) 跳过样本明显不足的当前小时（避免整天被半截小时拖垮）
        expected = hist_count_by_hod.get(hod, 0)
        if expected > 0 and cur["total_count"] < expected * 0.5:
            continue

        total_score = 0.0
        this_max_change = 0.0
        contribs = {}

        for feat in ("rtt_mean", "rtt_p95", "loss_rate"):
            vals = hist[feat].astype(float).values
            cur_val = float(cur[feat])

            hist_mean = float(np.mean(vals)) if len(vals) else 0.0
            hist_std = float(np.std(vals, ddof=1)) if len(vals) >= 2 else 0.0
            hist_p95 = float(np.percentile(vals, 95)) if len(vals) else 0.0

            if feat == "loss_rate":
                hist_std = max(hist_std, 0.02)
                hist_p95_safe = max(hist_p95, 0.02)
                z = abs(cur_val - hist_mean) / hist_std
                percent_score = max(0.0, (cur_val - hist_p95_safe) / (hist_p95_safe + 1e-6))
                percent_score = min(percent_score, 3.0)
                score = min(100.0, z * 15 + percent_score * 40)
                if cur_val >= 0.20:
                    score = min(100.0, score + 40)
                elif cur_val >= 0.10:
                    score = min(100.0, score + 25)
                elif cur_val >= 0.05:
                    score = min(100.0, score + 10)
            else:
                # RTT：std 下限
                hist_std = max(hist_std, hist_mean * 0.05 + 1e-6)
                change = abs(cur_val - hist_mean) / (hist_mean + 1e-6)
                if feat == "rtt_mean":
                    this_max_change = change

                z = abs(cur_val - hist_mean) / hist_std
                percent_score = max(0.0, (cur_val - hist_p95) / (hist_p95 + 1e-6))
                percent_score = min(percent_score, 5.0)
                score = min(100.0, z * 15 + percent_score * 40)

                # 2) 绝对安全区：相对升高但绝对值仍可接受时封顶
                if feat == "rtt_mean" and cur_val < RTT_SAFE_MEAN:
                    score = min(score, 40.0)
                if feat == "rtt_p95" and cur_val < RTT_SAFE_P95:
                    score = min(score, 40.0)

                # 3) 当前小时 p95 相对 mean 过大 → 多为个别毛刺，降低 p95 权重贡献
                if feat == "rtt_p95":
                    cur_mean = float(cur["rtt_mean"])
                    if cur_mean > 0 and cur_val > cur_mean * 2.5:
                        score *= 0.5  # 离群毛刺降权

            contribs[feat] = round(min(100.0, score), 2)
            total_score += contribs[feat] * weights[feat]

        hour_score = min(100.0, round(total_score, 1))
        hour_scores.append({
            "score": hour_score,
            "max_change": this_max_change,
            "contribs": contribs,
            "hour": cur["hour"],
        })

    if not hour_scores:
        return AnomalyResult(
            station_id=station_id,
            date=latest_date.strftime("%Y-%m-%d"),
            anomaly_score=0.0,
            alert_level="数据不足(小时历史不足)",
            rtt_mean=round(_cur_rtt(), 2),
            data_points=int(current["total_count"].sum()),
            history_days=int(unique_hist_days),
            continuous_fail_hours=continuous_fail,
        )

    scores = [x["score"] for x in hour_scores]
    worst = max(hour_scores, key=lambda x: x["score"])
    # 4) 不全信最差 1 小时：与当天小时分数的 75 分位加权
    p75 = float(np.percentile(scores, 75))
    score = round(0.6 * worst["score"] + 0.4 * p75, 1)
    max_change = worst["max_change"]
    contribs = worst["contribs"]

    current_rtt = _cur_rtt()
    base_vals = history.loc[history["ok_count"] > 0, "rtt_mean"]
    baseline = float(base_vals.mean()) if len(base_vals) else 0.0

    if max_change > ChangeThreshold and score >= AnomalyThreshold:
        alert = "🚨 重大突变"
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
        data_points=int(current["total_count"].sum()),
        history_days=int(unique_hist_days),
        continuous_fail_hours=continuous_fail,
    )

def check_continuous_fail(station_id: int, df: pd.DataFrame, hours: int = 12) -> int:
    """最近 N 小时是否全部失败，返回连续失败小时数"""
    if df is None or df.empty:
        return 0

    cutoff = datetime.now() - timedelta(hours=hours)
    sub = df[(df["station_id"] == station_id) & (df["timestamp"] >= cutoff)]
    if sub.empty:
        return 0

    sub = sub.copy()
    sub["hour"] = sub["timestamp"].dt.floor("h")
    # 每小时是否全失败
    hour_ok = sub.groupby("hour")["is_ok"].any()  # True=该小时至少一次成功
    # 从近到远
    for continuous, (h, any_ok) in enumerate(hour_ok.sort_index(ascending=False).items(), start=0):
        if any_ok:
            return continuous
        # 若一直全失败，循环结束时返回总数
    return len(hour_ok)

# ====================== 落盘 ======================
def save_anomaly_results(results: List[AnomalyResult]):
    log_dir = Path("/var/log/monitor_agent")
    log_dir.mkdir(parents=True, exist_ok=True)
    filename = log_dir / f"anomaly_{datetime.now().strftime('%Y%m%d')}.csv"
    try:
        rows = [{
            "station_id": r.station_id,
            "date": r.date,
            "anomaly_score": r.anomaly_score,
            "alert_level": r.alert_level,
            "rtt_mean": r.rtt_mean,
            "baseline_rtt": r.baseline_rtt,
            "max_change_ratio": r.max_change_ratio,
            "loss_contrib": r.loss_contrib,
            "continuous_fail_hours": r.continuous_fail_hours,
            "history_days": r.history_days,
        } for r in results]
        pd.DataFrame(rows).to_csv(filename, index=False, encoding="utf-8")
        log.info(f"检测结果已保存为 {filename}")
    except Exception as e:
        log.error(f"保存异常结果失败: {e}")

    # 清理 30 天前文件
    try:
        cutoff = datetime.now() - timedelta(days=30)
        for f in log_dir.glob("anomaly_*.csv"):
            try:
                file_date = datetime.strptime(f.stem.split("_")[1], "%Y%m%d")
                if file_date < cutoff:
                    f.unlink()
                    log.info(f"已删除过期文件: {f.name}")
            except Exception:
                continue
    except Exception as e:
        log.warning(f"清理历史 anomaly 文件失败: {e}")

# ====================== 日志解析 ======================
def fetch_last_n_days_data(days: int = 7) -> pd.DataFrame:
    start = datetime.now()
    cutoff = datetime.now() - timedelta(days=days)
    frames = []

    with CacheMutex:
        stations = list(BaseStationCache.items())

    for station_id, bs in stations:
        ip = bs.get("ip", "").replace(".", "")
        log_file = Path("/var/log/monitor_agent") / f"ping_{ip}.log"
        part = parse_log_file(str(log_file), station_id, cutoff)
        if not part.empty:
            frames.append(part)

    if not frames:
        log.info(f"日志解析完成，共读取 0 条记录（最近 {days} 天），耗时 {datetime.now() - start}")
        return pd.DataFrame(columns=["station_id", "timestamp", "rtt", "is_ok"])

    df = pd.concat(frames, ignore_index=True)
    # 压缩内存
    df["station_id"] = df["station_id"].astype("int32")
    df["rtt"] = df["rtt"].astype("float32")
    df["is_ok"] = df["is_ok"].astype("bool")
    log.info(
        f"日志解析完成，共读取 {len(df)} 条记录（最近 {days} 天），耗时 {datetime.now() - start}"
    )
    return df

def parse_log_file(filename: str, station_id: int, cutoff: datetime) -> pd.DataFrame:
    path = Path(filename)
    if not path.exists():
        return pd.DataFrame(columns=["station_id", "timestamp", "rtt", "is_ok"])

    # status 后允许 OK/FAIL；rtt 允许数字或 - (timeout/...)
    pattern = re.compile(
        r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*\|\s*seq=\d+\s*\|\s*(\w+)\s*\|\s*rtt=(.+)$"
    )
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                m = pattern.search(line)
                if not m:
                    continue
                ts_str, status, rtt_raw = m.groups()
                try:
                    ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                if ts < cutoff:
                    continue

                status_u = status.strip().upper()
                is_ok = status_u == "OK"

                # 解析 rtt：数字则取值，否则 0
                rtt_raw = rtt_raw.strip()
                m_rtt = re.match(r"([\d.]+)", rtt_raw)
                if m_rtt and is_ok:
                    rtt = float(m_rtt.group(1))
                else:
                    rtt = 0.0
                    is_ok = False  # 无有效 rtt 一律当失败

                rows.append((station_id, ts, rtt, is_ok))
    except Exception as e:
        log.warning(f"解析日志文件失败 {filename}: {e}")
        return pd.DataFrame(columns=["station_id", "timestamp", "rtt", "is_ok"])

    if not rows:
        return pd.DataFrame(columns=["station_id", "timestamp", "rtt", "is_ok"])
    return pd.DataFrame(rows, columns=["station_id", "timestamp", "rtt", "is_ok"])