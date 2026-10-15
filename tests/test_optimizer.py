import pytest

from paperbot.data import demo_candles
from paperbot.optimizer import optimize_chronologically


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
        optimize_chronologically(demo_candles(100))


def test_optimizer_rejects_invalid_minimum_trade_count():
    with pytest.raises(ValueError, match="min_trades"):
        optimize_chronologically(demo_candles(300), min_trades=0)
