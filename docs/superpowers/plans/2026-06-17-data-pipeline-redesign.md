# Data Pipeline Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the data ingestion pipeline to support thousands of tickers across multiple markets, with provider-pluggable architecture, tier-based news gating, batch OHLCV download, and a frontend screener with on-demand enrichment.

**Architecture:** Three-layer provider abstraction (OHLCV / Fundamentals / News) with a registry that routes by market field. Daily update pipeline uses batch yfinance for OHLCV, 7-day cache for fundamentals, and tier+mover gating for news. Web frontend provides sector + technical screening against local DB, with cache-first Finnhub fallback for single-ticker detail views.

**Tech Stack:** Python 3.11+, DuckDB, yfinance (batch), Finnhub API, FastAPI, pandas/pandas-ta, pytest

**Spec:** `docs/superpowers/specs/2026-06-16-data-source-pipeline-design.md`

---

## File Structure

### New files
- `data_sources/__init__.py` — package init
- `data_sources/base.py` — ABC interfaces: `OHLCVProvider`, `FundamentalsProvider`, `NewsProvider`
- `data_sources/us_market.py` — US implementations: yfinance batch OHLCV, Finnhub fundamentals + news
- `data_sources/registry.py` — Market-based provider routing
- `db/universe.py` — CSV-based universe import script
- `db/migrate.py` — Schema migration: add `market`, `tier` columns to `stocks_meta`
- `db/run_daily_update.bat` — Windows scheduler wrapper
- `tests/__init__.py` — test package
- `tests/test_providers.py` — Provider ABC + US provider tests
- `tests/test_registry.py` — Registry routing tests
- `tests/test_update.py` — Update pipeline: batch split, mover gating
- `tests/test_screen.py` — Web screener endpoint tests
- `tests/test_ticker_detail.py` — Cache-first detail endpoint tests

### Modified files
- `config.py` — Add `FUNDAMENTALS_STALE_DAYS`, `NEWS_MOVER_PCT_THRESHOLD`, `NEWS_MOVER_VOLRATIO_THRESHOLD`, `OHLCV_BATCH_SIZE`
- `db/init.py` — Add `market`, `tier` columns to `stocks_meta` CREATE TABLE
- `db/fetch.py` — Rewrite to use providers; keep `_calc_indicators` as shared utility
- `db/update.py` — Rewrite: batch OHLCV, 7-day fundamentals, tier+mover news gating
- `web/app.py` — Add `POST /screen`, `GET /ticker/{ticker}` endpoints
- `requirements.txt` — Add `pytest`

### Unchanged files
- `server.py` — No changes needed (MCP tools still call functions in `tools/`)
- `tools/screening.py` — Unchanged (already queries DB directly)
- `tools/trades.py` — Unchanged
- `setup.py` — Unchanged (still calls `db.fetch.fetch_all`)

---

### Task 1: Config updates

**Files:**
- Modify: `config.py`

- [ ] **Step 1: Add new config constants**

```python
# In config.py, after NEWS_DAYS line, add:

FUNDAMENTALS_STALE_DAYS = int(os.getenv("FUNDAMENTALS_STALE_DAYS", "7"))
NEWS_MOVER_PCT_THRESHOLD = float(os.getenv("NEWS_MOVER_PCT_THRESHOLD", "5.0"))
NEWS_MOVER_VOLRATIO_THRESHOLD = float(os.getenv("NEWS_MOVER_VOLRATIO_THRESHOLD", "2.0"))
OHLCV_BATCH_SIZE = int(os.getenv("OHLCV_BATCH_SIZE", "200"))
```

- [ ] **Step 2: Verify config loads**

Run: `cd D:\Dev_project\Python_Project\tt-trading-mcp && .venv\Scripts\python -c "from config import FUNDAMENTALS_STALE_DAYS, NEWS_MOVER_PCT_THRESHOLD, NEWS_MOVER_VOLRATIO_THRESHOLD, OHLCV_BATCH_SIZE; print(f'stale={FUNDAMENTALS_STALE_DAYS}, pct={NEWS_MOVER_PCT_THRESHOLD}, vol={NEWS_MOVER_VOLRATIO_THRESHOLD}, batch={OHLCV_BATCH_SIZE}')"`

Expected: `stale=7, pct=5.0, vol=2.0, batch=200`

- [ ] **Step 3: Commit**

```bash
git add config.py
git commit -m "feat: add config for stale days, mover thresholds, batch size"
```

---

### Task 2: DB schema migration

**Files:**
- Modify: `db/init.py`
- Create: `db/migrate.py`

- [ ] **Step 1: Update stocks_meta CREATE TABLE in init.py**

Replace the `stocks_meta` CREATE TABLE block in `db/init.py` with:

```python
    con.execute("""
        CREATE TABLE IF NOT EXISTS stocks_meta (
            ticker      VARCHAR PRIMARY KEY,
            company_name VARCHAR,
            exchange    VARCHAR,
            sector      VARCHAR,
            industry    VARCHAR,
            market      VARCHAR DEFAULT 'US',
            tier        VARCHAR DEFAULT 'core'
        )
    """)
```

- [ ] **Step 2: Create migration script for existing DBs**

Create `db/migrate.py`:

```python
"""
Run once to add market/tier columns to an existing stocks_meta table.
Safe to run multiple times — uses IF NOT EXISTS / try-except.
Usage: python -m db.migrate
"""
from db.init import get_conn


def migrate():
    con = get_conn()
    for col, typedef in [("market", "VARCHAR DEFAULT 'US'"), ("tier", "VARCHAR DEFAULT 'core'")]:
        try:
            con.execute(f"ALTER TABLE stocks_meta ADD COLUMN {col} {typedef}")
            print(f"  + Added column stocks_meta.{col}")
        except Exception:
            print(f"  . Column stocks_meta.{col} already exists")
    con.close()
    print("✓ Migration complete.")


if __name__ == "__main__":
    migrate()
```

- [ ] **Step 3: Run migration against existing DB**

Run: `.venv\Scripts\python -m db.migrate`

Expected: Either "Added column" or "already exists" for each column, then "Migration complete."

- [ ] **Step 4: Commit**

```bash
git add db/init.py db/migrate.py
git commit -m "feat: add market and tier columns to stocks_meta"
```

---

### Task 3: Provider ABC interfaces

**Files:**
- Create: `data_sources/__init__.py`
- Create: `data_sources/base.py`
- Create: `tests/__init__.py`
- Create: `tests/test_providers.py`

- [ ] **Step 1: Write failing test for provider interface contracts**

Create `tests/__init__.py` (empty file).

Create `tests/test_providers.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_providers.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'data_sources'`

- [ ] **Step 3: Implement provider ABCs**

Create `data_sources/__init__.py` (empty file).

Create `data_sources/base.py`:

```python
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
        """Fetch fundamental metrics for a single ticker.
        Returns dict with keys: pe_ratio, ps_ratio, pb_ratio, peg_ratio,
        market_cap, revenue_growth_yoy, earnings_growth_yoy, gross_margin,
        roe, fcf_yield, company_name, sector, industry, exchange."""
        ...

class NewsProvider(ABC):
    @abstractmethod
    def fetch_news(self, ticker: str, days: int = 3) -> list[dict]:
        """Fetch recent news for a single ticker.
        Returns list of dicts with keys: id, headline, summary, source, url,
        published_at, sentiment_label."""
        ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_providers.py -v`

Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add data_sources/ tests/
git commit -m "feat: add provider ABC interfaces (OHLCV, Fundamentals, News)"
```

---

### Task 4: US OHLCV Provider (yfinance batch)

**Files:**
- Create: `data_sources/us_market.py`
- Modify: `tests/test_providers.py`

- [ ] **Step 1: Write failing test for batch OHLCV**

Append to `tests/test_providers.py`:

```python
from unittest.mock import patch, MagicMock
import pandas as pd
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_providers.py::test_yfinance_ohlcv_batch_download -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'data_sources.us_market'`

- [ ] **Step 3: Implement YFinanceOHLCVProvider**

Create `data_sources/us_market.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_providers.py -k "yfinance" -v`

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add data_sources/us_market.py tests/test_providers.py
git commit -m "feat: implement YFinanceOHLCVProvider with batch download"
```

---

### Task 5: US Fundamentals + News Providers (Finnhub)

**Files:**
- Modify: `data_sources/us_market.py`
- Modify: `tests/test_providers.py`

- [ ] **Step 1: Write failing tests for Finnhub providers**

Append to `tests/test_providers.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_providers.py -k "finnhub" -v`

Expected: FAIL — `ImportError: cannot import name 'FinnhubFundamentalsProvider'`

- [ ] **Step 3: Implement Finnhub providers**

Append to `data_sources/us_market.py`:

```python
import time
import requests
from datetime import datetime


FINNHUB_BASE = "https://finnhub.io/api/v1"
RATE_LIMIT_SLEEP = 1.1


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_providers.py -k "finnhub" -v`

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add data_sources/us_market.py tests/test_providers.py
git commit -m "feat: implement Finnhub fundamentals and news providers"
```

---

### Task 6: Provider Registry

**Files:**
- Create: `data_sources/registry.py`
- Create: `tests/test_registry.py`

- [ ] **Step 1: Write failing test for registry routing**

Create `tests/test_registry.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_registry.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'data_sources.registry'`

- [ ] **Step 3: Implement registry**

Create `data_sources/registry.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_registry.py -v`

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add data_sources/registry.py tests/test_registry.py
git commit -m "feat: implement provider registry with market-based routing"
```

---

### Task 7: Rewrite db/fetch.py to use providers

**Files:**
- Modify: `db/fetch.py`

This task rewrites `fetch_ohlcv`, `fetch_fundamentals`, `fetch_news` to delegate to providers. `_calc_indicators` stays as a shared utility. `_finnhub` and `RATE_LIMIT_SLEEP` constants move to `data_sources/us_market.py` (already there from Task 5).

- [ ] **Step 1: Rewrite db/fetch.py**

Replace the entire contents of `db/fetch.py` with:

```python
"""
First-time data fetch using provider abstraction.
Designed to run once during setup. Progress is printed to console.
"""
from datetime import datetime, date

import pandas as pd
import pandas_ta as ta

from config import DB_PATH, FINNHUB_KEY, NEWS_DAYS, OHLCV_BATCH_SIZE
from db.init import get_conn
from data_sources.registry import ProviderRegistry


def _calc_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values("date").reset_index(drop=True)
    if len(df) < 20:
        return df
    df["ma_20"]      = ta.sma(df["close"], length=20)
    df["ma_50"]      = ta.sma(df["close"], length=50)
    df["ma_200"]     = ta.sma(df["close"], length=200)
    df["vol_ma_20"]  = ta.sma(df["volume"], length=20)
    df["atr_14"]     = ta.atr(df["high"], df["low"], df["close"], length=14)
    df["rsi_14"]     = ta.rsi(df["close"], length=14)
    df["pct_chg"]    = df["close"].pct_change() * 100
    df["dist_ma20_pct"] = (df["close"] - df["ma_20"]) / df["ma_20"]
    df["dist_ma50_pct"] = (df["close"] - df["ma_50"]) / df["ma_50"]
    df["high_20"]    = df["high"].rolling(20).max().shift(1)
    df["high_55"]    = df["high"].rolling(55).max().shift(1)
    df["vol_ratio"]  = df["volume"] / df["vol_ma_20"].replace(0, 1)
    df["atr_pct"]    = df["atr_14"] / df["close"]
    return df


OHLCV_COLS = [
    "ticker","date","open","high","low","close","volume",
    "ma_20","ma_50","ma_200","vol_ma_20","rsi_14","atr_14",
    "dist_ma20_pct","dist_ma50_pct","high_20","high_55",
    "vol_ratio","atr_pct","pct_chg",
]


def fetch_ohlcv(tickers: list[str]):
    print(f"\n[OHLCV] Fetching {len(tickers)} tickers via providers...")
    registry = ProviderRegistry()
    con = get_conn()

    for i in range(0, len(tickers), OHLCV_BATCH_SIZE):
        batch = tickers[i:i + OHLCV_BATCH_SIZE]
        batch_num = i // OHLCV_BATCH_SIZE + 1
        print(f"  Batch {batch_num}: {len(batch)} tickers...")

        try:
            provider = registry.get_ohlcv_provider("US")
            raw = provider.fetch_ohlcv(batch)
            if raw.empty:
                print(f"  Batch {batch_num}: no data")
                continue

            for ticker in batch:
                sub = raw[raw["ticker"] == ticker].copy()
                if sub.empty:
                    continue
                sub = _calc_indicators(sub)
                sub = sub.dropna(subset=["ma_20"])
                if sub.empty:
                    continue
                sub = sub[OHLCV_COLS]
                con.execute(f"DELETE FROM stock_ohlcv_daily WHERE ticker = ?", [ticker])
                con.execute("INSERT INTO stock_ohlcv_daily SELECT * FROM sub")
                print(f"    {ticker}: {len(sub)} rows")

        except Exception as e:
            print(f"  Batch {batch_num}: ERROR - {e}")

    con.close()
    print("[OHLCV] Done.")


def fetch_fundamentals(tickers: list[str]):
    if not FINNHUB_KEY:
        print("[Fundamentals] Skipped — no FINNHUB_KEY")
        return

    print(f"\n[Fundamentals] Fetching {len(tickers)} tickers via providers...")
    registry = ProviderRegistry()
    con = get_conn()

    for i, ticker in enumerate(tickers, 1):
        try:
            provider = registry.get_fundamentals_provider("US")
            data = provider.fetch_fundamentals(ticker)

            con.execute("""
                INSERT INTO stock_fundamentals (
                    ticker, pe_ratio, ps_ratio, pb_ratio, peg_ratio, market_cap,
                    revenue_growth_yoy, earnings_growth_yoy, gross_margin,
                    roe, fcf_yield, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (ticker) DO UPDATE SET
                    pe_ratio = EXCLUDED.pe_ratio, ps_ratio = EXCLUDED.ps_ratio,
                    pb_ratio = EXCLUDED.pb_ratio, peg_ratio = EXCLUDED.peg_ratio,
                    market_cap = EXCLUDED.market_cap,
                    revenue_growth_yoy = EXCLUDED.revenue_growth_yoy,
                    earnings_growth_yoy = EXCLUDED.earnings_growth_yoy,
                    gross_margin = EXCLUDED.gross_margin,
                    roe = EXCLUDED.roe, fcf_yield = EXCLUDED.fcf_yield,
                    updated_at = EXCLUDED.updated_at
            """, [
                ticker, data.get("pe_ratio"), data.get("ps_ratio"),
                data.get("pb_ratio"), data.get("peg_ratio"), data.get("market_cap"),
                data.get("revenue_growth_yoy"), data.get("earnings_growth_yoy"),
                data.get("gross_margin"), data.get("roe"), data.get("fcf_yield"),
                date.today().isoformat(),
            ])

            con.execute("""
                INSERT INTO stocks_meta (ticker, company_name, exchange, sector, industry)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (ticker) DO UPDATE SET
                    company_name = EXCLUDED.company_name,
                    sector = EXCLUDED.sector,
                    industry = EXCLUDED.industry
            """, [
                ticker, data.get("company_name", ""), data.get("exchange", ""),
                data.get("sector", ""), data.get("industry", ""),
            ])

            print(f"  [{i}/{len(tickers)}] {ticker}: OK")
        except Exception as e:
            print(f"  [{i}/{len(tickers)}] {ticker}: ERROR - {e}")

    con.close()
    print("[Fundamentals] Done.")


def fetch_news(tickers: list[str], days: int = NEWS_DAYS):
    if not FINNHUB_KEY:
        print("[News] Skipped — no FINNHUB_KEY")
        return

    print(f"\n[News] Fetching {len(tickers)} tickers ({days} days)...")
    registry = ProviderRegistry()
    con = get_conn()

    for i, ticker in enumerate(tickers, 1):
        try:
            provider = registry.get_news_provider("US")
            articles = provider.fetch_news(ticker, days=days)

            count = 0
            for art in articles:
                con.execute("""
                    INSERT INTO news (id, ticker, headline, summary, source, url, published_at, sentiment_label)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (id) DO NOTHING
                """, [
                    art["id"], ticker, art["headline"], art["summary"],
                    art["source"], art["url"], art["published_at"], art["sentiment_label"],
                ])
                count += 1

            print(f"  [{i}/{len(tickers)}] {ticker}: {count} articles")
        except Exception as e:
            print(f"  [{i}/{len(tickers)}] {ticker}: ERROR - {e}")

    con.close()
    print("[News] Done.")


def fetch_all(tickers: list[str]):
    start = datetime.now()
    fetch_ohlcv(tickers)
    fetch_fundamentals(tickers)
    fetch_news(tickers)
    elapsed = datetime.now() - start
    print(f"\n✓ All data fetched in {elapsed}. Ready to use!")
```

- [ ] **Step 2: Verify import works**

Run: `.venv\Scripts\python -c "from db.fetch import _calc_indicators, fetch_all; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add db/fetch.py
git commit -m "refactor: rewrite db/fetch.py to use provider abstraction"
```

---

### Task 8: Rewrite db/update.py with batch OHLCV + tier-gated news

**Files:**
- Modify: `db/update.py`
- Create: `tests/test_update.py`

- [ ] **Step 1: Write failing test for mover detection**

Create `tests/test_update.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_update.py -v`

Expected: FAIL — `ImportError: cannot import name 'find_movers' from 'db.update'`

- [ ] **Step 3: Rewrite db/update.py**

Replace entire contents of `db/update.py` with:

```python
"""
Daily incremental update with tier-based news gating.
  - OHLCV: batch download via provider, all tickers
  - Fundamentals: 7-day stale check, all tickers
  - News: core tickers always + universe movers only

Usage: python -m db.update
"""
from datetime import datetime, date, timedelta

import pandas as pd

from config import (
    DB_PATH, FINNHUB_KEY, OHLCV_BATCH_SIZE,
    FUNDAMENTALS_STALE_DAYS, NEWS_MOVER_PCT_THRESHOLD,
    NEWS_MOVER_VOLRATIO_THRESHOLD, load_tickers,
)
from db.init import get_conn
from db.fetch import _calc_indicators, OHLCV_COLS
from data_sources.registry import ProviderRegistry


def find_movers(
    rows: list[dict],
    pct_threshold: float = 5.0,
    vol_threshold: float = 2.0,
) -> list[str]:
    movers = []
    for r in rows:
        pct = r.get("pct_chg")
        vol = r.get("vol_ratio")
        if (pct is not None and abs(pct) > pct_threshold) or \
           (vol is not None and vol > vol_threshold):
            movers.append(r["ticker"])
    return movers


def _load_all_tickers(con) -> list[dict]:
    rows = con.execute("""
        SELECT DISTINCT
            COALESCE(m.ticker, w.ticker) AS ticker,
            COALESCE(m.market, 'US') AS market,
            CASE WHEN w.ticker IS NOT NULL THEN 'core' ELSE COALESCE(m.tier, 'universe') END AS tier
        FROM stocks_meta m
        FULL OUTER JOIN user_watchlist w ON m.ticker = w.ticker
        ORDER BY ticker
    """).fetchall()
    return [{"ticker": r[0], "market": r[1], "tier": r[2]} for r in rows]


def update_ohlcv(tickers: list[str], registry: ProviderRegistry):
    print(f"[OHLCV] Updating {len(tickers)} tickers in batches of {OHLCV_BATCH_SIZE}...")
    con = get_conn()

    for i in range(0, len(tickers), OHLCV_BATCH_SIZE):
        batch = tickers[i:i + OHLCV_BATCH_SIZE]
        batch_num = i // OHLCV_BATCH_SIZE + 1

        try:
            last_dates = {}
            for ticker in batch:
                row = con.execute(
                    "SELECT MAX(date) FROM stock_ohlcv_daily WHERE ticker = ?", [ticker]
                ).fetchone()
                if row and row[0]:
                    last_dates[ticker] = row[0]

            earliest = min(last_dates.values()) if last_dates else None
            provider = registry.get_ohlcv_provider("US")
            raw = provider.fetch_ohlcv(batch, since=earliest)

            if raw.empty:
                print(f"  Batch {batch_num}: no data")
                continue

            inserted = 0
            for ticker in batch:
                sub = raw[raw["ticker"] == ticker].copy()
                if sub.empty:
                    continue
                sub = _calc_indicators(sub)
                sub = sub.dropna(subset=["ma_20"])
                if sub.empty:
                    continue

                last_date = last_dates.get(ticker)
                if last_date:
                    sub = sub[sub["date"].astype(str) > str(last_date)]
                if sub.empty:
                    continue

                sub = sub[OHLCV_COLS]
                con.execute("INSERT OR REPLACE INTO stock_ohlcv_daily SELECT * FROM sub")
                inserted += len(sub)

            print(f"  Batch {batch_num}: {len(batch)} tickers, +{inserted} rows")
        except Exception as e:
            print(f"  Batch {batch_num}: ERROR - {e}")

    con.close()
    print("[OHLCV] Done.")


def update_fundamentals(tickers: list[str], registry: ProviderRegistry):
    if not FINNHUB_KEY:
        print("[Fundamentals] Skipped — no FINNHUB_KEY")
        return

    print(f"[Fundamentals] Refreshing (stale > {FUNDAMENTALS_STALE_DAYS} days)...")
    con = get_conn()

    for i, ticker in enumerate(tickers, 1):
        try:
            row = con.execute(
                "SELECT updated_at FROM stock_fundamentals WHERE ticker = ?", [ticker]
            ).fetchone()
            if row and row[0]:
                try:
                    last_update = date.fromisoformat(str(row[0])[:10])
                    if (date.today() - last_update).days < FUNDAMENTALS_STALE_DAYS:
                        continue
                except (ValueError, TypeError):
                    pass

            provider = registry.get_fundamentals_provider("US")
            data = provider.fetch_fundamentals(ticker)

            con.execute("""
                INSERT INTO stock_fundamentals (
                    ticker, pe_ratio, ps_ratio, pb_ratio, peg_ratio, market_cap,
                    revenue_growth_yoy, earnings_growth_yoy, gross_margin,
                    roe, fcf_yield, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (ticker) DO UPDATE SET
                    pe_ratio = EXCLUDED.pe_ratio, ps_ratio = EXCLUDED.ps_ratio,
                    pb_ratio = EXCLUDED.pb_ratio, peg_ratio = EXCLUDED.peg_ratio,
                    market_cap = EXCLUDED.market_cap,
                    revenue_growth_yoy = EXCLUDED.revenue_growth_yoy,
                    earnings_growth_yoy = EXCLUDED.earnings_growth_yoy,
                    gross_margin = EXCLUDED.gross_margin,
                    roe = EXCLUDED.roe, fcf_yield = EXCLUDED.fcf_yield,
                    updated_at = EXCLUDED.updated_at
            """, [
                ticker, data.get("pe_ratio"), data.get("ps_ratio"),
                data.get("pb_ratio"), data.get("peg_ratio"), data.get("market_cap"),
                data.get("revenue_growth_yoy"), data.get("earnings_growth_yoy"),
                data.get("gross_margin"), data.get("roe"), data.get("fcf_yield"),
                date.today().isoformat(),
            ])

            con.execute("""
                INSERT INTO stocks_meta (ticker, company_name, exchange, sector, industry)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (ticker) DO UPDATE SET
                    company_name = EXCLUDED.company_name,
                    sector = EXCLUDED.sector,
                    industry = EXCLUDED.industry
            """, [
                ticker, data.get("company_name", ""), data.get("exchange", ""),
                data.get("sector", ""), data.get("industry", ""),
            ])

            print(f"  [{i}/{len(tickers)}] {ticker}: OK")
        except Exception as e:
            print(f"  [{i}/{len(tickers)}] {ticker}: ERROR - {e}")

    con.close()
    print("[Fundamentals] Done.")


def update_news(tickers: list[str], registry: ProviderRegistry, days: int = 3):
    if not FINNHUB_KEY:
        print("[News] Skipped — no FINNHUB_KEY")
        return

    print(f"[News] Fetching {len(tickers)} tickers ({days} days)...")
    con = get_conn()

    for i, ticker in enumerate(tickers, 1):
        try:
            provider = registry.get_news_provider("US")
            articles = provider.fetch_news(ticker, days=days)

            count = 0
            for art in articles:
                con.execute("""
                    INSERT INTO news (id, ticker, headline, summary, source, url, published_at, sentiment_label)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (id) DO NOTHING
                """, [
                    art["id"], ticker, art["headline"], art["summary"],
                    art["source"], art["url"], art["published_at"], art["sentiment_label"],
                ])
                count += 1

            print(f"  [{i}/{len(tickers)}] {ticker}: +{count}")
        except Exception as e:
            print(f"  [{i}/{len(tickers)}] {ticker}: ERROR - {e}")

    con.close()
    print("[News] Done.")


def run():
    start = datetime.now()
    registry = ProviderRegistry()
    con = get_conn()

    all_tickers = _load_all_tickers(con)
    if not all_tickers:
        fallback = load_tickers()
        all_tickers = [{"ticker": t, "market": "US", "tier": "core"} for t in fallback]
    if not all_tickers:
        print("No tickers found — nothing to update.")
        return

    all_ticker_names = [t["ticker"] for t in all_tickers]
    core_tickers = [t["ticker"] for t in all_tickers if t["tier"] == "core"]

    print(f"\n[Update] {date.today()} — {len(all_tickers)} tickers ({len(core_tickers)} core)")
    print("=" * 55)

    # 1. OHLCV: all tickers, batch
    update_ohlcv(all_ticker_names, registry)

    # 2. Fundamentals: all tickers, stale check
    update_fundamentals(all_ticker_names, registry)

    # 3. News: core always + universe movers
    today_ohlcv = con.execute("""
        SELECT ticker, pct_chg, vol_ratio
        FROM stock_ohlcv_daily
        WHERE date = (SELECT MAX(date) FROM stock_ohlcv_daily)
    """).fetchall()
    con.close()

    universe_rows = [
        {"ticker": r[0], "pct_chg": r[1], "vol_ratio": r[2]}
        for r in today_ohlcv
        if r[0] not in set(core_tickers)
    ]
    movers = find_movers(universe_rows, NEWS_MOVER_PCT_THRESHOLD, NEWS_MOVER_VOLRATIO_THRESHOLD)
    news_tickers = list(set(core_tickers + movers))
    print(f"[News] {len(core_tickers)} core + {len(movers)} movers = {len(news_tickers)} total")
    update_news(news_tickers, registry)

    elapsed = datetime.now() - start
    print(f"\n✓ Update complete in {elapsed}.")


if __name__ == "__main__":
    run()
```

- [ ] **Step 4: Run mover tests**

Run: `.venv\Scripts\python -m pytest tests/test_update.py -v`

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add db/update.py tests/test_update.py
git commit -m "refactor: rewrite db/update.py with batch OHLCV + tier-gated news"
```

---

### Task 9: Universe import script

**Files:**
- Create: `db/universe.py`

- [ ] **Step 1: Create universe import script**

Create `db/universe.py`:

```python
"""
Import a ticker universe from CSV into stocks_meta with tier='universe'.
CSV must have at least a 'ticker' column. Optional: 'sector', 'industry'.

Usage:
  python -m db.universe path/to/tickers.csv
  python -m db.universe path/to/tickers.csv --market US
"""
import argparse
import csv
import sys
from datetime import datetime

from db.init import get_conn


def import_universe(csv_path: str, market: str = "US"):
    con = get_conn()
    count = 0

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if "ticker" not in reader.fieldnames:
            print(f"ERROR: CSV must have a 'ticker' column. Found: {reader.fieldnames}")
            sys.exit(1)

        for row in reader:
            ticker = row["ticker"].strip().upper()
            if not ticker:
                continue
            sector = row.get("sector", "").strip()
            industry = row.get("industry", "").strip()

            con.execute("""
                INSERT INTO stocks_meta (ticker, sector, industry, market, tier)
                VALUES (?, ?, ?, ?, 'universe')
                ON CONFLICT (ticker) DO UPDATE SET
                    market = EXCLUDED.market,
                    tier = CASE
                        WHEN stocks_meta.ticker IN (SELECT ticker FROM user_watchlist)
                        THEN 'core'
                        ELSE 'universe'
                    END,
                    sector = CASE WHEN EXCLUDED.sector != '' THEN EXCLUDED.sector ELSE stocks_meta.sector END,
                    industry = CASE WHEN EXCLUDED.industry != '' THEN EXCLUDED.industry ELSE stocks_meta.industry END
            """, [ticker, sector, industry, market])
            count += 1

    con.close()
    print(f"✓ Imported {count} tickers as '{market}' universe.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import ticker universe from CSV")
    parser.add_argument("csv_path", help="Path to CSV file with 'ticker' column")
    parser.add_argument("--market", default="US", help="Market code (default: US)")
    args = parser.parse_args()
    import_universe(args.csv_path, args.market)
```

- [ ] **Step 2: Verify it runs with --help**

Run: `.venv\Scripts\python -m db.universe --help`

Expected: Shows usage message with `csv_path` and `--market` arguments.

- [ ] **Step 3: Commit**

```bash
git add db/universe.py
git commit -m "feat: add CSV-based universe import script"
```

---

### Task 10: Web screener endpoint — POST /screen

**Files:**
- Modify: `web/app.py`
- Create: `tests/test_screen.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_screen.py`:

```python
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock


@pytest.fixture
def client():
    from web.app import app
    return TestClient(app)


@patch("web.app.get_conn")
def test_screen_returns_list(mock_conn, client):
    mock_con = MagicMock()
    mock_conn.return_value = mock_con

    mock_con.execute.side_effect = [
        MagicMock(fetchone=MagicMock(return_value=("2026-06-16",))),  # max date
        MagicMock(fetchall=MagicMock(return_value=[
            ("AAPL", "Apple Inc", "Technology", 195.0, 1.5, 55.0, 1.2, 0.02, "core"),
        ])),
    ]

    resp = client.post("/screen", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data
    assert data["results"][0]["ticker"] == "AAPL"


@patch("web.app.get_conn")
def test_screen_filters_by_sector(mock_conn, client):
    mock_con = MagicMock()
    mock_conn.return_value = mock_con

    mock_con.execute.side_effect = [
        MagicMock(fetchone=MagicMock(return_value=("2026-06-16",))),
        MagicMock(fetchall=MagicMock(return_value=[])),
    ]

    resp = client.post("/screen", json={"sectors": ["Technology"]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"] == []
    assert data["total"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_screen.py -v`

Expected: FAIL — `starlette.routing: No route for POST /screen`

- [ ] **Step 3: Add POST /screen endpoint to web/app.py**

Add these imports at the top of `web/app.py` (alongside existing imports):

```python
from pydantic import BaseModel
from typing import Optional
```

Add the request model after the existing helpers:

```python
class ScreenRequest(BaseModel):
    sectors: list[str] = []
    above_ma200: bool = False
    above_ma50: bool = False
    bull_alignment: bool = False
    dist_ma20_min: Optional[float] = None
    dist_ma20_max: Optional[float] = None
    rsi_min: Optional[float] = None
    rsi_max: Optional[float] = None
    vol_ratio_min: Optional[float] = None
    near_20d_high: bool = False
    atr_pct_min: Optional[float] = None
    pct_chg_min: Optional[float] = None
    pct_chg_max: Optional[float] = None
    market: str = "US"
    tier: str = "all"
    limit: int = 50
```

Add the endpoint after the existing routes:

```python
@app.post("/screen")
async def screen_stocks(req: ScreenRequest):
    con = get_conn()
    try:
        max_date_row = con.execute("SELECT MAX(date) FROM stock_ohlcv_daily").fetchone()
        if not max_date_row or not max_date_row[0]:
            return JSONResponse({"results": [], "total": 0, "date": None})
        max_date = max_date_row[0]

        where = ["o.date = ?"]
        params: list = [max_date]

        if req.sectors:
            placeholders = ",".join(["?"] * len(req.sectors))
            where.append(f"m.sector IN ({placeholders})")
            params.extend(req.sectors)

        if req.above_ma200:
            where.append("o.close > o.ma_200")
        if req.above_ma50:
            where.append("o.close > o.ma_50")
        if req.bull_alignment:
            where.append("o.ma_20 > o.ma_50 AND o.ma_50 > o.ma_200")

        if req.dist_ma20_min is not None:
            where.append("o.dist_ma20_pct >= ?")
            params.append(req.dist_ma20_min)
        if req.dist_ma20_max is not None:
            where.append("o.dist_ma20_pct <= ?")
            params.append(req.dist_ma20_max)

        if req.rsi_min is not None:
            where.append("o.rsi_14 >= ?")
            params.append(req.rsi_min)
        if req.rsi_max is not None:
            where.append("o.rsi_14 <= ?")
            params.append(req.rsi_max)

        if req.vol_ratio_min is not None:
            where.append("o.vol_ratio >= ?")
            params.append(req.vol_ratio_min)

        if req.near_20d_high:
            where.append("o.high_20 IS NOT NULL AND o.close / o.high_20 >= 0.95")

        if req.atr_pct_min is not None:
            where.append("o.atr_pct >= ?")
            params.append(req.atr_pct_min)

        if req.pct_chg_min is not None:
            where.append("o.pct_chg >= ?")
            params.append(req.pct_chg_min)
        if req.pct_chg_max is not None:
            where.append("o.pct_chg <= ?")
            params.append(req.pct_chg_max)

        if req.market != "all":
            where.append("COALESCE(m.market, 'US') = ?")
            params.append(req.market)

        if req.tier != "all":
            where.append("COALESCE(m.tier, 'core') = ?")
            params.append(req.tier)

        params.append(req.limit)

        sql = f"""
            SELECT
                o.ticker, m.company_name, m.sector,
                o.close, o.pct_chg, o.rsi_14, o.vol_ratio, o.dist_ma20_pct,
                CASE WHEN w.ticker IS NOT NULL THEN true ELSE false END AS is_core
            FROM stock_ohlcv_daily o
            LEFT JOIN stocks_meta m ON o.ticker = m.ticker
            LEFT JOIN user_watchlist w ON o.ticker = w.ticker
            WHERE {' AND '.join(where)}
            ORDER BY o.vol_ratio DESC NULLS LAST
            LIMIT ?
        """

        rows = con.execute(sql, params).fetchall()
        cols = ["ticker","company_name","sector","close","pct_chg","rsi_14","vol_ratio","dist_ma20_pct","is_core"]
        results = [dict(zip(cols, r)) for r in rows]
        return JSONResponse({"results": results, "total": len(results), "date": str(max_date)})
    finally:
        con.close()
```

- [ ] **Step 4: Run tests**

Run: `.venv\Scripts\python -m pytest tests/test_screen.py -v`

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add web/app.py tests/test_screen.py
git commit -m "feat: add POST /screen endpoint for sector + technical screening"
```

---

### Task 11: Web ticker detail endpoint — GET /ticker/{ticker}

**Files:**
- Modify: `web/app.py`
- Create: `tests/test_ticker_detail.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_ticker_detail.py`:

```python
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from datetime import date


@pytest.fixture
def client():
    from web.app import app
    return TestClient(app)


@patch("web.app.get_conn")
def test_ticker_detail_from_cache(mock_conn, client):
    mock_con = MagicMock()
    mock_conn.return_value = mock_con

    fund_row = (28.5, 7.2, 45.0, 1.5, 3000000, 0.125, 0.15, 0.46, 160.0, 3.5, date.today().isoformat())
    news_rows = [("Test headline", "summary", "src", "2026-06-16T10:00:00", "positive")]
    meta_row = ("Apple Inc", "Technology", "Semiconductors")

    mock_con.execute.side_effect = [
        MagicMock(fetchone=MagicMock(return_value=fund_row)),  # fundamentals
        MagicMock(fetchall=MagicMock(return_value=news_rows)),  # news
        MagicMock(fetchone=MagicMock(return_value=meta_row)),   # meta
    ]

    resp = client.get("/ticker/AAPL")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ticker"] == "AAPL"
    assert data["fundamentals"]["pe_ratio"] == 28.5
    assert len(data["news"]) == 1


@patch("web.app.get_conn")
def test_ticker_detail_no_data(mock_conn, client):
    mock_con = MagicMock()
    mock_conn.return_value = mock_con
    mock_con.execute.side_effect = [
        MagicMock(fetchone=MagicMock(return_value=None)),  # no fundamentals
        MagicMock(fetchall=MagicMock(return_value=[])),     # no news
        MagicMock(fetchone=MagicMock(return_value=None)),   # no meta
    ]

    resp = client.get("/ticker/FAKE")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ticker"] == "FAKE"
    assert data["fundamentals"] is None
    assert data["news"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_ticker_detail.py -v`

Expected: FAIL — `starlette.routing: No route for GET /ticker/AAPL`

- [ ] **Step 3: Add GET /ticker/{ticker} endpoint to web/app.py**

Append after the `/screen` endpoint in `web/app.py`:

```python
@app.get("/ticker/{ticker}")
async def ticker_detail(ticker: str):
    ticker = ticker.strip().upper()
    con = get_conn()
    try:
        fund_row = con.execute("""
            SELECT pe_ratio, ps_ratio, pb_ratio, peg_ratio, market_cap,
                   revenue_growth_yoy, earnings_growth_yoy, gross_margin,
                   roe, fcf_yield, updated_at
            FROM stock_fundamentals
            WHERE ticker = ? AND updated_at >= CURRENT_DATE - INTERVAL '7 days'
        """, [ticker]).fetchone()

        fundamentals = None
        if fund_row:
            fund_cols = ["pe_ratio","ps_ratio","pb_ratio","peg_ratio","market_cap",
                         "revenue_growth_yoy","earnings_growth_yoy","gross_margin",
                         "roe","fcf_yield","updated_at"]
            fundamentals = dict(zip(fund_cols, fund_row))
        else:
            try:
                from data_sources.registry import ProviderRegistry
                registry = ProviderRegistry()
                provider = registry.get_fundamentals_provider("US")
                data = provider.fetch_fundamentals(ticker)
                con.execute("""
                    INSERT INTO stock_fundamentals (
                        ticker, pe_ratio, ps_ratio, pb_ratio, peg_ratio, market_cap,
                        revenue_growth_yoy, earnings_growth_yoy, gross_margin,
                        roe, fcf_yield, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_DATE)
                    ON CONFLICT (ticker) DO UPDATE SET
                        pe_ratio=EXCLUDED.pe_ratio, ps_ratio=EXCLUDED.ps_ratio,
                        pb_ratio=EXCLUDED.pb_ratio, peg_ratio=EXCLUDED.peg_ratio,
                        market_cap=EXCLUDED.market_cap,
                        revenue_growth_yoy=EXCLUDED.revenue_growth_yoy,
                        earnings_growth_yoy=EXCLUDED.earnings_growth_yoy,
                        gross_margin=EXCLUDED.gross_margin,
                        roe=EXCLUDED.roe, fcf_yield=EXCLUDED.fcf_yield,
                        updated_at=EXCLUDED.updated_at
                """, [
                    ticker, data.get("pe_ratio"), data.get("ps_ratio"),
                    data.get("pb_ratio"), data.get("peg_ratio"), data.get("market_cap"),
                    data.get("revenue_growth_yoy"), data.get("earnings_growth_yoy"),
                    data.get("gross_margin"), data.get("roe"), data.get("fcf_yield"),
                ])
                fundamentals = {k: v for k, v in data.items()
                                if k in ["pe_ratio","ps_ratio","pb_ratio","peg_ratio","market_cap",
                                         "revenue_growth_yoy","earnings_growth_yoy","gross_margin",
                                         "roe","fcf_yield"]}
            except Exception:
                pass

        news_rows = con.execute("""
            SELECT headline, summary, source, published_at, sentiment_label
            FROM news
            WHERE ticker = ? AND published_at >= CURRENT_TIMESTAMP - INTERVAL '14 days'
            ORDER BY published_at DESC LIMIT 20
        """, [ticker]).fetchall()

        news = []
        if news_rows:
            news_cols = ["headline","summary","source","published_at","sentiment_label"]
            news = [dict(zip(news_cols, r)) for r in news_rows]
        else:
            try:
                from data_sources.registry import ProviderRegistry
                registry = ProviderRegistry()
                provider = registry.get_news_provider("US")
                articles = provider.fetch_news(ticker, days=14)
                for art in articles:
                    con.execute("""
                        INSERT INTO news (id, ticker, headline, summary, source, url, published_at, sentiment_label)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (id) DO NOTHING
                    """, [art["id"], ticker, art["headline"], art["summary"],
                          art["source"], art["url"], art["published_at"], art["sentiment_label"]])
                news_cols = ["headline","summary","source","published_at","sentiment_label"]
                news = [{k: art.get(k) for k in news_cols} for art in articles[:20]]
            except Exception:
                pass

        meta_row = con.execute(
            "SELECT company_name, sector, industry FROM stocks_meta WHERE ticker = ?", [ticker]
        ).fetchone()

        return JSONResponse({
            "ticker": ticker,
            "company_name": meta_row[0] if meta_row else "",
            "sector": meta_row[1] if meta_row else "",
            "industry": meta_row[2] if meta_row else "",
            "fundamentals": fundamentals,
            "news": news,
        }, default=str)

    finally:
        con.close()
```

Note: `JSONResponse` needs `default=str` to handle `date` objects. FastAPI's `JSONResponse` doesn't accept `default` — use `json.dumps` instead. Replace the final return with:

```python
        import json
        return JSONResponse(content=json.loads(json.dumps({
            "ticker": ticker,
            "company_name": meta_row[0] if meta_row else "",
            "sector": meta_row[1] if meta_row else "",
            "industry": meta_row[2] if meta_row else "",
            "fundamentals": fundamentals,
            "news": news,
        }, default=str)))
```

- [ ] **Step 4: Run tests**

Run: `.venv\Scripts\python -m pytest tests/test_ticker_detail.py -v`

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add web/app.py tests/test_ticker_detail.py
git commit -m "feat: add GET /ticker/{ticker} with cache-first Finnhub fallback"
```

---

### Task 12: Add pytest to requirements + run full test suite

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add pytest to requirements.txt**

Append `pytest>=7.0.0` to `requirements.txt`.

- [ ] **Step 2: Install**

Run: `.venv\Scripts\pip install pytest`

- [ ] **Step 3: Run full test suite**

Run: `.venv\Scripts\python -m pytest tests/ -v`

Expected: All tests pass (approximately 14 tests across 4 test files).

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "chore: add pytest to requirements"
```

---

### Task 13: Windows daily update script + README section

**Files:**
- Create: `db/run_daily_update.bat`
- Modify: `README.md` (add scheduling section)

- [ ] **Step 1: Create batch wrapper**

Create `db/run_daily_update.bat`:

```batch
@echo off
REM Daily data update for tt-trading-mcp
REM Schedule with: schtasks /create /tn "tt-trading-update" /tr "%~dp0run_daily_update.bat" /sc daily /st 05:00

cd /d "%~dp0.."
call .venv\Scripts\activate.bat
python -m db.update
```

- [ ] **Step 2: Verify bat file syntax**

Run: `type "D:\Dev_project\Python_Project\tt-trading-mcp\db\run_daily_update.bat"`

Expected: Shows the batch file contents without errors.

- [ ] **Step 3: Commit**

```bash
git add db/run_daily_update.bat
git commit -m "feat: add Windows daily update batch script"
```

---

### Task 14: Update CHANGELOG.md

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add changelog entry**

Prepend to `CHANGELOG.md`:

```markdown
### [2026-06-17] 数据管道架构重设计
* **新增 (Added)**: Provider 抽象层 (`data_sources/base.py`, `data_sources/us_market.py`, `data_sources/registry.py`)，支持按市场字段插拔数据源
* **新增 (Added)**: `stocks_meta` 表新增 `market` 和 `tier` 字段，支持 core/universe 分层
* **新增 (Added)**: CSV 股票池导入脚本 (`db/universe.py`)，支持导入数千支 ticker
* **新增 (Added)**: Web 筛选端点 `POST /screen`（按板块+技术条件本地 DB 筛选）和 `GET /ticker/{ticker}`（cache-first 详情）
* **新增 (Added)**: Windows 每日更新批处理脚本 (`db/run_daily_update.bat`)
* **修改 (Changed)**: `db/update.py` 重写为批量 OHLCV 下载 + 7 天 fundamentals 缓存 + tier 门槛新闻拉取
* **修改 (Changed)**: `db/fetch.py` 重写为基于 Provider 抽象层的初始数据拉取
* **修改 (Changed)**: `config.py` 新增 `FUNDAMENTALS_STALE_DAYS`、`NEWS_MOVER_PCT_THRESHOLD`、`NEWS_MOVER_VOLRATIO_THRESHOLD`、`OHLCV_BATCH_SIZE` 配置项
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: add changelog for data pipeline redesign"
```
