"""
Daily incremental update with tier-based news gating.
  - OHLCV: batch download via provider, all tickers
  - Fundamentals: 7-day stale check, all tickers
  - News: core tickers always + universe movers only

Usage: python -m db.update
"""
import sys
import time
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


def _elapsed(start: float) -> str:
    s = int(time.time() - start)
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m{s % 60:02d}s"


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
    t0 = time.time()
    total_batches = (len(tickers) + OHLCV_BATCH_SIZE - 1) // OHLCV_BATCH_SIZE
    print(f"\n[OHLCV] {len(tickers)} tickers, {total_batches} batches", flush=True)
    con = get_conn()
    total_inserted = 0
    total_errors = 0

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
                print(f"  [{batch_num}/{total_batches}] no data ({_elapsed(t0)})", flush=True)
                continue

            inserted = 0
            errors = 0
            for ticker in batch:
                try:
                    sub = raw[raw["ticker"] == ticker].copy()
                    if sub.empty:
                        continue
                    sub = _calc_indicators(sub)
                    if "ma_20" not in sub.columns or sub["ma_20"].isna().all():
                        continue
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
                except Exception:
                    errors += 1

            total_inserted += inserted
            total_errors += errors
            print(f"  [{batch_num}/{total_batches}] +{inserted} rows, {errors} err ({_elapsed(t0)})", flush=True)
        except Exception as e:
            print(f"  [{batch_num}/{total_batches}] ERROR: {e} ({_elapsed(t0)})", flush=True)

    con.close()
    print(f"[OHLCV] Done: +{total_inserted} rows, {total_errors} errors in {_elapsed(t0)}", flush=True)


def update_fundamentals(tickers: list[str], registry: ProviderRegistry):
    if not FINNHUB_KEY:
        print("[Fundamentals] Skipped — no FINNHUB_KEY", flush=True)
        return

    t0 = time.time()
    con = get_conn()
    total = len(tickers)
    fetched = 0
    skipped = 0
    errors = 0

    print(f"\n[Fundamentals] {total} tickers (stale > {FUNDAMENTALS_STALE_DAYS}d)", flush=True)

    for i, ticker in enumerate(tickers, 1):
        try:
            row = con.execute(
                "SELECT updated_at FROM stock_fundamentals WHERE ticker = ?", [ticker]
            ).fetchone()
            if row and row[0]:
                try:
                    last_update = date.fromisoformat(str(row[0])[:10])
                    if (date.today() - last_update).days < FUNDAMENTALS_STALE_DAYS:
                        skipped += 1
                        if skipped % 200 == 0:
                            print(f"  [{i}/{total}] skipped {skipped} (fresh) ({_elapsed(t0)})", flush=True)
                        continue
                except (ValueError, TypeError):
                    pass

            provider = registry.get_fundamentals_provider("US")
            data = provider.fetch_fundamentals(ticker)

            con.execute("""
                INSERT INTO stock_fundamentals (
                    ticker, pe_ratio, ps_ratio, pb_ratio, peg_ratio, market_cap,
                    revenue_growth_yoy, earnings_growth_yoy, gross_margin,
                    roe, fcf_yield,
                    analyst_rating, analyst_target_price, analyst_count,
                    next_earnings_date, last_earnings_date, mspr,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (ticker) DO UPDATE SET
                    pe_ratio = EXCLUDED.pe_ratio, ps_ratio = EXCLUDED.ps_ratio,
                    pb_ratio = EXCLUDED.pb_ratio, peg_ratio = EXCLUDED.peg_ratio,
                    market_cap = EXCLUDED.market_cap,
                    revenue_growth_yoy = EXCLUDED.revenue_growth_yoy,
                    earnings_growth_yoy = EXCLUDED.earnings_growth_yoy,
                    gross_margin = EXCLUDED.gross_margin,
                    roe = EXCLUDED.roe, fcf_yield = EXCLUDED.fcf_yield,
                    analyst_rating = EXCLUDED.analyst_rating,
                    analyst_target_price = EXCLUDED.analyst_target_price,
                    analyst_count = EXCLUDED.analyst_count,
                    next_earnings_date = EXCLUDED.next_earnings_date,
                    last_earnings_date = EXCLUDED.last_earnings_date,
                    mspr = EXCLUDED.mspr,
                    updated_at = EXCLUDED.updated_at
            """, [
                ticker, data.get("pe_ratio"), data.get("ps_ratio"),
                data.get("pb_ratio"), data.get("peg_ratio"), data.get("market_cap"),
                data.get("revenue_growth_yoy"), data.get("earnings_growth_yoy"),
                data.get("gross_margin"), data.get("roe"), data.get("fcf_yield"),
                data.get("analyst_rating"), data.get("analyst_target_price"),
                data.get("analyst_count"),
                data.get("next_earnings_date"), data.get("last_earnings_date"),
                data.get("mspr"),
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

            fetched += 1
            if fetched % 50 == 0:
                print(f"  [{i}/{total}] fetched {fetched}, skipped {skipped}, err {errors} ({_elapsed(t0)})", flush=True)
        except Exception as e:
            errors += 1
            if errors <= 5 or errors % 20 == 0:
                print(f"  [{i}/{total}] {ticker}: ERROR - {e} ({_elapsed(t0)})", flush=True)

    con.close()
    print(f"[Fundamentals] Done: {fetched} fetched, {skipped} skipped, {errors} errors in {_elapsed(t0)}", flush=True)


def update_news(tickers: list[str], registry: ProviderRegistry, days: int = 3):
    if not FINNHUB_KEY:
        print("[News] Skipped — no FINNHUB_KEY", flush=True)
        return

    t0 = time.time()
    total = len(tickers)
    total_articles = 0
    errors = 0

    print(f"\n[News] {total} tickers ({days} days)", flush=True)
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

            total_articles += count
        except Exception as e:
            errors += 1
            if errors <= 5 or errors % 20 == 0:
                print(f"  [{i}/{total}] {ticker}: ERROR - {e}", flush=True)

        if i % 50 == 0 or i == total:
            print(f"  [{i}/{total}] +{total_articles} articles, {errors} err ({_elapsed(t0)})", flush=True)

    con.close()
    print(f"[News] Done: +{total_articles} articles, {errors} errors in {_elapsed(t0)}", flush=True)


def run():
    t_start = time.time()
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

    print(f"\n{'=' * 55}")
    print(f"[Update] {date.today()} — {len(all_tickers)} tickers ({len(core_tickers)} core)")
    print(f"{'=' * 55}", flush=True)

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
    print(f"\n[News Gate] {len(core_tickers)} core + {len(movers)} movers = {len(news_tickers)} total", flush=True)
    update_news(news_tickers, registry)

    print(f"\n{'=' * 55}")
    print(f"Update complete in {_elapsed(t_start)}")
    print(f"{'=' * 55}", flush=True)


if __name__ == "__main__":
    run()
