from paperbot.models import Candle
from paperbot.session import PaperTradingSession
from paperbot.strategy import RsiMeanReversionStrategy


def test_rsi_strategy_is_restored_as_its_own_session_type():
    candles = [
        Candle(
            timestamp=index * 300_000,
            open=100.0,
            high=100.2,
            low=99.8,
            close=100.0,
            volume=1.0,
        )
        for index in range(30)
    ]
    session = PaperTradingSession(
        symbol="ETHUSDT",
        interval="5m",
        market_source="OKX USDT perpetual swap",
        starting_balance=100.0,
        strategy_name="rsi-reversion",
        initial_candles=candles,
    )

    restored = PaperTradingSession.from_dict(session.to_dict())

    assert isinstance(session.strategy, RsiMeanReversionStrategy)
    assert isinstance(restored.strategy, RsiMeanReversionStrategy)
    assert restored.strategy_name == "rsi-reversion"
