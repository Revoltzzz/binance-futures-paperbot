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
    parser.add_argument("--compare-strategies", action="store_true",
                        help="compare EMA and break-retest on the final 30% holdout; no parameter tuning")
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
        comparison_results = None
        if args.compare_strategies:
            split_index = int(len(candles) * 0.7)
            if split_index < 20 or len(candles) - split_index < 2:
                raise ValueError("strategy comparison needs at least 20 warmup candles and 2 holdout candles")
            warmup_candles = candles[:split_index]
            test_candles = candles[split_index:]
            candidates = (
                ("ema", EmaCrossStrategy()),
                ("break-retest", BreakRetestStrategy()),
            )
            comparison_results = [
                (name, run_backtest(
                    test_candles, args.balance, strategy=candidate,
                    funding_rates=funding_rates, warmup_candles=warmup_candles
                ))
                for name, candidate in candidates
            ]
            result = None
        else:
            strategy = EmaCrossStrategy() if args.strategy == "ema" else BreakRetestStrategy()
            result = run_backtest(candles, args.balance, strategy=strategy, funding_rates=funding_rates)
    except (ValueError, OSError, MarketDataError, NewsError) as exc:
        parser.error(str(exc))
    print("PAPER BACKTEST ONLY — no real orders are placed")
    print(f"Data source: {source_label}")
    if args.okx:
        print("Funding: historical OKX rates; exchange-specific, not Binance funding rates")
    print(f"Candles: {len(candles)}")
    if comparison_results is not None:
        print(f"Holdout: final {len(candles) - int(len(candles) * 0.7)} candles; prior candles used only for indicator warm-up")
        print("Strategy comparison (same holdout, same risk/cost assumptions):")
        for name, candidate_result in comparison_results:
            pf = "n/a" if candidate_result.profit_factor is None else f"{candidate_result.profit_factor:.3f}"
            print(
                f"{name:14} return={candidate_result.total_return_pct:8.2f}% "
                f"maxDD={candidate_result.max_drawdown_pct:7.2f}% "
                f"trades={candidate_result.trades:4d} "
                f"winRate={(candidate_result.wins / candidate_result.trades * 100 if candidate_result.trades else 0):6.2f}% "
                f"profitFactor={pf:>7} expectancy={candidate_result.expectancy_per_trade:9.4f}"
            )
        print("This is one chronological holdout, not proof of future profitability.")
        return
    print(f"Strategy: {args.strategy}")
    print(f"Starting balance: {result.starting_balance:.2f}")
    print(f"Ending balance:   {result.ending_balance:.2f}")
    print(f"Return:            {result.total_return_pct:.2f}%")
    print(f"Trades: {result.trades} | wins: {result.wins} | losses: {result.losses}")
    print(f"Max drawdown:      {result.max_drawdown_pct:.2f}%")
    print(f"Net expectancy/trade: {result.expectancy_per_trade:.4f}")
    if result.profit_factor is None:
        print("Profit factor:     n/a (no losing trades in this sample)")
    else:
        print(f"Profit factor:     {result.profit_factor:.3f}")


if __name__ == "__main__":
    main()
