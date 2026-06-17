import pytest
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
