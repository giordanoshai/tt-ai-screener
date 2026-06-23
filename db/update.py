"""
Daily incremental update with tier-based news gating.
  - OHLCV: batch download via provider, all tickers
  - Fundamentals: 7-day stale check, all tickers
  - News: core tickers always + universe movers only
  - Sentiment: AI-powered news scoring

Usage: python -m db.update
"""
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
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


# ── Shared update state (for web progress tracking) ──────────────────────────

_state_lock = threading.Lock()
_update_state = {
    "running": False,
    "stage": "",
    "log": [],
    "started_at": None,
    "finished_at": None,
}


def get_update_state() -> dict:
    with _state_lock:
        return {**_update_state, "log": list(_update_state["log"])}


def _log(msg: str):
    print(msg, flush=True)
    with _state_lock:
        _update_state["log"].append(msg)


def _set_stage(stage: str):
    with _state_lock:
        _update_state["stage"] = stage
    _log(f"\n[{stage}] started")


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


def _run_ohlcv_batches(tickers: list[str], registry, con, t0, label: str = "") -> tuple[int, int, list[str]]:
    """Run OHLCV download in batches. Returns (inserted, errors, failed_tickers)."""
    total_batches = (len(tickers) + OHLCV_BATCH_SIZE - 1) // OHLCV_BATCH_SIZE
    total_inserted = 0
    total_errors = 0
    failed_tickers = []

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
                failed_tickers.extend(batch)
                _log(f"  {label}[{batch_num}/{total_batches}] no data ({_elapsed(t0)})")
                continue

            inserted = 0
            errors = 0
            for ticker in batch:
                try:
                    new_data = raw[raw["ticker"] == ticker].copy()
                    if new_data.empty:
                        failed_tickers.append(ticker)
                        continue

                    last_date = last_dates.get(ticker)
                    if last_date:
                        hist = con.execute(
                            "SELECT ticker, date, open, high, low, close, volume "
                            "FROM stock_ohlcv_daily WHERE ticker = ? ORDER BY date",
                            [ticker]
                        ).fetchdf()
                        hist["date"] = hist["date"].astype(str)
                        new_data["date"] = new_data["date"].astype(str)
                        combined = pd.concat([hist, new_data]).drop_duplicates(subset=["ticker", "date"], keep="last")
                    else:
                        combined = new_data

                    combined = _calc_indicators(combined)
                    for col in OHLCV_COLS:
                        if col not in combined.columns:
                            combined[col] = None

                    if last_date:
                        combined = combined[combined["date"].astype(str) > str(last_date)]
                    if combined.empty:
                        continue

                    combined = combined[OHLCV_COLS]
                    con.execute("INSERT OR REPLACE INTO stock_ohlcv_daily SELECT * FROM combined")
                    inserted += len(combined)
                except Exception:
                    errors += 1

            total_inserted += inserted
            total_errors += errors
            _log(f"  {label}[{batch_num}/{total_batches}] +{inserted} rows, {errors} err ({_elapsed(t0)})")
        except Exception as e:
            failed_tickers.extend(batch)
            _log(f"  {label}[{batch_num}/{total_batches}] ERROR: {e} ({_elapsed(t0)})")

    return total_inserted, total_errors, failed_tickers


def update_ohlcv(tickers: list[str], registry: ProviderRegistry, max_retries: int = 2):
    _set_stage("OHLCV")
    t0 = time.time()
    _log(f"[OHLCV] {len(tickers)} tickers, batch_size={OHLCV_BATCH_SIZE}")
    con = get_conn()

    total_inserted, total_errors, failed = _run_ohlcv_batches(tickers, registry, con, t0)

    for attempt in range(1, max_retries + 1):
        if not failed:
            break
        _log(f"[OHLCV] Retry {attempt}/{max_retries}: {len(failed)} failed tickers")
        retry_inserted, retry_errors, failed = _run_ohlcv_batches(
            failed, registry, con, t0, label=f"retry{attempt}/"
        )
        total_inserted += retry_inserted
        total_errors += retry_errors

    con.close()
    if failed:
        _log(f"[OHLCV] {len(failed)} tickers still failed after {max_retries} retries")
    _log(f"[OHLCV] Done: +{total_inserted} rows, {total_errors} errors in {_elapsed(t0)}")


def update_analyst_data(tickers: list[str], registry: ProviderRegistry, max_workers: int = 8):
    """Fast pass: yfinance analyst ratings with concurrent fetching."""
    _set_stage("Analyst")
    t0 = time.time()
    con = get_conn()
    total = len(tickers)
    fetched = 0
    skipped = 0
    errors = 0

    today_str = date.today().isoformat()
    fresh = set()
    cursor = con.execute(
        "SELECT ticker FROM stock_fundamentals WHERE analyst_rating IS NOT NULL AND updated_at >= ?",
        [today_str]
    )
    for row in cursor.fetchall():
        fresh.add(row[0])

    need_fetch = [t for t in tickers if t not in fresh]
    skipped = len(tickers) - len(need_fetch)

    _log(f"[Analyst] {total} tickers — {skipped} fresh, {len(need_fetch)} to fetch ({max_workers} threads)")

    if not need_fetch:
        con.close()
        _log(f"[Analyst] Done: 0 fetched, {skipped} skipped, 0 errors in {_elapsed(t0)}")
        return

    import yfinance as yf
    try:
        yf.Ticker("AAPL").info
        _log(f"  [Analyst] yfinance session warmed up ({_elapsed(t0)})")
    except Exception:
        pass

    provider = registry.get_fundamentals_provider("US")

    def _fetch_one(ticker: str) -> tuple[str, dict | None, str | None]:
        try:
            data = provider.fetch_analyst_data(ticker)
            return (ticker, data, None)
        except Exception as e:
            return (ticker, None, str(e))

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch_one, t): t for t in need_fetch}
        done_count = 0
        for future in as_completed(futures):
            done_count += 1
            ticker, data, err = future.result()

            if err:
                errors += 1
                if errors <= 3 or errors % 50 == 0:
                    _log(f"  [{done_count}/{len(need_fetch)}] {ticker}: ERROR - {err} ({_elapsed(t0)})")
                continue

            if not data or not data.get("analyst_rating"):
                skipped += 1
                continue

            set_clauses = []
            params = []
            for col in ["analyst_rating", "analyst_target_price", "analyst_count"]:
                if data.get(col) is not None:
                    set_clauses.append(f"{col} = ?")
                    params.append(data[col])
            for col in ["pe_ratio", "ps_ratio", "pb_ratio", "peg_ratio", "fcf_yield"]:
                if data.get(col) is not None:
                    set_clauses.append(f"{col} = COALESCE({col}, ?)")
                    params.append(data[col])

            if set_clauses:
                params.append(ticker)
                con.execute(f"UPDATE stock_fundamentals SET {', '.join(set_clauses)} WHERE ticker = ?", params)

            fetched += 1
            if fetched % 100 == 0:
                _log(f"  [{done_count}/{len(need_fetch)}] fetched {fetched}, skipped {skipped}, err {errors} ({_elapsed(t0)})")

    con.close()
    _log(f"[Analyst] Done: {fetched} fetched, {skipped} skipped, {errors} errors in {_elapsed(t0)}")


def update_fundamentals(tickers: list[str], registry: ProviderRegistry):
    if not FINNHUB_KEY:
        _log("[Fundamentals] Skipped — no FINNHUB_KEY")
        return

    _set_stage("Fundamentals")
    t0 = time.time()
    con = get_conn()
    total = len(tickers)
    fetched = 0
    skipped = 0
    errors = 0

    _log(f"[Fundamentals] {total} tickers (stale > {FUNDAMENTALS_STALE_DAYS}d)")

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
                            _log(f"  [{i}/{total}] skipped {skipped} (fresh) ({_elapsed(t0)})")
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
                _log(f"  [{i}/{total}] fetched {fetched}, skipped {skipped}, err {errors} ({_elapsed(t0)})")
        except Exception as e:
            errors += 1
            if errors <= 5 or errors % 20 == 0:
                _log(f"  [{i}/{total}] {ticker}: ERROR - {e} ({_elapsed(t0)})")

    con.close()
    _log(f"[Fundamentals] Done: {fetched} fetched, {skipped} skipped, {errors} errors in {_elapsed(t0)}")


def _fetch_and_insert_news(ticker, registry, con, days):
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
    return count


def update_news(tickers: list[str], registry: ProviderRegistry, days: int = 3, max_retries: int = 2):
    if not FINNHUB_KEY:
        _log("[News] Skipped — no FINNHUB_KEY")
        return

    _set_stage("News")
    t0 = time.time()
    total = len(tickers)
    total_articles = 0
    errors = 0
    failed = []

    _log(f"[News] {total} tickers ({days} days)")
    con = get_conn()

    for i, ticker in enumerate(tickers, 1):
        try:
            total_articles += _fetch_and_insert_news(ticker, registry, con, days)
        except Exception as e:
            errors += 1
            failed.append(ticker)
            if errors <= 5 or errors % 20 == 0:
                _log(f"  [{i}/{total}] {ticker}: ERROR - {e}")

        if i % 50 == 0 or i == total:
            _log(f"  [{i}/{total}] +{total_articles} articles, {errors} err ({_elapsed(t0)})")

    for attempt in range(1, max_retries + 1):
        if not failed:
            break
        _log(f"[News] Retry {attempt}/{max_retries}: {len(failed)} failed tickers")
        time.sleep(3)
        still_failed = []
        for ticker in failed:
            try:
                total_articles += _fetch_and_insert_news(ticker, registry, con, days)
                errors -= 1
            except Exception as e:
                still_failed.append(ticker)
                _log(f"  [retry{attempt}] {ticker}: ERROR - {e}")
        failed = still_failed

    if failed:
        _log(f"[News] {len(failed)} tickers still failed after {max_retries} retries: {failed}")

    con.close()
    _log(f"[News] Done: +{total_articles} articles, {errors} errors in {_elapsed(t0)}")


def update_news_sentiment(batch_size: int = 20, workers: int = 5):
    """Score unscored news articles using AI sentiment model (multi-threaded)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from ai_client import sentiment_available, score_news_batch
    if not sentiment_available():
        _log("[Sentiment] Skipped — no SENTIMENT_API configured")
        return

    _set_stage("Sentiment")
    t0 = time.time()
    con = get_conn()
    unscored = con.execute("""
        SELECT id, headline, summary FROM news
        WHERE sentiment_label IS NULL
        ORDER BY published_at DESC
    """).fetchall()

    total = len(unscored)
    if total == 0:
        _log("[Sentiment] All news already scored")
        con.close()
        return

    batches = []
    for i in range(0, total, batch_size):
        batches.append(unscored[i:i + batch_size])

    _log(f"[Sentiment] {total} articles, {len(batches)} batches, {workers} threads")
    scored = 0
    errors = 0
    done_articles = 0

    def _score_batch(batch):
        articles = [{"headline": r[1], "summary": r[2]} for r in batch]
        return batch, score_news_batch(articles)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_score_batch, b): b for b in batches}
        for future in as_completed(futures):
            try:
                batch, results = future.result()
                batch_scored = 0
                for j, res in enumerate(results):
                    if res.get("label"):
                        con.execute(
                            "UPDATE news SET sentiment_label = ?, sentiment_score = ? WHERE id = ?",
                            [res["label"], res.get("score"), batch[j][0]]
                        )
                        batch_scored += 1
                scored += batch_scored
            except Exception as e:
                errors += 1
                if errors <= 3:
                    _log(f"  Thread error: {e}")

            done_articles += len(futures[future])
            if done_articles % 100 < batch_size or done_articles >= total:
                _log(f"  [{done_articles}/{total}] scored {scored}, err {errors} ({_elapsed(t0)})")

    con.close()
    _log(f"[Sentiment] Done: {scored}/{total} scored, {errors} errors in {_elapsed(t0)}")


def _save_update_history():
    import json, re
    log_lines = list(_update_state["log"])
    log_text = "\n".join(log_lines)
    summary = {}
    patterns = {
        "ohlcv":        r"\[OHLCV\] Done: (\d+) inserted, (\d+) skipped, (\d+) errors? in (.+)",
        "fundamentals": r"\[Fundamentals\] Done: (\d+) fetched, (\d+) skipped, (\d+) errors? in (.+)",
        "analyst":      r"\[Analyst\] Done: (\d+) fetched, (\d+) skipped, (\d+) errors? in (.+)",
        "news":         r"\[News\] Done: \+(\d+) articles, (\d+) errors? in (.+)",
        "sentiment":    r"\[Sentiment\] Done: (\d+)/(\d+) scored, (\d+) errors? in (.+)",
    }
    for key, pat in patterns.items():
        m = re.search(pat, log_text)
        if not m:
            continue
        g = m.groups()
        if key == "news":
            summary[key] = {"articles": int(g[0]), "errors": int(g[1]), "duration": g[2]}
        elif key == "sentiment":
            summary[key] = {"scored": int(g[0]), "total": int(g[1]), "errors": int(g[2]), "duration": g[3]}
        else:
            summary[key] = {"fetched": int(g[0]), "skipped": int(g[1]), "errors": int(g[2]), "duration": g[3]}

    duration_m = re.search(r"Update complete in (.+)", log_text)
    duration_s = 0
    if duration_m:
        d = duration_m.group(1)
        if "m" in d:
            parts = re.match(r"(\d+)m(\d+)s", d)
            if parts:
                duration_s = int(parts.group(1)) * 60 + int(parts.group(2))
        elif "s" in d:
            duration_s = int(re.sub(r"[^\d]", "", d))

    status = "success" if all(summary.get(k, {}).get("errors", 0) == 0 for k in patterns) else "partial"
    try:
        con = get_conn()
        con.execute("""
            INSERT INTO update_history (started_at, finished_at, status, duration_s, summary, log_text)
            VALUES (?, ?, ?, ?, ?, ?)
        """, [
            _update_state["started_at"], _update_state["finished_at"],
            status, duration_s, json.dumps(summary), log_text,
        ])
        con.close()
    except Exception as e:
        _log(f"[Update] Failed to save history: {e}")


def run():
    with _state_lock:
        if _update_state["running"]:
            _log("[Update] Already running — skipping")
            return
        _update_state["running"] = True
        _update_state["log"] = []
        _update_state["started_at"] = datetime.now().isoformat()
        _update_state["finished_at"] = None

    try:
        t_start = time.time()
        registry = ProviderRegistry()
        con = get_conn()

        all_tickers = _load_all_tickers(con)
        if not all_tickers:
            fallback = load_tickers()
            all_tickers = [{"ticker": t, "market": "US", "tier": "core"} for t in fallback]
        if not all_tickers:
            _log("No tickers found — nothing to update.")
            return

        all_ticker_names = [t["ticker"] for t in all_tickers]
        core_tickers = [t["ticker"] for t in all_tickers if t["tier"] == "core"]

        _log(f"\n{'=' * 55}")
        _log(f"[Update] {date.today()} — {len(all_tickers)} tickers ({len(core_tickers)} core)")
        _log(f"{'=' * 55}")

        # 1. OHLCV
        update_ohlcv(all_ticker_names, registry)

        # 2. Fundamentals (Finnhub)
        update_fundamentals(all_ticker_names, registry)

        # 3. Analyst (yfinance)
        update_analyst_data(all_ticker_names, registry)

        # 4. News
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
        _log(f"[News Gate] {len(core_tickers)} core + {len(movers)} movers = {len(news_tickers)} total")
        update_news(news_tickers, registry)

        # 5. Sentiment
        update_news_sentiment()

        _log(f"\n{'=' * 55}")
        _log(f"Update complete in {_elapsed(t_start)}")
        _log(f"{'=' * 55}")
    finally:
        with _state_lock:
            _update_state["running"] = False
            _update_state["stage"] = "done"
            _update_state["finished_at"] = datetime.now().isoformat()
        _save_update_history()


if __name__ == "__main__":
    run()
