# -*- coding: utf-8 -*-
"""基本面取数。**只有中线档（持有 2 周到 2 个月）才该调这个模块。**

短波段调它是设计错误：短线只看价格怎么走、钱往哪押、有没有雷，
公司好不好一概不看（设计文档第三节）。短线要的技术面在 core/snapshot.py，
要的财报日在 core/catalyst.earnings_for()。

## 一只票多行，必须去重

`stock_fundamentals` 的主键是 (ticker, updated_at)：2172 只美股散在 31721 行里，
平均一只票 15 行历史快照。不写 `distinct on (ticker) ... order by ticker,
updated_at desc` 就会随机拿到几年前的市盈率。

## A股基本面基本是空的

实测只有 150 只 A股有记录，而且只有营收增长率有值，市盈率、市值、财报日全空。
所以 A股走中线档时，`data_gaps` 里必须明写「A股基本面数据不完整」，
不能返回一个看起来跟美股一样、实际少了一半依据的结论。
"""
from db import pg

_SQL = """
select distinct on (ticker)
       ticker, updated_at, market,
       pe_ratio, forward_pe, ps_ratio, pb_ratio, peg_ratio, ev_to_ebitda,
       market_cap, shares_outstanding, beta,
       revenue_growth_yoy, earnings_growth_yoy,
       gross_margin, operating_margin, profit_margin, roe, fcf_yield,
       analyst_rating, analyst_target_price, analyst_count,
       week_52_high, week_52_low, dividend_yield,
       short_interest_pct, mspr,
       next_earnings_date, last_earnings_date
from stock_fundamentals
where ticker = any(%s)
  and market = %s
order by ticker, updated_at desc
"""


def get(tickers: list[str], market: str) -> dict[str, dict | None]:
    """取一批票的基本面。没有记录的票值是 None，但键一定在。"""
    tickers = [t.upper().strip() for t in (tickers or []) if t and t.strip()]
    if not tickers:
        return {}
    found = {row["ticker"]: dict(row)
             for row in pg.query(_SQL, (tickers, market.upper()))}
    return {t: found.get(t) for t in tickers}
