import csv
import math
from pathlib import Path
from .models import Candle


def load_csv(path: str | Path) -> list[Candle]:
    """Load CSV columns timestamp,open,high,low,close; volume is optional."""
    candles = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        required = {"timestamp", "open", "high", "low", "close"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError(f"CSV must contain columns: {', '.join(sorted(required))}")
        for row in reader:
            candles.append(Candle(int(float(row["timestamp"])), float(row["open"]),
                                  float(row["high"]), float(row["low"]), float(row["close"]),
                                  float(row.get("volume") or 0)))
    return candles


def demo_candles(count: int = 300) -> list[Candle]:
    """Deterministic synthetic data for smoke tests; not real market data."""
    if count < 2:
        raise ValueError("count must be at least 2")
    values = []
    for i in range(count):
        close = 100 + 0.035 * i + 5 * math.sin(i / 11) + 1.5 * math.sin(i / 3.7)
        values.append(close)
    candles = []
    for i, close in enumerate(values):
        open_ = values[i - 1] if i else close - 0.2
        high = max(open_, close) + 0.05
        low = min(open_, close) - 0.05
        candles.append(Candle(i, open_, high, low, close))
    return candles
