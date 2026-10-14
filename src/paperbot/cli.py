import argparse
from .backtest import run_backtest
from .data import demo_candles, load_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a local paper-trading backtest (no exchange connection).")
    parser.add_argument("--csv", help="OHLC CSV path; if omitted, use deterministic synthetic demo candles")
    parser.add_argument("--balance", type=float, default=1000.0, help="starting paper balance")
    parser.add_argument("--candles", type=int, default=300, help="number of synthetic candles for demo")
    args = parser.parse_args()
    if args.balance <= 0:
        parser.error("--balance must be positive")
    candles = load_csv(args.csv) if args.csv else demo_candles(args.candles)
    result = run_backtest(candles, args.balance)
    print("PAPER BACKTEST ONLY — no Binance connection and no real orders")
    print(f"Candles: {len(candles)}")
    print(f"Starting balance: {result.starting_balance:.2f}")
    print(f"Ending balance:   {result.ending_balance:.2f}")
    print(f"Return:            {result.total_return_pct:.2f}%")
    print(f"Trades: {result.trades} | wins: {result.wins} | losses: {result.losses}")
    print(f"Max drawdown:      {result.max_drawdown_pct:.2f}%")


if __name__ == "__main__":
    main()
