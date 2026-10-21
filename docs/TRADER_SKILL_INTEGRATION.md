# Trader Skill-inspired safeguards

This additive integration adapts two useful ideas from
[Jakeschincariol/trader-skill](https://github.com/Jakeschincariol/trader-skill)
to this crypto futures paper bot: a strict risk gate and evidence-based review
before considering a strategy change.

## What already exists

- `paperbot.risk` sizes positions using stop distance, estimated costs and a
  leverage cap, and checks the daily loss limit.
- `paperbot.paper.PaperBroker` is a local simulator; it does not send exchange
  orders.
- `paperbot.news` collects public crypto headlines. Headlines are leads, not
  verified facts, and must never trigger trades by themselves.
- `paperbot.review` reports closed-trade diagnostics.

## New candidate-strategy governance

`paperbot.strategy_governance.review_strategy_candidate(trades, ...)`
evaluates a supplied set of closed paper/backtest trades with net P&L after
modelled fees, slippage and funding. By default it requires at least 30 trades,
positive total net P&L, profit factor of at least 1.2, and peak-to-trough
drawdown no greater than 10% of peak equity.

Example:

```python
from paperbot.strategy_governance import review_strategy_candidate

report = review_strategy_candidate(
    [{"net_pnl": 8.2}, {"net_pnl": -3.1}],
    starting_equity=1000,
)
print(report)
```

A passing result means **eligible for manual review only**. It does not
automatically select, tune, or activate a strategy. Evaluate on chronologically
separated, untouched out-of-sample data and compare against a baseline before
any change to the worker configuration. The thresholds are conservative
screening defaults, not a claim that a strategy is profitable.

## Safety and history

- No exchange order adapter, API credentials, or live-trading switch is added.
- Existing worker/session/storage code and paper-trading records are untouched.
- Invalid or non-finite trade metrics fail closed.
- This review function is additive and is not wired to auto-change worker
  strategies; automatic promotion is intentionally not implemented.
- Headlines do not change strategy decisions or place simulated orders.
