from abc import ABC, abstractmethod
from datetime import date

import pandas as pd


class OHLCVProvider(ABC):
    @abstractmethod
    def fetch_ohlcv(self, tickers: list[str], since: date | None = None) -> pd.DataFrame:
        """Fetch OHLCV data for multiple tickers. Returns DataFrame with columns:
        ticker, date, open, high, low, close, volume."""
        ...

class FundamentalsProvider(ABC):
    @abstractmethod
    def fetch_fundamentals(self, ticker: str) -> dict:
        """Fetch fundamental metrics + earnings + insider data (rate-limited).
        Returns dict with keys: pe_ratio, ps_ratio, pb_ratio, peg_ratio,
        market_cap, revenue_growth_yoy, earnings_growth_yoy, gross_margin,
        roe, fcf_yield, company_name, sector, industry, exchange,
        next_earnings_date, last_earnings_date, mspr."""
        ...

    @abstractmethod
    def fetch_analyst_data(self, ticker: str) -> dict:
        """Fetch analyst data from a fast source (no strict rate limit).
        Returns dict with keys: analyst_rating, analyst_target_price,
        analyst_count. May also fill pe_ratio, ps_ratio, pb_ratio, peg_ratio,
        fcf_yield as fallback."""
        ...

class NewsProvider(ABC):
    @abstractmethod
    def fetch_news(self, ticker: str, days: int = 3) -> list[dict]:
        """Fetch recent news for a single ticker.
        Returns list of dicts with keys: id, headline, summary, source, url,
        published_at, sentiment_label."""
        ...
