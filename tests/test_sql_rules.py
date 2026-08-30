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

#: 列名里含 market 但不是市场条件的，判之前要先剔掉，
#: 否则 `select market_cap from stock_fundamentals` 会被当成带了市场条件。
_MARKET_LOOKALIKE = re.compile(r"\bmarket_(cap|amount)\b")


def _python_files():
    for folder in SCANNED_DIRS:
        for path in sorted((ROOT / folder).glob("*.py")):
            yield path


def _joined_str_text(node: ast.JoinedStr) -> str:
    """把 f-string 拼回一整条。插值位置换成一个空格——
    不换的话 '{X}from' 会粘成一个词，反而漏判。"""
    parts = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        else:
            parts.append(" ")
    return "".join(parts)


#: 带 docstring 的四种节点。module / class / def / async def 的函数体第一条
#: 语句如果是一个纯字符串，那条就是 docstring——`ast.get_docstring()` 认的
#: 正是这一个。
_DOCSTRING_OWNERS = (ast.Module, ast.ClassDef,
                     ast.FunctionDef, ast.AsyncFunctionDef)


def _docstring_constant_ids(tree: ast.AST) -> set[int]:
    """把这棵树里所有 docstring 那个字符串常量节点挑出来。

    **只挑 docstring，不是所有字符串常量。** 这个区别就是「修好误判」和
    「把护栏拆了」的分界线：docstring 按定义是定义体的第一条语句，
    没有任何办法把它当参数传给 `pg.query()`，所以它永远不可能是一条真查询；
    而普通字符串常量正是查询本身待的地方，一个都不能放过。
    """
    ids = set()
    for node in ast.walk(tree):
        if not isinstance(node, _DOCSTRING_OWNERS):
            continue
        if ast.get_docstring(node) is None:
            continue
        ids.add(id(node.body[0].value))
    return ids


def _sql_strings(path: pathlib.Path):
    """把一个文件里所有看着像 SQL 的字符串抠出来。

    f-string 必须整条拼回来再判：AST 会把插值两边切成独立的 Constant，
    照片段判的话，`select` 和 `from stock_ohlcv_daily` 分处两段，
    带表名的那一段永远进不了扫描——这个护栏就成了摆设。

    **docstring 要跳过。** 判据是「同时含 select 和 from」，而 core/ 里的
    中文模块说明经常两个词都占：`core/snapshot.py` 的 docstring 里写着一句
    反面教材 `date = (select max(date) ... where ticker = o.ticker)`，
    同一段里再提一个名字带 from 的字段（`dist_from_20d_high_pct`），
    整段中文说明就被当成一条查询，报「查了 stock_ohlcv_daily 却没带 market」。
    这种误判把人往完全错误的方向指，而且逼着写文档的人绕着词写。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = _docstring_constant_ids(tree)
    stack = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, ast.JoinedStr):
            text = _joined_str_text(node)   # 拼回整条，不再往下钻
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings:      # 文档不是查询，跳过
                continue
            text = node.value
        else:
            stack.extend(ast.iter_child_nodes(node))
            continue
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
        lowered = _MARKET_LOOKALIKE.sub("", sql.lower())
        for table in MARKET_TABLES:
            if table in lowered and not re.search(r"\bmarket\b", lowered):
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


# ---------------- 护栏本身的回归测试 ----------------

def test_scanner_sees_sql_split_across_fstring_interpolation():
    """f-string 里 select 和 from 被插值隔开时，扫描器必须仍然看得见整条。

    core/snapshot.py 的 _GET_SQL 就是这个形状：漏了它，规矩 2~5 等于没写。
    """
    found = [sql for name, sql in _all_sql()
             if name == "snapshot.py" and "stock_ohlcv_daily" in sql]
    assert found, "扫描器没看见 core/snapshot.py 里带 stock_ohlcv_daily 的查询"


#: 一个假模块，专门用来验「跳过 docstring」这件事做对了没有。
#: 三处 docstring（模块、类、函数）都故意写成看着像违规 SQL 的中文说明，
#: 而真正违规的那条 SQL 是当参数传给 pg.query 的普通字符串。
_FAKE_MODULE_SOURCE = '''# -*- coding: utf-8 -*-
"""模块说明：取最新交易日绝对不能写成
`o.date = (select max(date) from stock_ohlcv_daily where ticker = o.ticker)`
这种相关子查询。另外本模块自己算的 dist_from_20d_high_pct 已经带了 * 100。
"""
from db import pg


class Screener:
    """类说明：select ticker from stock_ohlcv_daily 这种写法漏了条件。"""

    def documented_and_clean(self):
        """函数说明：反面教材是 select * from stock_fundamentals 不去重。"""
        return pg.query(
            "select ticker from stock_ohlcv_daily where market = %(m)s",
            {"m": "US"})


def actually_broken():
    return pg.query("select ticker, close from stock_ohlcv_daily "
                    "where close > 10")
'''


def _write_fake_module(tmp_path) -> pathlib.Path:
    path = tmp_path / "fake_module.py"
    path.write_text(_FAKE_MODULE_SOURCE, encoding="utf-8")
    return path


def test_scanner_ignores_sql_looking_text_inside_docstrings(tmp_path):
    """docstring 里的反面教材不该被当成真查询。

    这三段 docstring 都同时含 select 和 from，旧扫描器会把它们全捞出来，
    然后报「查了 stock_ohlcv_daily 却没带 market」——报错说的是查询，
    真实原因却是有人写了段中文文档，指错方向指得非常彻底。
    """
    found = list(_sql_strings(_write_fake_module(tmp_path)))

    for text in found:
        assert "模块说明" not in text, f"模块 docstring 被当成 SQL 捞出来了：{text}"
        assert "类说明" not in text, f"类 docstring 被当成 SQL 捞出来了：{text}"
        assert "函数说明" not in text, f"函数 docstring 被当成 SQL 捞出来了：{text}"


def test_scanner_still_catches_a_real_violation_next_to_those_docstrings(
        tmp_path):
    """跳过 docstring 不等于把护栏挖空：真传给 pg.query 的违规 SQL 仍要被抓。

    这条和上一条必须成对看。只有上一条的话，把所有字符串常量一律跳过
    也能让它绿——那才是真的把规矩 2~5 全废了。
    """
    path = _write_fake_module(tmp_path)
    found = list(_sql_strings(path))

    # 两条真 SQL 都得看见：合规的那条和违规的那条
    assert any("where market = %(m)s" in t for t in found), (
        "合规的那条真 SQL 没被扫到，扫描器把普通字符串也跳过了")
    assert any("where close > 10" in t for t in found), (
        "违规的那条真 SQL 没被扫到，护栏被挖空了")

    # 再走一遍规矩 2 的判定逻辑，确认它确实被判为违规
    offenders = []
    for sql in found:
        lowered = _MARKET_LOOKALIKE.sub("", sql.lower())
        for table in MARKET_TABLES:
            if table in lowered and not re.search(r"\bmarket\b", lowered):
                offenders.append(sql)
    assert len(offenders) == 1, (
        f"规矩 2 应该正好抓到 1 条违规，实际抓到 {len(offenders)} 条：{offenders}")
    assert "where close > 10" in offenders[0]


def test_real_core_docstrings_do_mention_from_and_select_together():
    """确认上面两条测的不是假想问题：core/snapshot.py 的 docstring 现在
    确实同时含 select 和 from，靠的就是扫描器跳过 docstring 才没红。

    这条一旦失败，说明有人又把文档改成绕着词写了，那两条回归测试就成了摆设。
    """
    tree = ast.parse((ROOT / "core" / "snapshot.py").read_text(encoding="utf-8"))
    doc = ast.get_docstring(tree) or ""
    lowered = doc.lower()
    assert "select" in lowered and "from" in lowered, (
        "core/snapshot.py 的模块说明里不再同时出现 select 和 from，"
        "跳过 docstring 这件事就没有真实用例守着了")
    assert "dist_from_20d_high_pct" in doc, (
        "那个名字里带 from 的字段又被从文档里拿掉了")


def test_market_cap_column_does_not_count_as_a_market_condition():
    """market_cap 是列名，不是市场条件。靠裸子串判会把真违规放过去。"""
    sql = ("select ticker, next_earnings_date, market_cap "
           "from stock_fundamentals where ticker = any(%s)")
    lowered = _MARKET_LOOKALIKE.sub("", sql.lower())
    assert not re.search(r"\bmarket\b", lowered)
