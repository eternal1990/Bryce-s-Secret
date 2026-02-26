#!/usr/bin/env python3
"""Analyze BTCUSDT 10:00 ET shock pattern for latest N complete ET days."""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Dict, List
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo

BINANCE_KLINE_URL = "https://api.binance.com/api/v3/klines"
NY_TZ = ZoneInfo("America/New_York")
UTC = timezone.utc


@dataclass
class Config:
    symbol: str = "BTCUSDT"
    days: int = 7
    max_missing_60m: int = 3
    dump_5m_threshold: float = -0.01
    dump_15m_threshold: float = -0.015
    fakeout_ru_60m_threshold: float = 0.01


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--max-missing-60m", type=int, default=3)
    p.add_argument("--out-dir", default="output")
    p.add_argument(
        "--input-csv",
        default="",
        help="Optional local 1m OHLCV CSV path (for MCP/exported data). If set, skip Binance fetch.",
    )
    return p.parse_args()


def dt_et(d: date, hhmmss: str) -> datetime:
    hh, mm, ss = [int(x) for x in hhmmss.split(":")]
    return datetime.combine(d, time(hh, mm, ss), tzinfo=NY_TZ)


def to_ms(dt_obj: datetime) -> int:
    return int(dt_obj.astimezone(UTC).timestamp() * 1000)


def _mk_row(open_ms: int, op: float, hi: float, lo: float, cl: float, vol: float) -> dict:
    open_utc = datetime.fromtimestamp(open_ms / 1000, tz=UTC)
    return {
        "open_ms": open_ms,
        "open_utc": open_utc,
        "open_et": open_utc.astimezone(NY_TZ),
        "open": op,
        "high": hi,
        "low": lo,
        "close": cl,
        "volume": vol,
    }


def fetch_1m_klines(symbol: str, start_ms: int, end_ms: int) -> List[dict]:
    rows = []
    cursor = start_ms
    while cursor <= end_ms:
        params = urlencode(
            {
                "symbol": symbol,
                "interval": "1m",
                "startTime": cursor,
                "endTime": end_ms,
                "limit": 1000,
            }
        )
        with urlopen(f"{BINANCE_KLINE_URL}?{params}", timeout=20) as resp:
            batch = json.loads(resp.read().decode("utf-8"))
        if not batch:
            break
        for r in batch:
            open_ms = int(r[0])
            rows.append(
                _mk_row(open_ms, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
            )
        cursor = int(batch[-1][0]) + 60_000
        if len(batch) < 1000:
            break
    rows.sort(key=lambda x: x["open_ms"])
    dedup = {r["open_ms"]: r for r in rows}
    return [dedup[k] for k in sorted(dedup)]


def load_ohlcv_csv(path: Path) -> List[dict]:
    """Load 1m OHLCV from CSV exported by MCP/API workflows.

    Required columns: timestamp,open,high,low,close,volume
    - timestamp supports unix ms, unix s, or ISO8601 (UTC preferred)
    """
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"timestamp", "open", "high", "low", "close", "volume"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise SystemExit(f"input-csv missing required columns: {sorted(required)}")
        for row in reader:
            ts = (row.get("timestamp") or "").strip()
            if not ts:
                continue
            if ts.isdigit():
                v = int(ts)
                open_ms = v if v > 10_000_000_000 else v * 1000
            else:
                t = ts.replace("Z", "+00:00")
                open_ms = int(datetime.fromisoformat(t).astimezone(UTC).timestamp() * 1000)
            rows.append(
                _mk_row(
                    open_ms,
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                    float(row["volume"]),
                )
            )
    rows.sort(key=lambda x: x["open_ms"])
    dedup = {r["open_ms"]: r for r in rows}
    return [dedup[k] for k in sorted(dedup)]


def day_rows(rows: List[dict], d: date) -> List[dict]:
    return [r for r in rows if r["open_et"].date() == d]


def missing_minutes_count(rows_for_day: List[dict], start: datetime, minutes: int) -> int:
    actual = {r["open_et"] for r in rows_for_day}
    miss = 0
    for i in range(minutes):
        if start + timedelta(minutes=i) not in actual:
            miss += 1
    return miss


def quantile(vals: List[float], q: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    pos = (len(s) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    frac = pos - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def metrics_from_window(segment: List[dict], open_t0: float) -> Dict[str, float | None]:
    if not segment:
        return {"ret": None, "dd": None, "ru": None, "vol": None, "time_to_low": None, "rebound": None}
    close_end = segment[-1]["close"]
    lows = [x["low"] for x in segment]
    highs = [x["high"] for x in segment]
    low_min = min(lows)
    high_max = max(highs)
    low_idx = lows.index(low_min) + 1
    rebound = (close_end / low_min - 1) if low_min > 0 else None
    return {
        "ret": close_end / open_t0 - 1,
        "dd": low_min / open_t0 - 1,
        "ru": high_max / open_t0 - 1,
        "vol": sum(x["volume"] for x in segment),
        "time_to_low": low_idx,
        "rebound": rebound,
    }


def analyze(rows: List[dict], cfg: Config, start_day: date, end_day: date) -> tuple[list[dict], dict]:
    windows = [5, 15, 30, 60]
    daily = []

    d = start_day
    while d <= end_day:
        rday = sorted(day_rows(rows, d), key=lambda x: x["open_et"])
        if not rday:
            d += timedelta(days=1)
            continue

        t0_target = dt_et(d, "10:00:00")
        t0_candidates = [r for r in rday if r["open_et"] >= t0_target]
        if not t0_candidates:
            d += timedelta(days=1)
            continue

        t0 = t0_candidates[0]
        aligned = t0["open_et"] == t0_target
        t0_idx = rday.index(t0)

        rec = {
            "date_et": d.isoformat(),
            "open_10_00": t0["open"],
            "t0_used": t0["open_et"].isoformat(),
            "aligned": aligned,
            "missing_minutes_day": missing_minutes_count(rday, dt_et(d, "00:00:00"), 24 * 60),
        }

        miss_60m = missing_minutes_count(rday, t0["open_et"], 60)
        rec["missing_minutes_60m"] = miss_60m
        rec["invalid_day"] = miss_60m > cfg.max_missing_60m

        open_t0 = t0["open"]
        for m in windows:
            seg = rday[t0_idx : t0_idx + m]
            met = metrics_from_window(seg if len(seg) == m else [], open_t0)
            for k, v in met.items():
                rec[f"{k}_{m}m"] = v

        base_start = dt_et(d, "09:00:00")
        baseline_rows = [r for r in rday if base_start <= r["open_et"] < t0_target]
        vol_baseline = sum(r["volume"] for r in baseline_rows)
        rec["vol_baseline_9_10"] = vol_baseline
        rec["vol_ratio_60m_vs_9_10"] = (
            (rec["vol_60m"] / vol_baseline) if vol_baseline and rec["vol_60m"] is not None else None
        )

        dd5 = rec.get("dd_5m")
        dd15 = rec.get("dd_15m")
        ret60 = rec.get("ret_60m")
        ru60 = rec.get("ru_60m")
        rec["dump_5m"] = dd5 is not None and dd5 <= cfg.dump_5m_threshold
        rec["dump_15m"] = dd15 is not None and dd15 <= cfg.dump_15m_threshold
        rec["dump_rebound"] = (
            dd5 is not None and ret60 is not None and dd5 <= cfg.dump_5m_threshold and ret60 >= 0
        )
        rec["fakeout"] = (
            dd5 is not None
            and ru60 is not None
            and dd5 <= cfg.dump_5m_threshold
            and ru60 >= cfg.fakeout_ru_60m_threshold
        )

        daily.append(rec)
        d += timedelta(days=1)

    valid = [x for x in daily if not x["invalid_day"]]
    summary: dict = {
        "n_days_total": len(daily),
        "n_days_valid": len(valid),
        "invalid_days": [x["date_et"] for x in daily if x["invalid_day"]],
    }

    for m in windows:
        for metric in ("ret", "dd", "ru"):
            col = f"{metric}_{m}m"
            vals = [x[col] for x in valid if x[col] is not None]
            summary[f"{col}_mean"] = mean(vals) if vals else None
            summary[f"{col}_median"] = median(vals) if vals else None
            summary[f"{col}_p25"] = quantile(vals, 0.25)
            summary[f"{col}_p75"] = quantile(vals, 0.75)

    if valid:
        summary["dump_rebound_count"] = sum(1 for x in valid if x["dump_rebound"])
        summary["dump_rebound_ratio"] = summary["dump_rebound_count"] / len(valid)
        summary["fakeout_count"] = sum(1 for x in valid if x["fakeout"])
        summary["time_to_low_5m_distribution"] = {}
        summary["time_to_low_15m_distribution"] = {}
        for x in valid:
            t5 = x.get("time_to_low_5m")
            t15 = x.get("time_to_low_15m")
            if t5 is not None:
                key = str(t5)
                summary["time_to_low_5m_distribution"][key] = summary["time_to_low_5m_distribution"].get(key, 0) + 1
            if t15 is not None:
                key = str(t15)
                summary["time_to_low_15m_distribution"][key] = summary["time_to_low_15m_distribution"].get(key, 0) + 1
        ratios = [x["vol_ratio_60m_vs_9_10"] for x in valid if x["vol_ratio_60m_vs_9_10"] is not None]
        summary["vol_ratio_60m_vs_9_10_mean"] = mean(ratios) if ratios else None
        summary["vol_ratio_60m_vs_9_10_median"] = median(ratios) if ratios else None

    return daily, summary


def write_daily_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    headers = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    cfg = Config(symbol=args.symbol, days=args.days, max_missing_60m=args.max_missing_60m)

    now_et = datetime.now(NY_TZ)
    end_day = now_et.date() - timedelta(days=1)
    start_day = end_day - timedelta(days=cfg.days - 1)

    if args.input_csv:
        rows = load_ohlcv_csv(Path(args.input_csv))
    else:
        fetch_start = dt_et(start_day, "00:00:00")
        fetch_end = dt_et(now_et.date(), "23:59:59")
        try:
            rows = fetch_1m_klines(cfg.symbol, to_ms(fetch_start), to_ms(fetch_end))
        except URLError as e:
            raise SystemExit(
                f"Failed to fetch Binance klines: {e}. "
                "Please run in a network environment that can access api.binance.com "
                "or provide --input-csv generated from your MCP/API workflow."
            )

    daily, summary = analyze(rows, cfg, start_day, end_day)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    daily_path = out_dir / f"{cfg.symbol.lower()}_10am_et_daily.csv"
    summary_path = out_dir / f"{cfg.symbol.lower()}_10am_et_summary.json"
    write_daily_csv(daily_path, daily)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Saved daily details: {daily_path}")
    print(f"Saved summary stats: {summary_path}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
