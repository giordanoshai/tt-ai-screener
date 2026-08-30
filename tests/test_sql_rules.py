# -*- coding: utf-8 -*-
"""查询规矩的回归测试。扫 core/ 和 tools/ 的源码，不连数据库。

这五条规矩每一条都真的把这个项目搞崩过一次（见 db/pg.py 的模块注释），
外加一条设计约束（短线不许碰基本面）。靠自觉守不住，所以让测试守。
"""
import ast
import inspect
import pathlib
import re

import pytest

from core import scoring, snapshot

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCANNED_DIRS = ("core", "tools")

#: 混了美股和 A股的表。查它们必须带 market 条件。
MARKET_TABLES = ("stock_ohlcv_daily", "stock_ohlcv_min", "stocks_meta",
                 "stock_fundamentals")


def _python_files():
    for folder in SCANNED_DIRS:
        for path in sorted((ROOT / folder).glob("*.py")):
            yield path


def _sql_strings(path: pathlib.Path):
    """把一个文件里所有看着像 SQL 的字符串字面量抠出来。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            lowered = text.lower()
            if "select" in lowered and "from" in lowered:
                yield text


def _all_sql():
    for path in _python_files():
        for sql in _sql_strings(path):
            yield path.name, sql


# ---------------- 规矩 1：占位符是 %s 不是 ? ----------------

def test_no_duckdb_placeholders():
    """psycopg2 用 %s。留一个 ? 会直接报 syntax error at or near "?"。"""
    offenders = []
    for name, sql in _all_sql():
        # 去掉字符串字面量里的问号（比如 like '%?%'），只看裸的占位符
        stripped = re.sub(r"'[^']*'", "''", sql)
        if re.search(r"(?<![\w'])\?(?![\w'])", stripped):
            offenders.append(name)
    assert not offenders, f"这些文件里有 DuckDB 风格的 ? 占位符：{offenders}"


# ---------------- 规矩 2：行情表查询必须带 market ----------------

def test_market_tables_always_filtered_by_market():
    offenders = []
    for name, sql in _all_sql():
        lowered = sql.lower()
        for table in MARKET_TABLES:
            if table in lowered and "market" not in lowered:
                offenders.append(f"{name}: 查了 {table} 却没带 market")
    assert not offenders, offenders


# ---------------- 规矩 3：不用相关子查询取最新日期 ----------------

def test_no_correlated_max_date_subquery():
    """`date = (select max(date) ... where ticker = x.ticker)` 是全表扫 380 万行。"""
    pattern = re.compile(
        r"=\s*\(\s*select\s+max\s*\(\s*date\s*\)", re.IGNORECASE | re.DOTALL)
    offenders = [name for name, sql in _all_sql() if pattern.search(sql)]
    assert not offenders, f"这些文件用了相关子查询取最新日期：{offenders}"


# ---------------- 规矩 4：查基本面必须去重 ----------------

def test_fundamentals_queries_dedupe_by_ticker():
    """stock_fundamentals 一只票多行，不去重会拿到几年前的数。"""
    offenders = []
    for name, sql in _all_sql():
        lowered = sql.lower()
        if "stock_fundamentals" in lowered and "distinct on (ticker)" not in lowered:
            offenders.append(f"{name}: 查 stock_fundamentals 没有 distinct on (ticker)")
    assert not offenders, offenders


# ---------------- 规矩 5：查分钟表必须带 timeframe ----------------

def test_min_table_queries_pin_timeframe():
    """stock_ohlcv_min 里 1h / 1m / 5m 混在一起，分钟线 2000 万行 9.3GB。"""
    offenders = []
    for name, sql in _all_sql():
        lowered = sql.lower()
        if "stock_ohlcv_min" in lowered and "timeframe" not in lowered:
            offenders.append(f"{name}: 查 stock_ohlcv_min 没带 timeframe")
    assert not offenders, offenders


# ---------------- 设计约束：短线不许碰基本面 ----------------

def test_snapshot_module_never_touches_fundamental_columns():
    """设计文档第三节：短波段只看价格和资金，公司好不好一概不看。"""
    source = (ROOT / "core" / "snapshot.py").read_text(encoding="utf-8")
    # 模块自己声明的禁用清单那一段不算（它必须写出这些词）
    body = source.split("MARKETS = ", 1)[-1]
    hits = [col for col in snapshot.BANNED_FUNDAMENTAL_COLUMNS if col in body]
    assert not hits, f"core/snapshot.py 里出现了短线禁用的基本面字段：{hits}"


def test_short_scoring_functions_take_no_fundamentals():
    for func in (scoring.score_short, scoring.score_short_cn):
        params = set(inspect.signature(func).parameters)
        assert not params & {"fund", "fundamental", "fundamentals"}, (
            f"{func.__name__} 的入参里出现了基本面")


def test_banned_columns_list_is_not_empty():
    """防着有人把禁用清单清空来让上面的测试变绿。"""
    assert len(snapshot.BANNED_FUNDAMENTAL_COLUMNS) >= 15
