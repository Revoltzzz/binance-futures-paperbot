"""Conservative research-only parameter search with an untouched chronological test set.

This module never changes the live worker's configured strategy. Candidate selection is
based on a validation window; the final test window is evaluated only after selection.
"""
from dataclasses import dataclass

from .backtest import BacktestResult, run_backtest
from .models import Candle
from .strategy import BreakRetestStrategy, EmaCrossStrategy


@dataclass(frozen=True)
class Candidate:
    name: str
    strategy: object


@dataclass(frozen=True)
class CandidateScore:
    name: str
    validation: BacktestResult
    test: BacktestResult | None
    eligible: bool


@dataclass(frozen=True)
class OptimizationReport:
    train_candles: int
    validation_candles: int
    test_candles: int
    selected_name: str | None
    selected_validation: BacktestResult | None
    selected_test: BacktestResult | None
    candidates: tuple[CandidateScore, ...]


def default_candidates() -> tuple[Candidate, ...]:
    """Small, predeclared search space; deliberately avoids a huge overfit-prone grid."""
    return (
        Candidate("ema-3-12", EmaCrossStrategy(3, 12)),
        Candidate("ema-5-12", EmaCrossStrategy(5, 12)),
        Candidate("ema-5-20", EmaCrossStrategy(5, 20)),
        Candidate("ema-8-20", EmaCrossStrategy(8, 20)),
        Candidate("ema-8-30", EmaCrossStrategy(8, 30)),
        Candidate("ema-10-30", EmaCrossStrategy(10, 30)),
        Candidate("break-retest-10-30", BreakRetestStrategy(10, 30)),
        Candidate("break-retest-20-50", BreakRetestStrategy(20, 50)),
    )


def _validation_score(result: BacktestResult, min_trades: int) -> float:
    """Rank validation outcomes conservatively; sparse/no-trade candidates are ineligible."""
    if result.trades < min_trades:
        return float("-inf")
    # Net return is already after modeled costs; penalize drawdown to avoid selecting
    # a high-return candidate that took substantially larger risk in the validation set.
    return result.total_return_pct - 0.5 * result.max_drawdown_pct


def optimize_chronologically(
    candles: list[Candle],
    starting_balance: float = 100.0,
    min_trades: int = 5,
    funding_rates: dict[int, float] | None = None,
) -> OptimizationReport:
    """Select from a fixed candidate set using validation, then score untouched test data.

    Split is chronological 50% train / 25% validation / 25% final test. The first
    segment is also the indicator warm-up for validation; all earlier candles are
    warm-up only when evaluating the final test. The final test never influences
    candidate selection.
    """
    if not isinstance(min_trades, int) or isinstance(min_trades, bool) or min_trades < 1:
        raise ValueError("min_trades must be an integer >= 1")
    if starting_balance <= 0:
        raise ValueError("starting_balance must be positive")
    train_end = int(len(candles) * 0.5)
    validation_end = int(len(candles) * 0.75)
    if train_end < 50 or validation_end - train_end < 20 or len(candles) - validation_end < 20:
        raise ValueError(
            "optimization needs at least 50 train candles and 20 candles in each validation/test window"
        )

    train = candles[:train_end]
    validation = candles[train_end:validation_end]
    test = candles[validation_end:]
    scored: list[tuple[float, Candidate, BacktestResult]] = []
    for candidate in default_candidates():
        # Training is a separate historical score; validation alone drives selection.
        # No validation/test candle is used to fit indicator state or generate past P&L.
        run_backtest(train, starting_balance, strategy=candidate.strategy, funding_rates=funding_rates)
        validation_result = run_backtest(
            validation, starting_balance, strategy=candidate.strategy, warmup_candles=train, funding_rates=funding_rates
        )
        score = _validation_score(validation_result, min_trades)
        scored.append((score, candidate, validation_result))

    eligible = [item for item in scored if item[0] != float("-inf")]
    eligible.sort(key=lambda item: (item[0], item[2].expectancy_per_trade), reverse=True)
    selected = eligible[0] if eligible else None
    # Do not compute or expose test-window scores for rejected candidates: the final
    # window remains untouched by the selection process.
    selected_test = run_backtest(
        test, starting_balance, strategy=selected[1].strategy,
        warmup_candles=candles[:validation_end], funding_rates=funding_rates,
    ) if selected else None
    candidate_scores = tuple(
        CandidateScore(
            name=candidate.name,
            validation=validation_result,
            test=selected_test if selected and candidate.name == selected[1].name else None,
            eligible=score != float("-inf"),
        )
        for score, candidate, validation_result in sorted(
            scored, key=lambda item: (item[0], item[2].expectancy_per_trade), reverse=True
        )
    )
    return OptimizationReport(
        train_candles=len(train),
        validation_candles=len(validation),
        test_candles=len(test),
        selected_name=selected[1].name if selected else None,
        selected_validation=selected[2] if selected else None,
        selected_test=selected_test,
        candidates=candidate_scores,
    )
