from paperbot.ai_analyst import analyze_market, classify_news_sentiment
from paperbot.models import Candle


def candles_from_closes(closes):
    rows = []
    for i, close in enumerate(closes):
        rows.append(Candle(i * 60_000, close - 0.2, close + 0.8, close - 0.8,
                           close, 100 + (i % 7) * 10))
    return rows


def test_analysis_is_explainable_and_never_emits_order():
    prices = [100 + i * 0.25 for i in range(100)]
    result = analyze_market(candles_from_closes(prices), "BTCUSDT", "5m")
    assert result.signal in {"LONG", "SHORT", "NO TRADE"}
    assert result.trend == "BULLISH"
    assert result.last_price == prices[-1]
    assert result.entry_low is None or result.entry_low <= result.entry_high
    assert result.stop_loss is None or result.target_1 is not None


def test_insufficient_candles_rejected():
    try:
        analyze_market(candles_from_closes([100 + i for i in range(30)]), "BTCUSDT", "1m")
    except ValueError as exc:
        assert "60" in str(exc)
    else:
        raise AssertionError("expected insufficient history error")


def test_news_sentiment_is_transparent_heuristic():
    assert classify_news_sentiment("Bitcoin ETF inflows surge") [0] == "BULLISH"
    assert classify_news_sentiment("Major exchange reports hack and exploit") [0] == "BEARISH"
    assert classify_news_sentiment("Market recap") [0] == "NEUTRAL"
