"""
Daily incremental update:
  - OHLCV: fetch only missing days since last record
  - Fundamentals: refresh all tickers
  - News: fetch last 3 days (overlap to avoid gaps)

Can be run manually or scheduled (cron / task scheduler).
Usage: python db/update.py
"""

import time
from datetime import datetime, date, timedelta

import pandas as pd
import pandas_ta as ta
import yfinance as yf

from config import DB_PATH, FINNHUB_KEY, load_tickers
from db.init import get_conn
from db.fetch import _calc_indicators, _finnhub, RATE_LIMIT_SLEEP


def update_ohlcv(tickers: list[str]):
    print(f"[OHLCV] Updating {len(tickers)} tickers...")
    con = get_conn()

    for i, ticker in enumerate(tickers, 1):
        try:
            row = con.execute(
                "SELECT MAX(date) FROM stock_ohlcv_daily WHERE ticker = ?", [ticker]
            ).fetchone()
            last_date = row[0] if row and row[0] else None

            if last_date and last_date >= date.today():
                print(f"  [{i}/{len(tickers)}] {ticker}: already up to date")
                continue

            # Fetch from last known date (or 2 years if no data)
            if last_date:
                start = (last_date - timedelta(days=30)).isoformat()  # 30-day overlap for indicator recalc
                raw = yf.download(ticker, start=start, auto_adjust=True, progress=False)
            else:
                raw = yf.download(ticker, period="2y", auto_adjust=True, progress=False)

            if raw.empty:
                print(f"  [{i}/{len(tickers)}] {ticker}: no data")
                continue

            df = raw.reset_index()
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0].lower() for c in df.columns]
            else:
                df.columns = [c.lower() for c in df.columns]

            df["ticker"] = ticker
            df["volume"] = df["volume"].astype("int64")
            df = _calc_indicators(df)
            df = df.dropna(subset=["ma_20"])

            # Only insert rows newer than last_date
            if last_date:
                df = df[df["date"].astype(str) > str(last_date)]

            if df.empty:
                print(f"  [{i}/{len(tickers)}] {ticker}: no new rows")
                continue

            cols = ["ticker","date","open","high","low","close","volume",
                    "ma_20","ma_50","ma_200","vol_ma_20","rsi_14","atr_14",
                    "dist_ma20_pct","dist_ma50_pct","high_20","high_55",
                    "vol_ratio","atr_pct","pct_chg"]
            df = df[cols]

            con.execute("INSERT OR REPLACE INTO stock_ohlcv_daily SELECT * FROM df")
            print(f"  [{i}/{len(tickers)}] {ticker}: +{len(df)} new rows")

        except Exception as e:
            print(f"  [{i}/{len(tickers)}] {ticker}: ERROR - {e}")

    con.close()
    print("[OHLCV] Done.")


FUNDAMENTALS_STALE_DAYS = 5  # only refresh if data is older than this


def update_fundamentals(tickers: list[str]):
    if not FINNHUB_KEY:
        print("[Fundamentals] Skipped — no FINNHUB_KEY")
        return

    print(f"[Fundamentals] Refreshing {len(tickers)} tickers (stale > {FUNDAMENTALS_STALE_DAYS} days)...")
    con = get_conn()

    for i, ticker in enumerate(tickers, 1):
        try:
            # Skip if updated recently — fundamentals change quarterly
            row = con.execute(
                "SELECT updated_at FROM stock_fundamentals WHERE ticker = ?", [ticker]
            ).fetchone()
            if row and row[0]:
                try:
                    last_update = date.fromisoformat(str(row[0])[:10])
                    if (date.today() - last_update).days < FUNDAMENTALS_STALE_DAYS:
                        print(f"  [{i}/{len(tickers)}] {ticker}: fresh (updated {last_update}), skipped")
                        continue
                except (ValueError, TypeError):
                    pass

            data = _finnhub("/stock/metric", {"symbol": ticker, "metric": "all"})
            m = data.get("metric", {})
            time.sleep(RATE_LIMIT_SLEEP)

            def g(key):
                v = m.get(key)
                return float(v) if v is not None else None

            def gp(key):
                v = m.get(key)
                return float(v) / 100.0 if v is not None else None

            con.execute("""
                INSERT INTO stock_fundamentals (
                    ticker, pe_ratio, ps_ratio, pb_ratio, peg_ratio, market_cap,
                    revenue_growth_yoy, earnings_growth_yoy, gross_margin,
                    roe, fcf_yield, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (ticker) DO UPDATE SET
                    pe_ratio            = EXCLUDED.pe_ratio,
                    ps_ratio            = EXCLUDED.ps_ratio,
                    pb_ratio            = EXCLUDED.pb_ratio,
                    peg_ratio           = EXCLUDED.peg_ratio,
                    market_cap          = EXCLUDED.market_cap,
                    revenue_growth_yoy  = EXCLUDED.revenue_growth_yoy,
                    earnings_growth_yoy = EXCLUDED.earnings_growth_yoy,
                    gross_margin        = EXCLUDED.gross_margin,
                    roe                 = EXCLUDED.roe,
                    fcf_yield           = EXCLUDED.fcf_yield,
                    updated_at          = EXCLUDED.updated_at
            """, [
                ticker,
                g("peExclExtraTTM"), g("psTTM"), g("pbQuarterly"), g("pegAnnual"),
                g("marketCapitalization"),
                gp("revenueGrowthTTMYoy"), gp("epsGrowthTTMYoy"), gp("grossMarginTTM"),
                gp("roeTTM"), gp("fcfYieldTTM"),
                date.today().isoformat(),
            ])

            print(f"  [{i}/{len(tickers)}] {ticker}: OK")

        except Exception as e:
            print(f"  [{i}/{len(tickers)}] {ticker}: ERROR - {e}")
        finally:
            time.sleep(RATE_LIMIT_SLEEP)

    con.close()
    print("[Fundamentals] Done.")


def update_news(tickers: list[str], days: int = 3):
    if not FINNHUB_KEY:
        print("[News] Skipped — no FINNHUB_KEY")
        return

    date_to   = date.today().isoformat()
    date_from = (date.today() - timedelta(days=days)).isoformat()
    print(f"[News] Fetching last {days} days ({date_from} → {date_to})...")
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

            print(f"  [{i}/{len(tickers)}] {ticker}: +{count} articles")

        except Exception as e:
            print(f"  [{i}/{len(tickers)}] {ticker}: ERROR - {e}")
        finally:
            time.sleep(RATE_LIMIT_SLEEP)

    con.close()
    print("[News] Done.")


def run():
    start = datetime.now()
    tickers = load_tickers()
    if not tickers:
        print("No tickers in tickers.txt — nothing to update.")
        return

    print(f"\n[Update] {date.today()} — {len(tickers)} tickers")
    print("=" * 45)

    update_ohlcv(tickers)
    update_fundamentals(tickers)
    update_news(tickers)

    elapsed = datetime.now() - start
    print(f"\n✓ Update complete in {elapsed}.")


if __name__ == "__main__":
    run()
