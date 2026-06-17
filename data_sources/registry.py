from config import FINNHUB_KEY
from data_sources.base import OHLCVProvider, FundamentalsProvider, NewsProvider
from data_sources.us_market import (
    YFinanceOHLCVProvider,
    FinnhubFundamentalsProvider,
    FinnhubNewsProvider,
)


class ProviderRegistry:

    def __init__(self):
        self._ohlcv = {"US": YFinanceOHLCVProvider()}
        self._fundamentals = {
            "US": FinnhubFundamentalsProvider(api_key=FINNHUB_KEY)
        } if FINNHUB_KEY else {}
        self._news = {
            "US": FinnhubNewsProvider(api_key=FINNHUB_KEY)
        } if FINNHUB_KEY else {}

    def get_ohlcv_provider(self, market: str) -> OHLCVProvider:
        if market not in self._ohlcv:
            raise ValueError(f"No OHLCV provider registered for market: {market}")
        return self._ohlcv[market]

    def get_fundamentals_provider(self, market: str) -> FundamentalsProvider:
        if market not in self._fundamentals:
            raise ValueError(f"No fundamentals provider registered for market: {market}")
        return self._fundamentals[market]

    def get_news_provider(self, market: str) -> NewsProvider:
        if market not in self._news:
            raise ValueError(f"No news provider registered for market: {market}")
        return self._news[market]

    def group_by_market(self, ticker_rows: list[dict]) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        for row in ticker_rows:
            market = row.get("market", "US")
            groups.setdefault(market, []).append(row["ticker"])
        return groups
