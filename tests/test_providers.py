import pytest
import pandas as pd
from datetime import date
from data_sources.base import OHLCVProvider, FundamentalsProvider, NewsProvider


def test_ohlcv_provider_is_abstract():
    with pytest.raises(TypeError):
        OHLCVProvider()


def test_fundamentals_provider_is_abstract():
    with pytest.raises(TypeError):
        FundamentalsProvider()


def test_news_provider_is_abstract():
    with pytest.raises(TypeError):
        NewsProvider()


class ConcreteOHLCV(OHLCVProvider):
    def fetch_ohlcv(self, tickers, since=None):
        import pandas as pd
        return pd.DataFrame()

class ConcreteFundamentals(FundamentalsProvider):
    def fetch_fundamentals(self, ticker):
        return {}

class ConcreteNews(NewsProvider):
    def fetch_news(self, ticker, days=3):
        return []


def test_concrete_ohlcv_instantiates():
    p = ConcreteOHLCV()
    assert p.fetch_ohlcv(["AAPL"]).empty


def test_concrete_fundamentals_instantiates():
    p = ConcreteFundamentals()
    assert p.fetch_fundamentals("AAPL") == {}


def test_concrete_news_instantiates():
    p = ConcreteNews()
    assert p.fetch_news("AAPL") == []


from unittest.mock import patch, MagicMock
import numpy as np
from data_sources.us_market import YFinanceOHLCVProvider


def _make_mock_yf_data(ticker: str, n_rows: int = 30):
    """Create mock yfinance-style DataFrame for a single ticker."""
    dates = pd.bdate_range(end="2026-06-16", periods=n_rows)
    base = 150.0
    closes = base + np.random.randn(n_rows).cumsum()
    return pd.DataFrame({
        ("Open", ticker): closes - 1,
        ("High", ticker): closes + 2,
        ("Low", ticker): closes - 2,
        ("Close", ticker): closes,
        ("Volume", ticker): np.random.randint(1_000_000, 10_000_000, n_rows),
    }, index=dates)


@patch("data_sources.us_market.yf.download")
def test_yfinance_ohlcv_batch_download(mock_download):
    mock_data = pd.concat([_make_mock_yf_data("AAPL", 30), _make_mock_yf_data("MSFT", 30)], axis=1)
    mock_data.columns = pd.MultiIndex.from_tuples(mock_data.columns)
    mock_download.return_value = mock_data

    provider = YFinanceOHLCVProvider()
    result = provider.fetch_ohlcv(["AAPL", "MSFT"])

    assert not result.empty
    assert "ticker" in result.columns
    assert "close" in result.columns
    assert set(result["ticker"].unique()) == {"AAPL", "MSFT"}
    mock_download.assert_called_once()


@patch("data_sources.us_market.yf.download")
def test_yfinance_ohlcv_empty_result(mock_download):
    mock_download.return_value = pd.DataFrame()
    provider = YFinanceOHLCVProvider()
    result = provider.fetch_ohlcv(["FAKE"])
    assert result.empty
