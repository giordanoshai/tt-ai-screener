from datetime import date, timedelta

import pandas as pd
import yfinance as yf

from data_sources.base import OHLCVProvider, FundamentalsProvider, NewsProvider


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
