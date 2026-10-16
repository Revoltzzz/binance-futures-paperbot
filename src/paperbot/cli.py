import argparse
from dataclasses import replace

from .backtest import run_backtest
from .binance_data import MarketDataError, load_binance_klines
from .data import demo_candles, load_csv
from .news import NewsError, fetch_latest_news, print_news, watch_news
from .optimizer import optimize_chronologically
from .public_market_data import load_okx_funding_rates, load_okx_swap_klines
from .risk import RiskConfig
from .strategy import AtrVolatilityBreakoutStrategy, BreakRetestStrategy, EmaCrossStrategy


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
    parser.add_argument("--strategy", choices=("ema", "break-retest", "atr-breakout"), default="ema",
                        help="strategy candidate to evaluate; EMA remains the default baseline")
    evaluation_mode = parser.add_mutually_exclusive_group()
    evaluation_mode.add_argument("--compare-strategies", action="store_true",
                                 help="compare EMA and break-retest on the final 30% holdout; no parameter tuning")
    evaluation_mode.add_argument("--walk-forward", action="store_true",
                                 help="evaluate both fixed strategies across sequential unseen windows")
    evaluation_mode.add_argument("--cost-sensitivity", action="store_true",
                                 help="compare both fixed strategies on the final 30% holdout at 1x, 1.5x and 2x modeled costs")
    evaluation_mode.add_argument("--optimize-validate-test", action="store_true",
                                 help="select from a small fixed candidate grid on validation data, then evaluate only the selected candidate on an untouched final test window")
    parser.add_argument("--walk-forward-folds", type=int, default=3,
                        help="number of sequential holdout folds for --walk-forward (2-10)")
    parser.add_argument("--balance", type=float, default=100.0, help="starting paper balance")
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
        walk_forward_results = None
        cost_sensitivity_results = None
        optimization_report = None
        if args.optimize_validate_test:
            optimization_report = optimize_chronologically(
                candles, starting_balance=args.balance, funding_rates=funding_rates
            )
            result = None
        elif args.cost_sensitivity:
            split_index = int(len(candles) * 0.7)
            if split_index < 20 or len(candles) - split_index < 2:
                raise ValueError("cost sensitivity needs at least 20 warmup candles and 2 holdout candles")
            warmup_candles = candles[:split_index]
            test_candles = candles[split_index:]
            candidates = (
                ("ema", EmaCrossStrategy()),
                ("break-retest", BreakRetestStrategy()),
            )
            cost_sensitivity_results = []
            base_costs = RiskConfig()
            for multiplier in (1.0, 1.5, 2.0):
                scenario_config = replace(
                    base_costs,
                    fee_rate=base_costs.fee_rate * multiplier,
                    slippage_rate=base_costs.slippage_rate * multiplier,
                    funding_rate_per_8h=base_costs.funding_rate_per_8h * multiplier,
                )
                scenario_funding = (
                    None if funding_rates is None
                    else {timestamp: rate * multiplier for timestamp, rate in funding_rates.items()}
                )
                for name, candidate in candidates:
                    scenario_result = run_backtest(
                        test_candles, args.balance, strategy=candidate,
                        funding_rates=scenario_funding, warmup_candles=warmup_candles,
                        risk_config=scenario_config,
                    )
                    cost_sensitivity_results.append((multiplier, name, scenario_result))
            result = None
        elif args.walk_forward:
            if not 2 <= args.walk_forward_folds <= 10:
                raise ValueError("--walk-forward-folds must be between 2 and 10")
            first_test_index = len(candles) // 2
            remaining = len(candles) - first_test_index
            fold_size = remaining // args.walk_forward_folds
            if first_test_index < 50 or fold_size < 10:
                raise ValueError("walk-forward needs at least 50 warmup candles and 10 candles per test fold")
            candidates = (
                ("ema", EmaCrossStrategy()),
                ("break-retest", BreakRetestStrategy()),
            )
            walk_forward_results = []
            for fold_index in range(args.walk_forward_folds):
                start_index = first_test_index + fold_index * fold_size
                end_index = (len(candles) if fold_index == args.walk_forward_folds - 1
                             else start_index + fold_size)
                warmup_candles = candles[:start_index]
                test_candles = candles[start_index:end_index]
                for name, candidate in candidates:
                    fold_result = run_backtest(
                        test_candles, args.balance, strategy=candidate,
                        funding_rates=funding_rates, warmup_candles=warmup_candles
                    )
                    walk_forward_results.append((fold_index + 1, name, fold_result))
            result = None
        elif args.compare_strategies:
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
            strategy = {"ema": EmaCrossStrategy, "break-retest": BreakRetestStrategy, "atr-breakout": AtrVolatilityBreakoutStrategy}[args.strategy]()
            result = run_backtest(candles, args.balance, strategy=strategy, funding_rates=funding_rates)
    except (ValueError, OSError, MarketDataError, NewsError) as exc:
        parser.error(str(exc))
    print("PAPER BACKTEST ONLY — no real orders are placed")
    print(f"Data source: {source_label}")
    if args.okx:
        print("Funding: historical OKX rates; exchange-specific, not Binance funding rates")
    print(f"Candles: {len(candles)}")
    if optimization_report is not None:
        report = optimization_report
        print(
            f"Chronological optimization: train={report.train_candles}, "
            f"validation={report.validation_candles}, untouched_test={report.test_candles} candles"
        )
        print("Candidates are ranked only on validation net return penalized by drawdown; minimum 5 closed trades.")
        for candidate in report.candidates:
            val = candidate.validation
            pf = "n/a" if val.profit_factor is None else f"{val.profit_factor:.3f}"
            marker = "SELECTED" if candidate.name == report.selected_name else ("ineligible" if not candidate.eligible else "not selected")
            print(
                f"{marker:12} {candidate.name:22} validation_return={val.total_return_pct:8.2f}% "
                f"maxDD={val.max_drawdown_pct:7.2f}% trades={val.trades:4d} "
                f"profitFactor={pf:>7} expectancy={val.expectancy_per_trade:9.4f}"
            )
        if report.selected_name is None or report.selected_test is None:
            print("No candidate met the minimum validation trade count; no candidate was selected.")
        else:
            test = report.selected_test
            pf = "n/a" if test.profit_factor is None else f"{test.profit_factor:.3f}"
            print("UNTOUCHED FINAL TEST — only the validation-selected candidate was evaluated here:")
            print(
                f"{report.selected_name} return={test.total_return_pct:.2f}% "
                f"maxDD={test.max_drawdown_pct:.2f}% trades={test.trades} "
                f"profitFactor={pf} expectancy={test.expectancy_per_trade:.4f}"
            )
        print("Research only: this does not change the worker strategy or unpause the paper session.")
        print("A single holdout is not proof of future profitability; verify on more symbols, periods, and cost assumptions.")
        return
    if cost_sensitivity_results is not None:
        print(f"Cost sensitivity: final {len(candles) - int(len(candles) * 0.7)} candles holdout; prior candles are warm-up only")
        print("Costs scaled together: commission, adverse slippage, and funding; strategy parameters stay fixed.")
        for multiplier, name, candidate_result in cost_sensitivity_results:
            pf = "n/a" if candidate_result.profit_factor is None else f"{candidate_result.profit_factor:.3f}"
            print(
                f"{multiplier:3.1f}x costs | {name:14} return={candidate_result.total_return_pct:8.2f}% "
                f"maxDD={candidate_result.max_drawdown_pct:7.2f}% "
                f"trades={candidate_result.trades:4d} "
                f"profitFactor={pf:>7} expectancy={candidate_result.expectancy_per_trade:9.4f}"
            )
        print("Higher-cost scenarios are stress tests, not forecasts; no strategy is automatically selected.")
        return
    if walk_forward_results is not None:
        print(f"Walk-forward: {args.walk_forward_folds} sequential test folds; earlier candles are warm-up only")
        for fold_number, name, candidate_result in walk_forward_results:
            pf = "n/a" if candidate_result.profit_factor is None else f"{candidate_result.profit_factor:.3f}"
            win_rate = candidate_result.wins / candidate_result.trades * 100 if candidate_result.trades else 0
            print(
                f"Fold {fold_number} | {name:14} return={candidate_result.total_return_pct:8.2f}% "
                f"maxDD={candidate_result.max_drawdown_pct:7.2f}% "
                f"trades={candidate_result.trades:4d} winRate={win_rate:6.2f}% "
                f"profitFactor={pf:>7} expectancy={candidate_result.expectancy_per_trade:9.4f}"
            )
        print("Parameters stay fixed; this is sequential out-of-sample testing, not parameter optimization.")
        print("Each fold resets paper balance; compare consistency across folds, not summed returns.")
        return
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
