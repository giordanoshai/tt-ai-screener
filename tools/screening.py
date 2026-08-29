import json
import datetime
from db.init import get_conn
from data_sources.uw_client import UnusualWhalesClient, uw_available


def _extract_uw_summary(uw_data: dict) -> dict:
    """Extract key UW data points from a comprehensive profile into a flat dict."""
    summary = {}

    # Insider: distinguish buys vs sells
    insider_data = uw_data.get("insider", {}).get("data", [])
    if insider_data:
        buys = sum(1 for t in insider_data[:20] if t.get("transaction_type") == "P")
        sells = sum(1 for t in insider_data[:20] if t.get("transaction_type") == "S")
        summary["uw_insider_buys"] = buys
        summary["uw_insider_sells"] = sells

    # Short Interest
    si_data = uw_data.get("short_interest", {}).get("data", {})
    if si_data:
        summary["uw_short_interest_pct"] = si_data.get("short_interest")

    # Option Stance
    stance = uw_data.get("option_stance", {}).get("data", {})
    if stance:
        summary["uw_option_stance"] = stance.get("stance")

    # Seasonality — current month
    season = uw_data.get("seasonality", {}).get("data", [])
    if season:
        current_month = datetime.datetime.now().month
        month_data = next((m for m in season if m.get("month") == current_month), None)
        if month_data:
            summary["uw_seasonality_current_month_avg_return"] = month_data.get("average_return")

    # Risk Reversal Skew — latest value
    skew = uw_data.get("skew", {}).get("data", [])
    if isinstance(skew, list) and skew:
        summary["uw_latest_skew"] = skew[-1].get("skew")

    return summary


def screen_stocks(
    screen_type: str = "longterm",
    sector: str = None,
    min_market_cap: float = None,
    max_pe: float = None,
    min_growth: float = None,
    min_margin: float = None,
    min_rsi: float = None,
    max_rsi: float = None,
    above_ma: int = None,
    limit: int = 20,
) -> str:
    """
    Screen stocks with custom filters, then return data for analysis.
    AT LEAST ONE filter parameter is required (besides screen_type and limit).

    screen_type: 'longterm' | 'swing' — determines scoring and default sort
    sector: filter by sector name, e.g. 'Technology', 'Healthcare'
    min_market_cap: minimum market cap in billions (e.g. 10 = $10B+)
    max_pe: maximum P/E ratio
    min_growth: minimum revenue growth YoY as percentage (e.g. 15 = 15%+)
    min_margin: minimum gross margin as percentage (e.g. 30 = 30%+)
    min_rsi: minimum RSI-14
    max_rsi: maximum RSI-14
    above_ma: price must be above this MA period (20, 50, or 200)
    limit: max results (default 20, max 30)
    """
    has_filter = any(v is not None for v in [
        sector, min_market_cap, max_pe, min_growth, min_margin,
        min_rsi, max_rsi, above_ma,
    ])
    if not has_filter:
        return json.dumps({
            "error": "At least one filter is required to avoid token explosion. "
                     "Available filters: sector, min_market_cap, max_pe, min_growth, "
                     "min_margin, min_rsi, max_rsi, above_ma"
        })

    limit = min(limit, 30)

    con = get_conn()
    try:
        where = [
            "o.date = (SELECT MAX(date) FROM stock_ohlcv_daily WHERE ticker = f.ticker)",
        ]
        params = []

        if sector:
            where.append("LOWER(m.sector) = LOWER(?)")
            params.append(sector)
        if min_market_cap is not None:
            where.append("f.market_cap >= ?")
            params.append(min_market_cap * 1e9)
        if max_pe is not None:
            where.append("(f.pe_ratio <= ? OR f.pe_ratio IS NULL)")
            params.append(max_pe)
        if min_growth is not None:
            where.append("f.revenue_growth_yoy >= ?")
            params.append(min_growth / 100.0)
        if min_margin is not None:
            where.append("f.gross_margin >= ?")
            params.append(min_margin / 100.0)
        if min_rsi is not None:
            where.append("o.rsi_14 >= ?")
            params.append(min_rsi)
        if max_rsi is not None:
            where.append("o.rsi_14 <= ?")
            params.append(max_rsi)
        if above_ma is not None:
            if above_ma == 200:
                where.append("o.close > o.ma_200")
            elif above_ma == 50:
                where.append("o.close > o.ma_50")
            elif above_ma == 20:
                where.append("o.close > o.ma_20")

        if screen_type == "swing":
            order_clause = "o.vol_ratio DESC NULLS LAST"
        else:
            order_clause = """(
                COALESCE(f.revenue_growth_yoy, 0) * 0.4
                + COALESCE(f.earnings_growth_yoy, 0) * 0.3
                + COALESCE(f.gross_margin, 0) * 0.3
            ) DESC"""

        params.append(limit)

        rows = con.execute(f"""
            SELECT
                f.ticker,
                m.company_name,
                m.sector,
                o.close,
                o.pct_chg,
                o.rsi_14,
                o.vol_ratio,
                o.ma_20,
                o.ma_50,
                o.ma_200,
                o.dist_ma20_pct,
                ROUND((o.close - o.ma_200) / NULLIF(o.ma_200, 0), 4) AS dist_ma200_pct,
                o.atr_pct,
                f.pe_ratio,
                f.peg_ratio,
                f.market_cap,
                f.revenue_growth_yoy,
                f.earnings_growth_yoy,
                f.gross_margin,
                f.analyst_rating,
                f.analyst_target_price,
                f.next_earnings_date
            FROM stock_fundamentals f
            JOIN stock_ohlcv_daily o ON f.ticker = o.ticker
            LEFT JOIN stocks_meta m ON f.ticker = m.ticker
            WHERE {' AND '.join(where)}
            ORDER BY {order_clause}
            LIMIT ?
        """, params).fetchall()

        cols = [
            "ticker", "company_name", "sector", "close", "pct_chg",
            "rsi_14", "vol_ratio", "ma_20", "ma_50", "ma_200",
            "dist_ma20_pct", "dist_ma200_pct", "atr_pct",
            "pe_ratio", "peg_ratio", "market_cap",
            "revenue_growth_yoy", "earnings_growth_yoy", "gross_margin",
            "analyst_rating", "analyst_target_price", "next_earnings_date",
        ]
        result = [dict(zip(cols, r)) for r in rows]

        # Fetch UW Smart Money & Options Data for top 10 candidates (only if token configured)
        if uw_available():
            uw_client = UnusualWhalesClient()
            for i in range(min(10, len(result))):
                ticker = result[i]["ticker"]
                try:
                    uw_data = uw_client.get_comprehensive_profile(ticker)
                    result[i].update(_extract_uw_summary(uw_data))
                except Exception as e:
                    result[i]["uw_error"] = str(e)

        filters_used = {k: v for k, v in {
            "screen_type": screen_type, "sector": sector,
            "min_market_cap": min_market_cap, "max_pe": max_pe,
            "min_growth": min_growth, "min_margin": min_margin,
            "min_rsi": min_rsi, "max_rsi": max_rsi, "above_ma": above_ma,
        }.items() if v is not None}

        return json.dumps({
            "filters": filters_used,
            "count": len(result),
            "candidates": result,
        }, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})
    finally:
        con.close()
