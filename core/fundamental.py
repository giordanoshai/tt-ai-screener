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

## 单位约定：交出去的增长率、利润率、收益率一律是百分点

库里那些比率列存的是**小数**（2026-08-28 实测）：AAPL 的 `revenue_growth_yoy`
是 0.164，意思是「营收同比增长 16.4%」；NVDA 是 1.059，意思是「增长 105.9%」。

`core/scoring.py` 的阈值全是按百分点写的（`revenue >= 20` 读作「增长 20%」）。
拿小数直接去比的后果实测过：全市场 2172 只美股里 `profit_margin >= 3` 只有 1 只
命中、`>= 20` 是 0 只、`roe > 10` 是 0 只——净利率那 7 分和 ROE 那 5 分全场归零，
NVDA（营收 +106%、净利率 64%）和一家增速 0.5% 的公司拿一样的分。显示串
`f"营收同比增长 {revenue:.1f}%"` 还会把 NVDA 的 +105.9% 印成「1.1%」。

所以本模块定下这条约定：

> **凡是 `core/fundamental.py` 交出去的增长率、利润率、ROE、收益率，
> 一律是百分点。**

换算的列见 `_RATIO_COLUMNS`。**不换算**的列以及为什么：

- `pe_ratio` / `forward_pe` / `ps_ratio` / `pb_ratio` / `peg_ratio` /
  `ev_to_ebitda`：倍数，本来就不是百分比，乘 100 会把 27.5 倍市盈率变成 2750。
- `market_cap` / `shares_outstanding` / `analyst_target_price` /
  `week_52_high` / `week_52_low`：金额和股数。
- `beta`：相对大盘的波动倍数，1.0 就是跟大盘同步。
- `mspr`：内部人交易情绪分，取值 -100..100，本来就是分值不是比率。
- `analyst_rating` / `analyst_count` / 两个 `*_earnings_date`：评级、家数、日期。

## `short_interest_pct` 这个列名在撒谎

它存的不是百分比，是**股数**：全市场中位数 7,121,538，最大 294,021,600，
NVDA 那天是 292,667,375 股。名字里带 `pct` 会让调用方当成百分比去显示。
所以本模块返回时把它**改名成 `short_interest_shares`**，原键不再出现——
留着原名只会继续骗人。别处没有经过本模块消费这个字段
（`web/app.py:744` 是它自己直接查库，不走这里）。
"""
from db import pg

#: 库里存小数（0.164 = 16.4%），本模块一律按百分点交出去
_RATIO_COLUMNS = (
    "revenue_growth_yoy", "earnings_growth_yoy",
    "gross_margin", "operating_margin", "profit_margin",
    "roe", "fcf_yield", "dividend_yield",
)

#: 列名在撒谎：存的是股数不是百分比，交出去时改成不会骗人的名字
_SHORT_INTEREST_SRC = "short_interest_pct"
_SHORT_INTEREST_DST = "short_interest_shares"

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


def _shape(row: dict) -> dict:
    """把一行库记录整成本模块对外的形状：比率换成百分点、空头持仓改名。"""
    out = dict(row)
    for col in _RATIO_COLUMNS:
        value = out.get(col)
        if value is not None:
            out[col] = round(float(value) * 100, 2)
    if _SHORT_INTEREST_SRC in out:
        shares = out.pop(_SHORT_INTEREST_SRC)
        out[_SHORT_INTEREST_DST] = None if shares is None else float(shares)
    return out


def get(tickers: list[str], market: str) -> dict[str, dict | None]:
    """取一批票的基本面。没有记录的票值是 None，但键一定在。

    返回的增长率/利润率/ROE/收益率都是**百分点**（16.4 = 增长 16.4%），
    库里存的小数已经在这里换算过了。见模块 docstring。
    """
    tickers = [t.upper().strip() for t in (tickers or []) if t and t.strip()]
    if not tickers:
        return {}
    found = {row["ticker"]: _shape(row)
             for row in pg.query(_SQL, (tickers, market.upper()))}
    return {t: found.get(t) for t in tickers}
