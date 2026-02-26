#!/usr/bin/env python3
"""Generate a valid 1m OHLCV CSV for btc_1000_et_shock.py.

Default behavior: generate recent ET-day synthetic BTC-like minute bars.
"""

from __future__ import annotations

import argparse
import csv
import math
import random
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

NY_TZ = ZoneInfo("America/New_York")
UTC = timezone.utc


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate 1m OHLCV CSV for shock analyzer")
    p.add_argument("--days", type=int, default=8, help="How many complete ET days to generate")
    p.add_argument("--start-price", type=float, default=62000.0)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--out", default="data/btc_1m_ohlcv.csv")
    return p.parse_args()


def minute_range_for_day(day_et: datetime.date) -> list[datetime]:
    start = datetime.combine(day_et, time(0, 0, 0), tzinfo=NY_TZ)
    return [start + timedelta(minutes=i) for i in range(24 * 60)]


def generate_rows(days: int, start_price: float, seed: int) -> list[list[float]]:
    rng = random.Random(seed)
    now_et = datetime.now(NY_TZ)
    end_day = now_et.date() - timedelta(days=1)
    start_day = end_day - timedelta(days=days - 1)

    price = start_price
    rows: list[list[float]] = []

    d = start_day
    while d <= end_day:
        minutes = minute_range_for_day(d)
        for i, dt_et in enumerate(minutes):
            # smooth intraday pattern + small noise
            cycle = math.sin((i / 1440) * 2 * math.pi)
            drift = 0.00002 * cycle
            shock = rng.uniform(-0.00035, 0.00035)

            open_p = price
            close_p = price * (1 + drift + shock)
            hi = max(open_p, close_p) * (1 + rng.uniform(0.0, 0.00025))
            lo = min(open_p, close_p) * (1 - rng.uniform(0.0, 0.00025))
            vol = 8 + abs(shock) * 15000 + rng.uniform(0, 4)

            # add a stronger 10:00 ET impulse in some days to mimic event behavior
            if dt_et.hour == 10 and dt_et.minute in (0, 1, 2):
                close_p *= 1 + rng.uniform(-0.003, 0.001)
                hi = max(hi, close_p * 1.0002)
                lo = min(lo, close_p * 0.9998)
                vol *= 2.2

            ts_ms = int(dt_et.astimezone(UTC).timestamp() * 1000)
            rows.append([
                ts_ms,
                round(open_p, 2),
                round(hi, 2),
                round(lo, 2),
                round(close_p, 2),
                round(vol, 6),
            ])
            price = close_p
        d += timedelta(days=1)

    return rows


def main() -> None:
    args = parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    rows = generate_rows(args.days, args.start_price, args.seed)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        w.writerows(rows)

    print(f"Generated: {out}")
    print(f"Rows: {len(rows)}")


if __name__ == "__main__":
    main()
