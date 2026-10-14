import argparse

from .backtest import run_backtest
from .binance_data import MarketDataError, load_binance_klines
from .data import demo_candles, load_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a local paper backtest; no real orders are possible.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--csv", help="OHLC CSV path")
    source.add_argument("--binance", action="store_true",
                        help="download public Binance USDⓈ-M Futures candles (no API key; network required)")
    parser.add_argument("--symbol", default="BTCUSDT", help="symbol for --binance (default: BTCUSDT)")
    parser.add_argument("--interval", default="5m", help="interval for --binance (default: 5m)")
    parser.add_argument("--limit", type=int, default=500, help="completed candle request limit (1-1500)")
    parser.add_argument("--cache-dir", default=".cache/binance", help="directory for downloaded candle cache")
    parser.add_argument("--balance", type=float, default=1000.0, help="starting paper balance")
    parser.add_argument("--candles", type=int, default=300, help="number of synthetic candles for demo")
    args = parser.parse_args()
    if args.balance <= 0:
        parser.error("--balance must be positive")
    try:
        if args.csv:
            candles = load_csv(args.csv)
            source_label = f"CSV: {args.csv}"
        elif args.binance:
            candles = load_binance_klines(args.symbol, args.interval, args.limit, args.cache_dir)
            source_label = f"Binance public USDⓈ-M Futures candles: {args.symbol.upper()} {args.interval}"
        else:
            candles = demo_candles(args.candles)
            source_label = "deterministic synthetic demo candles (not real market data)"
        result = run_backtest(candles, args.balance)
    except (ValueError, OSError, MarketDataError) as exc:
        parser.error(str(exc))
    print("PAPER BACKTEST ONLY — no real orders are placed")
    print(f"Data source: {source_label}")
    print(f"Candles: {len(candles)}")
    print(f"Starting balance: {result.starting_balance:.2f}")
    print(f"Ending balance:   {result.ending_balance:.2f}")
    print(f"Return:            {result.total_return_pct:.2f}%")
    print(f"Trades: {result.trades} | wins: {result.wins} | losses: {result.losses}")
    print(f"Max drawdown:      {result.max_drawdown_pct:.2f}%")


if __name__ == "__main__":
    main()
