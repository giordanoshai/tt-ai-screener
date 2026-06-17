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
