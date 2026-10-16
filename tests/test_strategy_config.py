import pytest

from paperbot.worker import _parse_strategy_names


def test_strategy_list_parses_multiple_candidates_in_order():
    assert _parse_strategy_names("break-retest, rsi-reversion", "ema") == (
        "break-retest",
        "rsi-reversion",
    )


def test_strategy_list_falls_back_to_single_strategy():
    assert _parse_strategy_names(None, "ema") == ("ema",)


@pytest.mark.parametrize("value", ["", "ema,", "ema, ,rsi-reversion"])
def test_strategy_list_rejects_empty_items(value):
    with pytest.raises(ValueError, match="non-empty"):
        _parse_strategy_names(value)


def test_strategy_list_rejects_duplicates():
    with pytest.raises(ValueError, match="duplicates"):
        _parse_strategy_names("ema,ema")


def test_strategy_list_rejects_unknown_strategy():
    with pytest.raises(ValueError, match="unsupported"):
        _parse_strategy_names("ema,magic-profit")
