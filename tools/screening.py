import json
from db.init import get_conn


def screen_longterm_candidates(limit: int = 20) -> str:
    """
    Long-term stock screening: high growth + high margin + reasonable valuation +
    price above MA200 but not overextended (< 35% above MA200) + not overbought (RSI < 72).
    Returns candidates sorted by composite score (growth quality + valuation).
    """
    con = get_conn()
    try:
        rows = con.execute("""
            SELECT
                f.ticker,
                m.company_name,
                m.sector,
                f.revenue_growth_yoy,
                f.earnings_growth_yoy,
                f.gross_margin,
                f.pe_ratio,
                f.peg_ratio,
                f.market_cap,
                f.analyst_rating,
                f.analyst_target_price,
                f.next_earnings_date,
                o.close,
                o.ma_50,
                o.ma_200,
                o.rsi_14,
                o.dist_ma20_pct,
                ROUND((o.close - o.ma_200) / o.ma_200, 4) AS dist_ma200_pct,
                -- Composite score: reward growth+margin, penalise extended price & high PE
                ROUND(
                    (f.revenue_growth_yoy * 0.4 + f.earnings_growth_yoy * 0.3 + f.gross_margin * 0.3)
                    - COALESCE((o.close - o.ma_200) / o.ma_200, 0) * 0.2
                    - COALESCE(f.pe_ratio, 30) / 300.0
                , 4) AS score
            FROM stock_fundamentals f
            JOIN stock_ohlcv_daily o ON f.ticker = o.ticker
            LEFT JOIN stocks_meta m ON f.ticker = m.ticker
            WHERE
                o.date = (SELECT MAX(date) FROM stock_ohlcv_daily WHERE ticker = f.ticker)
                AND f.revenue_growth_yoy  > 0.10
                AND f.earnings_growth_yoy > 0.10
                AND f.gross_margin        > 0.30
                AND (f.pe_ratio < 60 OR f.pe_ratio IS NULL)
                AND o.close > o.ma_200
                AND (o.close - o.ma_200) / o.ma_200 < 0.35
                AND o.rsi_14 < 72
            ORDER BY score DESC
            LIMIT ?
        """, [limit]).fetchall()

        cols = ["ticker","company_name","sector","revenue_growth_yoy","earnings_growth_yoy",
                "gross_margin","pe_ratio","peg_ratio","market_cap","analyst_rating",
                "analyst_target_price","next_earnings_date","close","ma_50","ma_200",
                "rsi_14","dist_ma20_pct","dist_ma200_pct","score"]

        result = [dict(zip(cols, r)) for r in rows]
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        return f"Error: {e}"
    finally:
        con.close()


def screen_swing_candidates(setup_type: str = "all", limit: int = 20) -> str:
    """
    Swing trade screening: technical breakout/pullback setups with volume confirmation.
    setup_type: 'breakout' | 'pullback' | 'all'

    Breakout: price near 20-day high (within 5%), strong volume (>1.5x), RSI 50-68 (confirmed but not overbought)
    Pullback: price pulled back to near MA20 (1-6% above), RSI cooled to 40-62, trend still intact
    """
    con = get_conn()
    try:
        rows = con.execute("""
            SELECT
                ticker, date, close, vol_ratio, rsi_14,
                dist_ma20_pct, dist_ma50_pct, high_20,
                ma_20, ma_50, ma_200, atr_pct,
                ROUND((close - ma_200) / ma_200, 4) AS dist_ma200_pct
            FROM stock_ohlcv_daily
            WHERE date = (SELECT MAX(date) FROM stock_ohlcv_daily)
              AND atr_pct > 0.01
              AND close   > ma_50
              AND close   > ma_200
              AND ma_20   > ma_50
        """).fetchall()

        cols = ["ticker","date","close","vol_ratio","rsi_14","dist_ma20_pct",
                "dist_ma50_pct","high_20","ma_20","ma_50","ma_200","atr_pct","dist_ma200_pct"]
        all_candidates = [dict(zip(cols, r)) for r in rows]

        if setup_type == "breakout":
            # Near 20-day high + strong volume + RSI confirmed but not overbought
            candidates = [
                c for c in all_candidates
                if (c["high_20"] and c["close"] / c["high_20"] >= 0.95)
                and (c["vol_ratio"] or 0) > 1.5
                and 50 <= (c["rsi_14"] or 0) <= 68
            ]
            candidates.sort(key=lambda x: x.get("vol_ratio") or 0, reverse=True)

        elif setup_type == "pullback":
            # Price pulled back to near MA20, RSI cooled, trend still up
            candidates = [
                c for c in all_candidates
                if c["dist_ma20_pct"] is not None
                and 0.01 <= c["dist_ma20_pct"] <= 0.06
                and 40 <= (c["rsi_14"] or 0) <= 62
                and (c["vol_ratio"] or 0) > 0.8
            ]
            # Sort: RSI closest to 50 (oversold recovery is best entry)
            candidates.sort(key=lambda x: abs((x.get("rsi_14") or 50) - 50))

        else:  # all
            breakouts = [
                c for c in all_candidates
                if (c["high_20"] and c["close"] / c["high_20"] >= 0.95)
                and (c["vol_ratio"] or 0) > 1.5
                and 50 <= (c["rsi_14"] or 0) <= 68
            ]
            pullbacks = [
                c for c in all_candidates
                if c["dist_ma20_pct"] is not None
                and 0.01 <= c["dist_ma20_pct"] <= 0.06
                and 40 <= (c["rsi_14"] or 0) <= 62
                and (c["vol_ratio"] or 0) > 0.8
            ]
            # Tag and merge, dedup by ticker
            seen = set()
            candidates = []
            for c in breakouts:
                if c["ticker"] not in seen:
                    c["setup"] = "breakout"
                    candidates.append(c)
                    seen.add(c["ticker"])
            for c in pullbacks:
                if c["ticker"] not in seen:
                    c["setup"] = "pullback"
                    candidates.append(c)
                    seen.add(c["ticker"])

        candidates = candidates[:limit]
        return json.dumps(candidates, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        return f"Error: {e}"
    finally:
        con.close()
