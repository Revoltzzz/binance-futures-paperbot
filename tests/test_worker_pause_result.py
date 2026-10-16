from paperbot.worker import _env_int, _is_paused_result


def test_safety_pause_result_is_recognized_without_parsing_reason():
    assert _is_paused_result(
        "PAUSED at revision 111: Performance circuit breaker: manual review required."
    )
    assert _is_paused_result("PAUSED at revision 2: market-data source changed")
    assert not _is_paused_result("No new candles; account revision 111 remains current.")
    assert not _is_paused_result("Processed 1 candle(s); revision=112")


def test_paused_poll_interval_is_bounded_and_configurable(monkeypatch):
    monkeypatch.setenv("PAPERBOT_PAUSED_POLL_SECONDS", "600")
    assert _env_int("PAPERBOT_PAUSED_POLL_SECONDS", 300, 60, 3600) == 600

    monkeypatch.setenv("PAPERBOT_PAUSED_POLL_SECONDS", "30")
    try:
        _env_int("PAPERBOT_PAUSED_POLL_SECONDS", 300, 60, 3600)
    except ValueError as exc:
        assert "between 60 and 3600" in str(exc)
    else:
        raise AssertionError("paused poll interval below the safety minimum must be rejected")
