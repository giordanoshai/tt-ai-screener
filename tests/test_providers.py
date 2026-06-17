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


from data_sources.us_market import FinnhubFundamentalsProvider, FinnhubNewsProvider


@patch("data_sources.us_market.requests.get")
def test_finnhub_fundamentals_returns_dict(mock_get):
    mock_metric_resp = MagicMock()
    mock_metric_resp.json.return_value = {
        "metric": {
            "peExclExtraTTM": 28.5,
            "psTTM": 7.2,
            "pbQuarterly": 45.0,
            "pegAnnual": 1.5,
            "marketCapitalization": 3000000,
            "revenueGrowthTTMYoy": 12.5,
            "epsGrowthTTMYoy": 15.3,
            "grossMarginTTM": 46.2,
            "roeTTM": 160.0,
            "fcfYieldTTM": 3.5,
        }
    }
    mock_metric_resp.raise_for_status = MagicMock()

    mock_profile_resp = MagicMock()
    mock_profile_resp.json.return_value = {
        "name": "Apple Inc",
        "exchange": "NASDAQ",
        "finnhubIndustry": "Technology",
    }
    mock_profile_resp.raise_for_status = MagicMock()

    mock_get.side_effect = [mock_metric_resp, mock_profile_resp]

    provider = FinnhubFundamentalsProvider(api_key="test_key")
    result = provider.fetch_fundamentals("AAPL")

    assert result["pe_ratio"] == 28.5
    assert result["revenue_growth_yoy"] == pytest.approx(0.125)
    assert result["company_name"] == "Apple Inc"
    assert result["sector"] == "Technology"


@patch("data_sources.us_market.requests.get")
def test_finnhub_news_returns_list(mock_get):
    mock_resp = MagicMock()
    mock_resp.json.return_value = [
        {"id": 1, "headline": "Test", "summary": "s", "source": "src",
         "url": "http://x", "datetime": 1718550000, "related": "AAPL"},
    ]
    mock_resp.raise_for_status = MagicMock()
    mock_get.return_value = mock_resp

    provider = FinnhubNewsProvider(api_key="test_key")
    result = provider.fetch_news("AAPL", days=3)

    assert len(result) == 1
    assert result[0]["id"] == 1
    assert result[0]["headline"] == "Test"
    assert "published_at" in result[0]
