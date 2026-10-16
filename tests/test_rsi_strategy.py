import math

import pytest

from paperbot.models import Candle
from paperbot.strategy import RsiMeanReversionStrategy


def candles_from_closes(closes):
    result = []
    for index, close in enumerate(closes):
        previous = closes[index - 1] if index else close
        result.append(Candle(
            timestamp=index * 300_000,
            open=previous,
            high=max(previous, close) * 1.001,
            low=min(previous, close) * 0.999,
            close=close,
            volume=1.0,
        ))
    return result


def test_rsi_reversion_waits_for_sufficient_history():
    strategy = RsiMeanReversionStrategy()
    assert strategy.signal(candles_from_closes([100.0] * 20)) is None


def test_rsi_reversion_does_not_signal_on_flat_market():
    strategy = RsiMeanReversionStrategy()
    assert strategy.signal(candles_from_closes([100.0] * 40)) is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"rsi_period": 1},
        {"band_period": 1},
        {"oversold": 70, "overbought": 30},
        {"band_stddev": 0},
        {"band_stddev": math.inf},
    ],
)
def test_rsi_reversion_rejects_invalid_parameters(kwargs):
    with pytest.raises(ValueError):
        RsiMeanReversionStrategy(**kwargs)


def test_rsi_reversion_constant_prices_have_neutral_rsi():
    strategy = RsiMeanReversionStrategy()
    values = strategy._rsi([100.0] * 30)
    assert values[-1] == 50.0
