import pytest

from paperbot.storage import SQLitePaperStore, SessionConflict


def test_store_round_trips_state_and_increments_revision(tmp_path):
    store = SQLitePaperStore(tmp_path / "paper.sqlite")
    assert store.load("BTCUSDT:5m") is None
    revision = store.save("BTCUSDT:5m", {"balance": 1000, "last": 123}, expected_revision=None)
    assert revision == 1
    loaded = store.load("BTCUSDT:5m")
    assert loaded == (1, {"balance": 1000, "last": 123})

    revision = store.save("BTCUSDT:5m", {"balance": 995, "last": 456}, expected_revision=1)
    assert revision == 2
    assert store.load("BTCUSDT:5m") == (2, {"balance": 995, "last": 456})


def test_store_rejects_duplicate_create_and_stale_writer(tmp_path):
    store = SQLitePaperStore(tmp_path / "paper.sqlite")
    store.save("BTCUSDT:5m", {"balance": 1000}, expected_revision=None)
    with pytest.raises(SessionConflict):
        store.save("BTCUSDT:5m", {"balance": 900}, expected_revision=None)
    store.save("BTCUSDT:5m", {"balance": 990}, expected_revision=1)
    with pytest.raises(SessionConflict):
        store.save("BTCUSDT:5m", {"balance": 800}, expected_revision=1)
    assert store.load("BTCUSDT:5m") == (2, {"balance": 990})


def test_store_rejects_non_json_finite_values(tmp_path):
    store = SQLitePaperStore(tmp_path / "paper.sqlite")
    with pytest.raises(ValueError):
        store.save("BTCUSDT:5m", {"balance": float("nan")}, expected_revision=None)
