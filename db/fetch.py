"""
First-time data fetch: OHLCV (yfinance) + Fundamentals & News (Finnhub)
Designed to run once during setup. Progress is printed to console.
"""

import time
import json
import requests
from datetime import datetime, timedelta, date

import pandas as pd
import pandas_ta as ta
import yfinance as yf
import duckdb

from config import DB_PATH, FINNHUB_KEY, NEWS_DAYS
from db.init import get_conn

FINNHUB_BASE = "https://finnhub.io/api/v1"
RATE_LIMIT_SLEEP = 1.1  # seconds between Finnhub calls (max 60/min)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _finnhub(path: str, params: dict) -> dict | list:
    params["token"] = FINNHUB_KEY
    r = requests.get(f"{FINNHUB_BASE}{path}", params=params, timeout=15)
    r.raise_for_status()
    return r.json()


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


# ── OHLCV ─────────────────────────────────────────────────────────────────────

def fetch_ohlcv(tickers: list[str], years: int = 2):
    print(f"\n[OHLCV] Fetching {len(tickers)} tickers ({years}y history) via yfinance...")
    con = get_conn()

    for i, ticker in enumerate(tickers, 1):
        try:
            raw = yf.download(ticker, period=f"{years}y", auto_adjust=True, progress=False)
            if raw.empty:
                print(f"  [{i}/{len(tickers)}] {ticker}: no data")
                continue

            df = raw.reset_index()
            # yfinance returns MultiIndex columns like ('Close', 'AAPL') — flatten them
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0].lower() for c in df.columns]
            else:
                df.columns = [c.lower() for c in df.columns]
            df["ticker"] = ticker
            df["volume"] = df["volume"].astype("int64")

            df = _calc_indicators(df)
            df = df.dropna(subset=["ma_20"])

            cols = ["ticker","date","open","high","low","close","volume",
                    "ma_20","ma_50","ma_200","vol_ma_20","rsi_14","atr_14",
                    "dist_ma20_pct","dist_ma50_pct","high_20","high_55",
                    "vol_ratio","atr_pct","pct_chg"]
            df = df[cols]

            con.execute("DELETE FROM stock_ohlcv_daily WHERE ticker = ?", [ticker])
            con.execute("INSERT INTO stock_ohlcv_daily SELECT * FROM df")
            print(f"  [{i}/{len(tickers)}] {ticker}: {len(df)} rows")

        except Exception as e:
            print(f"  [{i}/{len(tickers)}] {ticker}: ERROR - {e}")

    con.close()
    print("[OHLCV] Done.")


# ── Fundamentals ──────────────────────────────────────────────────────────────

def fetch_fundamentals(tickers: list[str]):
    if not FINNHUB_KEY:
        print("[Fundamentals] Skipped — no FINNHUB_KEY")
        return

    print(f"\n[Fundamentals] Fetching {len(tickers)} tickers via Finnhub...")
    con = get_conn()

    for i, ticker in enumerate(tickers, 1):
        try:
            data = _finnhub("/stock/metric", {"symbol": ticker, "metric": "all"})
            m = data.get("metric", {})
            profile = _finnhub("/stock/profile2", {"symbol": ticker})
            time.sleep(RATE_LIMIT_SLEEP)

            def g(key):
                v = m.get(key)
                return float(v) if v is not None else None

            def gp(key):
                # Finnhub returns growth/margin as percentage (70.68 = 70.68%) → store as decimal (0.7068)
                v = m.get(key)
                return float(v) / 100.0 if v is not None else None

            record = {
                "ticker":               ticker,
                "pe_ratio":             g("peExclExtraTTM"),
                "ps_ratio":             g("psTTM"),
                "pb_ratio":             g("pbQuarterly"),
                "peg_ratio":            g("pegAnnual"),
                "market_cap":           g("marketCapitalization"),
                "revenue_growth_yoy":   gp("revenueGrowthTTMYoy"),
                "earnings_growth_yoy":  gp("epsGrowthTTMYoy"),
                "gross_margin":         gp("grossMarginTTM"),
                "roe":                  g("roeTTM"),
                "fcf_yield":            g("fcfYieldTTM"),
                "analyst_rating":       profile.get("finnhubIndustry"),
                "analyst_target_price": None,
                "analyst_count":        None,
                "next_earnings_date":   None,
                "last_earnings_date":   None,
                "mspr":                 None,
                "updated_at":           date.today().isoformat(),
            }

            con.execute("""
                INSERT INTO stock_fundamentals VALUES (
                    $ticker, $pe_ratio, $ps_ratio, $pb_ratio, $peg_ratio,
                    $market_cap, $revenue_growth_yoy, $earnings_growth_yoy,
                    $gross_margin, $roe, $fcf_yield, $analyst_rating,
                    $analyst_target_price, $analyst_count,
                    $next_earnings_date, $last_earnings_date,
                    $mspr, $updated_at
                )
                ON CONFLICT (ticker) DO UPDATE SET
                    pe_ratio = EXCLUDED.pe_ratio,
                    revenue_growth_yoy = EXCLUDED.revenue_growth_yoy,
                    earnings_growth_yoy = EXCLUDED.earnings_growth_yoy,
                    gross_margin = EXCLUDED.gross_margin,
                    updated_at = EXCLUDED.updated_at
            """, record)

            con.execute("""
                INSERT INTO stocks_meta VALUES ($ticker, $company_name, $exchange, $sector, $industry)
                ON CONFLICT (ticker) DO UPDATE SET
                    company_name = EXCLUDED.company_name
            """, {
                "ticker":       ticker,
                "company_name": profile.get("name", ""),
                "exchange":     profile.get("exchange", ""),
                "sector":       profile.get("finnhubIndustry", ""),
                "industry":     profile.get("finnhubIndustry", ""),
            })

            print(f"  [{i}/{len(tickers)}] {ticker}: OK")

        except Exception as e:
            print(f"  [{i}/{len(tickers)}] {ticker}: ERROR - {e}")
        finally:
            time.sleep(RATE_LIMIT_SLEEP)

    con.close()
    print("[Fundamentals] Done.")


# ── News ──────────────────────────────────────────────────────────────────────

def fetch_news(tickers: list[str], days: int = NEWS_DAYS):
    if not FINNHUB_KEY:
        print("[News] Skipped — no FINNHUB_KEY")
        return

    date_to   = date.today().isoformat()
    date_from = (date.today() - timedelta(days=days)).isoformat()
    print(f"\n[News] Fetching {len(tickers)} tickers ({date_from} → {date_to})...")
    con = get_conn()

    for i, ticker in enumerate(tickers, 1):
        try:
            articles = _finnhub("/company-news", {
                "symbol": ticker,
                "from":   date_from,
                "to":     date_to,
            })
            time.sleep(RATE_LIMIT_SLEEP)

            if not isinstance(articles, list):
                print(f"  [{i}/{len(tickers)}] {ticker}: unexpected response")
                continue

            count = 0
            for art in articles:
                art_id = art.get("id")
                if not art_id:
                    continue
                published = datetime.fromtimestamp(art["datetime"]).isoformat() if art.get("datetime") else None
                con.execute("""
                    INSERT INTO news (id, ticker, headline, summary, source, url, published_at, sentiment_label)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (id) DO NOTHING
                """, [
                    art_id, ticker,
                    art.get("headline", ""),
                    art.get("summary", ""),
                    art.get("source", ""),
                    art.get("url", ""),
                    published,
                    None,
                ])
                count += 1

            print(f"  [{i}/{len(tickers)}] {ticker}: {count} articles")

        except Exception as e:
            print(f"  [{i}/{len(tickers)}] {ticker}: ERROR - {e}")
        finally:
            time.sleep(RATE_LIMIT_SLEEP)

    con.close()
    print("[News] Done.")


# ── Entry point ───────────────────────────────────────────────────────────────

def fetch_all(tickers: list[str]):
    start = datetime.now()
    fetch_ohlcv(tickers)
    fetch_fundamentals(tickers)
    fetch_news(tickers)
    elapsed = datetime.now() - start
    print(f"\n✓ All data fetched in {elapsed}. Ready to use!")
