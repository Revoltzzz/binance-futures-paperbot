from paperbot.session import PaperTradingSession
from paperbot.worker import _parse_strategy_names
from paperbot.models import Candle
from paperbot.strategy import AtrVolatilityBreakoutStrategy


def sample_candles(count=60):
    return [
        Candle(i * 300_000, 100 + i * 0.1, 100.2 + i * 0.1,
               99.8 + i * 0.1, 100 + i * 0.1, 10)
        for i in range(count)
    ]


def test_worker_accepts_atr_breakout_as_an_isolated_candidate():
    assert _parse_strategy_names("ema,atr-breakout") == ("ema", "atr-breakout")


def test_paper_session_constructs_and_restores_atr_strategy():
    session = PaperTradingSession(
        symbol="BTCUSDT", interval="5m", market_source="test",
        starting_balance=1000, strategy_name="atr-breakout",
        initial_candles=sample_candles(),
    )
    assert isinstance(session.strategy, AtrVolatilityBreakoutStrategy)
    restored = PaperTradingSession.from_dict(session.to_dict())
    assert isinstance(restored.strategy, AtrVolatilityBreakoutStrategy)
    assert restored.strategy_name == "atr-breakout"
