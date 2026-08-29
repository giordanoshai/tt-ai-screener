"""数据库访问层。读和写用不同账号，物理隔开。

只读账号 readonly_user 在库里就没有写权限，所以就算代码写错了也写不进去——
这是防误写的最后一道闸，别为了图省事都用写账号。在这之上还加了一道：
读连接一开就把会话设成只读事务，哪怕哪天账号权限被改宽了，写语句照样会被
数据库自己挡回来。

这个项目是**只读消费端**，往库里写行情/基本面/新闻的活全部在 tt-data-hub。
这里的写连接只给自选（user_watchlist）和持仓（user_positions）的增删改用。

用法上有两处跟原来的 DuckDB 版本不一样，移植时最容易漏：

1. 参数占位符是 ``%s``，不是 DuckDB 的 ``?``。写成 ``?`` 会直接报
   ``syntax error at or near "?"``。
2. 查最新一天不要写相关子查询
   （``o.date = (select max(date) from stock_ohlcv_daily where ticker = f.ticker)``），
   在 Postgres 上是全表扫 275 万行。先算一个 latest 公共表达式再等值连接。
"""
from contextlib import contextmanager

import psycopg2
import psycopg2.extensions
from psycopg2.extras import RealDictCursor

import config

APP_NAME = "tt-trading-mcp"
CONNECT_TIMEOUT = 15


def _numeric_to_float(value, _cur):
    """把 Postgres 的 numeric 转成 float，而不是默认的 Decimal。

    stock_fundamentals 有二十多个 numeric 列，user_positions 的 avg_cost /
    shares 也是 numeric。Decimal 丢给 json.dumps 会直接抛
    ``Object of type Decimal is not JSON serializable``，而 web 层和 MCP 层
    到处都要序列化。在这一层统一转掉，上层就不用各自处理了。

    代价是浮点精度，对这个用途（看盘、算盈亏百分比）完全够用。
    """
    if value is None:
        return None
    return float(value)


# 1700 = numeric，1231 = numeric[]。数组要单独注册一次，
# 否则 numeric[] 列里的元素还是 Decimal。
_FLOAT_NUMERIC = psycopg2.extensions.new_type(
    psycopg2.extensions.DECIMAL.values, "NUMERIC_AS_FLOAT", _numeric_to_float)
_FLOAT_NUMERIC_ARRAY = psycopg2.extensions.new_array_type(
    (1231,), "NUMERIC_AS_FLOAT[]", _FLOAT_NUMERIC)
psycopg2.extensions.register_type(_FLOAT_NUMERIC)
psycopg2.extensions.register_type(_FLOAT_NUMERIC_ARRAY)


@contextmanager
def _conn(write: bool = False):
    """开一个连接。正常结束自动提交，出异常自动回滚，总是关闭。"""
    url = config.PG_WRITE_URL if write else config.PG_READ_URL
    if not url:
        key = "PG_WRITE_URL" if write else "PG_READ_URL"
        raise RuntimeError(f"缺少 {key}，请在 .env 里配置")

    connection = psycopg2.connect(
        url, connect_timeout=CONNECT_TIMEOUT,
        application_name=APP_NAME + ("-write" if write else "-read"))
    try:
        if not write:
            # 会话级只读：账号权限之外的第二道闸
            connection.set_session(readonly=True)
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def query(sql: str, params=None) -> list[dict]:
    """只读查询。占位符用 %s。"""
    with _conn() as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]


def query_one(sql: str, params=None) -> dict | None:
    """只读查询，取第一行；没有结果返回 None。"""
    rows = query(sql, params)
    return rows[0] if rows else None


def execute_write(sql: str, params=None) -> int:
    """写操作，返回受影响行数。只该用在自选和持仓上。"""
    with _conn(write=True) as connection:
        with connection.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount


def fetch_write(sql: str, params=None) -> list[dict]:
    """写操作并取回结果，给带 RETURNING 的语句用。"""
    with _conn(write=True) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]
