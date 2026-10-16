import pytest

from paperbot.data import demo_candles
from paperbot.optimizer import optimize_chronologically
from paperbot.backtest import run_backtest


def test_optimizer_selects_on_validation_and_tests_only_selected_candidate():
    report = optimize_chronologically(demo_candles(400), min_trades=1)

    assert report.train_candles == 200
    assert report.validation_candles == 100
    assert report.test_candles == 100
    assert report.selected_name is not None
    assert report.selected_validation is not None
    assert report.selected_test is not None
    tested_candidates = [candidate.name for candidate in report.candidates if candidate.test is not None]
    assert tested_candidates == [report.selected_name]
    assert report.candidates[0].validation is not None


def test_optimizer_refuses_too_little_data():
    with pytest.raises(ValueError, match="at least 50 train candles"):
        optimize_chronologically(demo_candles(80))


def test_optimizer_rejects_invalid_minimum_trade_count():
    with pytest.raises(ValueError, match="min_trades"):
        optimize_chronologically(demo_candles(300), min_trades=0)



def test_cli_runs_validation_selected_untouched_test_mode(monkeypatch, capsys):
    import sys
    from paperbot.cli import main

    monkeypatch.setattr(
        sys, "argv", ["paperbot", "--candles", "400", "--optimize-validate-test"]
    )
    main()
    output = capsys.readouterr().out
    assert "Chronological optimization:" in output
    assert "UNTOUCHED FINAL TEST" in output or "No candidate met the minimum validation trade count" in output
    assert "does not change the worker strategy" in output

@pytest.mark.parametrize("balance", [float("nan"), float("inf"), float("-inf"), 0, -1])
def test_optimizer_rejects_non_finite_or_non_positive_balance(balance):
    with pytest.raises(ValueError, match="finite and positive"):
        optimize_chronologically(demo_candles(300), starting_balance=balance)


def test_optimizer_rejects_out_of_order_candles():
    candles = demo_candles(300)
    candles[120], candles[121] = candles[121], candles[120]
    with pytest.raises(ValueError, match="strictly increasing"):
        optimize_chronologically(candles)


def test_optimizer_rejects_non_candle_items():
    candles = demo_candles(300)
    candles[100] = {"timestamp": candles[100].timestamp}
    with pytest.raises(ValueError, match="valid Candle objects"):
        optimize_chronologically(candles)


@pytest.mark.parametrize(
    "funding_rates",
    [
        {1: float("nan")},
        {1: float("inf")},
        {-1: 0.001},
        {1.5: 0.001},
        {1: True},
        [],
    ],
)
def test_optimizer_rejects_invalid_funding_inputs(funding_rates):
    with pytest.raises(ValueError, match="funding"):
        optimize_chronologically(demo_candles(300), funding_rates=funding_rates)

@pytest.mark.parametrize("balance", [float("nan"), float("inf"), float("-inf"), 0, -1])
def test_backtest_rejects_invalid_starting_balance(balance):
    with pytest.raises(ValueError, match="finite and positive"):
        run_backtest(demo_candles(120), starting_balance=balance)


def test_backtest_rejects_malformed_candle_history():
    candles = demo_candles(120)
    candles[50] = {"timestamp": candles[50].timestamp}
    with pytest.raises(ValueError, match="valid Candle objects"):
        run_backtest(candles)


@pytest.mark.parametrize("funding_rates", [{1: float("nan")}, {1: float("inf")}, {-1: 0.01}, {1.5: 0.01}, {1: True}, []])
def test_backtest_rejects_invalid_funding_rates(funding_rates):
    with pytest.raises(ValueError, match="funding"):
        run_backtest(demo_candles(120), funding_rates=funding_rates)


def test_backtest_rejects_malformed_warmup_history():
    candles = demo_candles(120)
    with pytest.raises(ValueError, match="warmup_candles"):
        run_backtest(candles, warmup_candles=[{"timestamp": candles[0].timestamp - 1}])

