import pytest
from data_sources.registry import ProviderRegistry
from data_sources.base import OHLCVProvider, FundamentalsProvider, NewsProvider


def test_get_us_ohlcv_provider():
    reg = ProviderRegistry()
    p = reg.get_ohlcv_provider("US")
    assert isinstance(p, OHLCVProvider)


def test_get_us_fundamentals_provider():
    reg = ProviderRegistry()
    p = reg.get_fundamentals_provider("US")
    assert isinstance(p, FundamentalsProvider)


def test_get_us_news_provider():
    reg = ProviderRegistry()
    p = reg.get_news_provider("US")
    assert isinstance(p, NewsProvider)


def test_unknown_market_raises():
    reg = ProviderRegistry()
    with pytest.raises(ValueError, match="CN"):
        reg.get_ohlcv_provider("CN")


def test_get_all_tickers_grouped_by_market():
    reg = ProviderRegistry()
    tickers = [
        {"ticker": "AAPL", "market": "US"},
        {"ticker": "MSFT", "market": "US"},
    ]
    grouped = reg.group_by_market(tickers)
    assert grouped == {"US": ["AAPL", "MSFT"]}
