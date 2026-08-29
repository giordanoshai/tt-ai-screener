from db.update import find_movers


def test_find_movers_by_pct_chg():
    rows = [
        {"ticker": "AAPL", "pct_chg": 6.0, "vol_ratio": 1.0},
        {"ticker": "MSFT", "pct_chg": 2.0, "vol_ratio": 1.0},
        {"ticker": "TSLA", "pct_chg": -7.0, "vol_ratio": 1.0},
    ]
    movers = find_movers(rows, pct_threshold=5.0, vol_threshold=2.0)
    assert set(movers) == {"AAPL", "TSLA"}


def test_find_movers_by_vol_ratio():
    rows = [
        {"ticker": "AAPL", "pct_chg": 1.0, "vol_ratio": 2.5},
        {"ticker": "MSFT", "pct_chg": 1.0, "vol_ratio": 1.0},
    ]
    movers = find_movers(rows, pct_threshold=5.0, vol_threshold=2.0)
    assert movers == ["AAPL"]


def test_find_movers_handles_none():
    rows = [
        {"ticker": "AAPL", "pct_chg": None, "vol_ratio": None},
        {"ticker": "MSFT", "pct_chg": 6.0, "vol_ratio": None},
    ]
    movers = find_movers(rows, pct_threshold=5.0, vol_threshold=2.0)
    assert movers == ["MSFT"]
