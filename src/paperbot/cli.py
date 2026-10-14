import argparse

from .backtest import run_backtest
from .binance_data import MarketDataError, load_binance_klines
from .data import demo_candles, load_csv
from .news import NewsError, fetch_latest_news, print_news, watch_news
from .public_market_data import load_okx_funding_rates, load_okx_swap_klines
from .strategy import BreakRetestStrategy, EmaCrossStrategy


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a local paper backtest; no real orders are possible.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--csv", help="OHLC CSV path")
    source.add_argument("--binance", action="store_true",
                        help="download public Binance USDⓈ-M Futures candles (no API key; network required)")
    source.add_argument("--okx", action="store_true",
                        help="download public OKX perpetual candles and historical OKX funding rates (no API key)")
    news_mode = parser.add_mutually_exclusive_group()
    news_mode.add_argument("--news-only", action="store_true", help="fetch and print latest crypto-market headlines, then exit")
    news_mode.add_argument("--watch-news", action="store_true", help="repeatedly refresh news until Ctrl+C")
    parser.add_argument("--show-news", action="store_true", help="print latest headlines before the backtest")
    parser.add_argument("--news-limit", type=int, default=15, help="headlines to display (1-100)")
    parser.add_argument("--refresh-seconds", type=int, default=300, help="news watch refresh interval, minimum 30 seconds")
    parser.add_argument("--news-cache", default=".cache/news/latest.json", help="local news snapshot path")
    parser.add_argument("--symbol", default="BTCUSDT", help="symbol for --binance (default: BTCUSDT)")
    parser.add_argument("--interval", default="5m", help="interval for --binance (default: 5m)")
    parser.add_argument("--limit", type=int, default=500, help="completed candle request limit (1-1500)")
    parser.add_argument("--cache-dir", default=".cache/binance", help="directory for downloaded candle cache")
    parser.add_argument("--strategy", choices=("ema", "break-retest"), default="ema",
                        help="strategy candidate to evaluate; EMA remains the default baseline")
    parser.add_argument("--balance", type=float, default=1000.0, help="starting paper balance")
    parser.add_argument("--candles", type=int, default=300, help="number of synthetic candles for demo")
    args = parser.parse_args()
    if args.balance <= 0:
        parser.error("--balance must be positive")
    try:
        funding_rates = None
        if args.news_only:
            print_news(fetch_latest_news(limit=args.news_limit, cache_path=args.news_cache))
            return
        if args.watch_news:
            watch_news(args.refresh_seconds, args.news_limit, args.news_cache)
            return
        if args.show_news:
            print("LATEST CRYPTO-MARKET HEADLINES (context only; not trade instructions)")
            print_news(fetch_latest_news(limit=args.news_limit, cache_path=args.news_cache))
        if args.csv:
            candles = load_csv(args.csv)
            source_label = f"CSV: {args.csv}"
        elif args.binance:
            candles = load_binance_klines(args.symbol, args.interval, args.limit, args.cache_dir)
            source_label = f"Binance public USDⓈ-M Futures candles: {args.symbol.upper()} {args.interval}"
        elif args.okx:
            candles = load_okx_swap_klines(args.symbol, args.interval, args.limit)
            funding_rates = load_okx_funding_rates(args.symbol, 400)
            source_label = f"OKX public perpetual candles + historical funding: {args.symbol.upper()} {args.interval}"
        else:
            candles = demo_candles(args.candles)
            source_label = "deterministic synthetic demo candles (not real market data)"
        strategy = EmaCrossStrategy() if args.strategy == "ema" else BreakRetestStrategy()\n        result = run_backtest(candles, args.balance, strategy=strategy, funding_rates=funding_rates)
    except (ValueError, OSError, MarketDataError, NewsError) as exc:
        parser.error(str(exc))
    print("PAPER BACKTEST ONLY — no real orders are placed")
    print(f"Data source: {source_label}")\n    print(f"Strategy: {args.strategy}")
    if args.okx:
        print("Funding: historical OKX rates; exchange-specific, not Binance funding rates")
    print(f"Candles: {len(candles)}")
    print(f"Starting balance: {result.starting_balance:.2f}")
    print(f"Ending balance:   {result.ending_balance:.2f}")
    print(f"Return:            {result.total_return_pct:.2f}%")
    print(f"Trades: {result.trades} | wins: {result.wins} | losses: {result.losses}")
    print(f"Max drawdown:      {result.max_drawdown_pct:.2f}%")


if __name__ == "__main__":
    main()
