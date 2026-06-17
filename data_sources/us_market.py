import time
from datetime import date, datetime, timedelta

import pandas as pd
import requests
import yfinance as yf

from data_sources.base import OHLCVProvider, FundamentalsProvider, NewsProvider


FINNHUB_BASE = "https://finnhub.io/api/v1"
RATE_LIMIT_SLEEP = 1.1


class YFinanceOHLCVProvider(OHLCVProvider):

    def fetch_ohlcv(self, tickers: list[str], since: date | None = None) -> pd.DataFrame:
        if not tickers:
            return pd.DataFrame()

        if since:
            start = (since - timedelta(days=30)).isoformat()
            raw = yf.download(tickers, start=start, auto_adjust=True, progress=False)
        else:
            raw = yf.download(tickers, period="2y", auto_adjust=True, progress=False)

        if raw.empty:
            return pd.DataFrame()

        if len(tickers) == 1:
            return self._normalize_single(raw, tickers[0])
        return self._normalize_multi(raw, tickers)

    def _normalize_single(self, raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
        df = raw.reset_index()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0].lower() for c in df.columns]
        else:
            df.columns = [c.lower() for c in df.columns]
        df["ticker"] = ticker
        df["volume"] = df["volume"].astype("int64")
        return df

    def _normalize_multi(self, raw: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
        frames = []
        for ticker in tickers:
            try:
                cols = {field: (field, ticker) for field in ["Open", "High", "Low", "Close", "Volume"]}
                present = all(c in raw.columns for c in cols.values())
                if not present:
                    continue
                sub = raw[[cols["Open"], cols["High"], cols["Low"], cols["Close"], cols["Volume"]]].copy()
                sub.columns = ["open", "high", "low", "close", "volume"]
                sub = sub.dropna(subset=["close"])
                if sub.empty:
                    continue
                sub = sub.reset_index()
                if isinstance(sub.columns, pd.MultiIndex):
                    sub.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in sub.columns]
                else:
                    sub.columns = [c.lower() for c in sub.columns]
                sub["ticker"] = ticker
                sub["volume"] = sub["volume"].astype("int64")
                frames.append(sub)
            except Exception:
                continue
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


class FinnhubFundamentalsProvider(FundamentalsProvider):

    def __init__(self, api_key: str):
        self.api_key = api_key

    def _get(self, path: str, params: dict) -> dict | list:
        params["token"] = self.api_key
        r = requests.get(f"{FINNHUB_BASE}{path}", params=params, timeout=15)
        r.raise_for_status()
        return r.json()

    def fetch_fundamentals(self, ticker: str) -> dict:
        data = self._get("/stock/metric", {"symbol": ticker, "metric": "all"})
        m = data.get("metric", {})
        time.sleep(RATE_LIMIT_SLEEP)

        profile = self._get("/stock/profile2", {"symbol": ticker})
        time.sleep(RATE_LIMIT_SLEEP)

        def g(key):
            v = m.get(key)
            return float(v) if v is not None else None

        def gp(key):
            v = m.get(key)
            return float(v) / 100.0 if v is not None else None

        return {
            "ticker": ticker,
            "pe_ratio": g("peExclExtraTTM"),
            "ps_ratio": g("psTTM"),
            "pb_ratio": g("pbQuarterly"),
            "peg_ratio": g("pegAnnual"),
            "market_cap": g("marketCapitalization"),
            "revenue_growth_yoy": gp("revenueGrowthTTMYoy"),
            "earnings_growth_yoy": gp("epsGrowthTTMYoy"),
            "gross_margin": gp("grossMarginTTM"),
            "roe": g("roeTTM"),
            "fcf_yield": g("fcfYieldTTM"),
            "company_name": profile.get("name", ""),
            "exchange": profile.get("exchange", ""),
            "sector": profile.get("finnhubIndustry", ""),
            "industry": profile.get("finnhubIndustry", ""),
        }


class FinnhubNewsProvider(NewsProvider):

    def __init__(self, api_key: str):
        self.api_key = api_key

    def _get(self, path: str, params: dict) -> dict | list:
        params["token"] = self.api_key
        r = requests.get(f"{FINNHUB_BASE}{path}", params=params, timeout=15)
        r.raise_for_status()
        return r.json()

    def fetch_news(self, ticker: str, days: int = 3) -> list[dict]:
        date_to = date.today().isoformat()
        date_from = (date.today() - timedelta(days=days)).isoformat()

        articles = self._get("/company-news", {
            "symbol": ticker,
            "from": date_from,
            "to": date_to,
        })
        time.sleep(RATE_LIMIT_SLEEP)

        if not isinstance(articles, list):
            return []

        result = []
        for art in articles:
            art_id = art.get("id")
            if not art_id:
                continue
            published = (
                datetime.fromtimestamp(art["datetime"]).isoformat()
                if art.get("datetime") else None
            )
            result.append({
                "id": art_id,
                "ticker": ticker,
                "headline": art.get("headline", ""),
                "summary": art.get("summary", ""),
                "source": art.get("source", ""),
                "url": art.get("url", ""),
                "published_at": published,
                "sentiment_label": None,
            })
        return result
