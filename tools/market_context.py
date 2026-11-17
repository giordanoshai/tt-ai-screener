# -*- coding: utf-8 -*-
"""市场环境、新闻、行情基础表查询与系统运维诊断工具（由 TTAiTradingSystem 迁入）。

所有对 `stocks_meta` / `stock_ohlcv_daily` / `stock_fundamentals` 的查询
均强制带 `market` 条件，并满足 `tests/test_sql_rules.py` 全部约束。
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
from db import pg
from tools._common import dump_json, resolve_market


def get_latest_market_breadth(limit: int = 5) -> str:
    """获取最近几天的市场广度及趋势数据 (market_breadth_daily)。"""
    try:
        rows = pg.query(
            "select * from market_breadth_daily order by date desc limit %s",
            (int(limit),),
        )
        return dump_json(rows)
    except Exception as exc:
        return f"获取市场趋势失败: {exc}"


def get_market_regime_history(limit: int = 10, include_config: bool = False) -> str:
    """获取最近的市场环境/市场体制历史 (market_regime_history)。"""
    try:
        rows = pg.query(
            "select * from market_regime_history order by date desc limit %s",
            (int(limit),),
        )
        if not include_config:
            for row in rows:
                row.pop("regime_config", None)
        return dump_json(rows)
    except Exception as exc:
        return f"获取市场体制历史失败: {exc}"


def get_daily_news_summary(limit: int = 5) -> str:
    """获取最近几天的每日新闻精华总结 (news_daily_summary)。"""
    try:
        rows = pg.query(
            "select * from news_daily_summary order by summary_date desc limit %s",
            (int(limit),),
        )
        return dump_json(rows)
    except Exception as exc:
        return f"获取新闻总结失败: {exc}"


def search_stock_news(
    ticker: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    significant_only: bool = False,
    min_sentiment: float | None = None,
    limit: int = 10,
) -> str:
    """搜索特定股票的近期新闻 (news)，支持按日期、重要性、情绪分数筛选。"""
    try:
        clauses: list[str] = []
        params: list = []
        if ticker:
            clauses.append("%s = any(related_tickers)")
            params.append(ticker.strip().upper())
        if start_date:
            clauses.append("published_at >= %s")
            params.append(start_date.strip())
        if end_date:
            clauses.append("published_at <= %s")
            params.append(f"{end_date.strip()}T23:59:59Z")
        if significant_only:
            clauses.append("significant ilike %s")
            params.append("Yes%")
        if min_sentiment is not None:
            clauses.append("sentiment_score >= %s")
            params.append(float(min_sentiment))

        where_sql = f"where {' and '.join(clauses)}" if clauses else ""
        sql = f"""
            select id, title, url, published_at, title_cn, summary_cn,
                   sentiment_label, sentiment_score, significant, significant_reason
            from news
            {where_sql}
            order by published_at desc
            limit %s
        """
        return dump_json(pg.query(sql, tuple(params) + (int(limit),)))
    except Exception as exc:
        return f"搜索新闻失败: {exc}"


def get_news_sentiment_summary(ticker: str, days: int = 7) -> str:
    """返回指定股票近期新闻情绪聚合统计与显著正/负面数量（不返回新闻正文）。"""
    try:
        sym = ticker.strip().upper()
        since = datetime.now(timezone.utc) - timedelta(days=int(days))
        rows = pg.query(
            """
            select sentiment_score, significant
            from news
            where %s = any(related_tickers)
              and published_at >= %s
            """,
            (sym, since),
        )
        scores = [float(r["sentiment_score"]) for r in rows if r.get("sentiment_score") is not None]
        avg_sentiment = round(sum(scores) / len(scores), 4) if scores else None

        neg_sig = 0
        pos_sig = 0
        for row in rows:
            sig = row.get("significant")
            score = row.get("sentiment_score")
            if sig in ("High", "Yes", "Yes (Minor)") and score is not None:
                val = float(score)
                if val < -0.3:
                    neg_sig += 1
                elif val > 0.3:
                    pos_sig += 1

        return dump_json({
            "ticker": sym,
            "days": int(days),
            "total_news": len(rows),
            "avg_sentiment": avg_sentiment,
            "negative_significant": neg_sig,
            "positive_significant": pos_sig,
        })
    except Exception as exc:
        return f"获取情绪聚合统计失败: {exc}"


def search_stocks_meta(
    ticker: str | None = None,
    company_name: str | None = None,
    is_spy500: bool | None = None,
    market: str | None = None,
    limit: int = 50,
) -> str:
    """检索或模糊搜索股票元数据 (stocks_meta)。默认按代码推市场或查 US。"""
    try:
        target_market = resolve_market(ticker, market)
        extra_clauses: list[str] = []
        params: list = [target_market]
        if ticker:
            extra_clauses.append("and ticker = %s")
            params.append(ticker.strip().upper())
        if company_name:
            extra_clauses.append("and company_name ilike %s")
            params.append(f"%{company_name.strip()}%")
        if is_spy500 is not None:
            extra_clauses.append("and is_spy500 = %s")
            params.append(bool(is_spy500))

        extra_sql = " ".join(extra_clauses)
        sql = f"""
            select *
            from stocks_meta
            where market = %s {extra_sql}
            order by ticker asc
            limit %s
        """
        return dump_json(pg.query(sql, tuple(params) + (int(limit),)))
    except Exception as exc:
        return f"搜索股票元数据失败: {exc}"


def get_stock_ohlcv(
    ticker: str,
    start_date: str | None = None,
    end_date: str | None = None,
    market: str | None = None,
    limit: int = 100,
) -> str:
    """获取正股日线 K 线与技术指标历史 (stock_ohlcv_daily)。"""
    try:
        sym = ticker.strip().upper()
        target_market = resolve_market(sym, market)
        extra_clauses: list[str] = []
        params: list = [sym, target_market]
        if start_date:
            extra_clauses.append("and date >= %s::date")
            params.append(start_date.strip())
        if end_date:
            extra_clauses.append("and date <= %s::date")
            params.append(end_date.strip())

        extra_sql = " ".join(extra_clauses)
        sql = f"""
            select *
            from stock_ohlcv_daily
            where ticker = %s and market = %s {extra_sql}
            order by date desc
            limit %s
        """
        return dump_json(pg.query(sql, tuple(params) + (int(limit),)))
    except Exception as exc:
        return f"获取正股历史K线失败: {exc}"


def get_stock_fundamentals(ticker: str, market: str | None = None) -> str:
    """获取单只股票最新发布的基本面快照 (stock_fundamentals)。"""
    try:
        sym = ticker.strip().upper()
        target_market = resolve_market(sym, market)
        row = pg.query_one(
            """
            select distinct on (ticker) *
            from stock_fundamentals
            where ticker = %s
              and market = %s
            order by ticker, updated_at desc
            limit 1
            """,
            (sym, target_market),
        )
        return dump_json(row or {})
    except Exception as exc:
        return f"获取股票基本面数据失败: {exc}"


def _log_file_path() -> Path:
    return Path(config.TTAI_LOG_DIR) / "ttaisystem.log"


def get_system_logs(lines: int = 100) -> str:
    """读取 TTAiTradingSystem 系统日志末尾若干行，检查后台任务执行情况。"""
    try:
        log_path = _log_file_path()
        if not log_path.exists():
            return f"日志文件不存在: {log_path}"
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        return "".join(all_lines[-int(lines):])
    except Exception as exc:
        return f"读取日志失败: {exc}"


def analyze_server_status(lines_to_scan: int = 300) -> str:
    """扫描 `ttaisystem.log` 与最近归档日志，评估后台调度、Tiger API 与数据拉取状态。"""
    try:
        log_path = _log_file_path()
        log_dir = log_path.parent
        log_contents: list[str] = []

        if log_path.exists():
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                log_contents.extend(f.readlines())

        if log_dir.exists():
            rotated = sorted(
                (p for p in log_dir.iterdir()
                 if p.name.startswith("ttaisystem.log.") and p.name.endswith(".log")),
                key=lambda p: p.stat().st_mtime,
            )
            if rotated:
                try:
                    with open(rotated[-1], "r", encoding="utf-8", errors="replace") as f:
                        log_contents = f.readlines() + log_contents
                except Exception:
                    pass

        if not log_contents:
            return f"诊断结果: 未发现任何系统日志文件 ({log_path})，无法分析运行状态。"

        scanned = log_contents[-int(lines_to_scan):]
        errors: list[str] = []
        data_fetches: list[str] = []
        schedulers: list[str] = []
        rate_limits = 0
        success_fetches = 0
        failed_fetches = 0

        for raw in scanned:
            line = raw.strip()
            if not line:
                continue
            if " - ERROR - " in line or " - CRITICAL - " in line or "Traceback" in line:
                errors.append(line)
                if "rate limit" in line.lower() or "limit" in line.lower():
                    rate_limits += 1
            if any(k in line for k in ("Fetch", "抓取", "下载", "VIX", "OHLCV", "scraper")):
                data_fetches.append(line)
                if "成功保存" in line or "success" in line.lower() or "💾" in line:
                    success_fetches += 1
                elif "出错" in line or "error" in line.lower() or "❌" in line:
                    failed_fetches += 1
            if any(k in line for k in ("APScheduler", "scheduler", "启动后端", "lifespan", "关闭后台")):
                schedulers.append(line)

        health = "HEALTHY (正常)"
        if errors:
            health = (
                "DEGRADED (运行受限/频繁报错)"
                if (len(errors) > 10 or rate_limits > 5)
                else "WARNING (有部分报错，需关注)"
            )
        if schedulers and "关闭" in "".join(schedulers[-2:]):
            health = "STOPPED (后台服务已关闭)"

        report = [
            f"TTAiTradingSystem 服务器状态诊断 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})",
            f"日志路径: {log_path} | 扫描行数: {len(scanned)} | 健康度: {health}",
            f"调度生命周期: {schedulers[-3:] if schedulers else '无近期记录'}",
            f"数据拉取: 成功={success_fetches}, 失败={failed_fetches}, 频控={rate_limits}",
            f"最近报错({len(errors)}条): {errors[-5:] if errors else '无'}",
        ]
        return "\n".join(report)
    except Exception as exc:
        return f"自动健康度分析失败: {exc}"
