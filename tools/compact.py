# -*- coding: utf-8 -*-
"""交出去之前把返回体压一压。

## 为什么需要这么一层

这些工具的返回体是直接进 Claude 上下文的，每一个字符都要花钱。
2026-09-03 实测全部 12 个工具一次调用的体积（按 3 字符 ≈ 1 token 折算）：

    list_watchlist    66,672 字符 ≈ 22,000 token   ← 一个顶其他仨
    scan_swing        23,287                7,800
    review_positions  16,092                5,400
    screen_stocks     15,760                5,300
    detect_catalysts  14,926                5,000
    analyze_ticker     8,832                2,900

拆开看，最大的两块浪费根本不是信息，是格式：

1. **小数一位没截。** `"close": 142.229995727539` —— 一个字段白占 10 个字符。
   自选那个工具里有 1,224 个这种数字，白占 11,310 字符，占它总量的 17%。
2. **`indent=2` 的缩进。** 155 行 × 13 个字段，光空格和换行就是 14%。
   缩进是给人看的，而这些 JSON 只有 Claude 读，它不需要。

## 用这一层的时候必须自己确认字段量级

`digits` 表**要逐个字段写，不能一刀切**，因为同一张表里两种量级混着放：

* `pct_chg`、`dist_ma20_pct`（`core/` 交出来的那份）是**百分点**——
  2.61 表示涨 2.61%，截 2 位小数没问题。
* `stock_ohlcv_daily.dist_ma20_pct`、`atr_pct`（**直接查库**的那份）是
  **小数**——0.0443 表示 4.43%，同样截 2 位就变成 0.04，精度全丢。

这两种量级差 100 倍的坑在这个仓库里已经炸过三次，见 CLAUDE.md 里
「比率字段的单位约定」那一节。往 `digits` 里加字段之前先看一眼它是哪一种。

## 空值不省略

看着也像浪费，实测却只有 1%（自选 155 行里空值总共 19 个），
代价却是返回体的形状随数据变化——有指标的票 13 个字段、没指标的 7 个，
调用方每读一个字段都得先判断在不在。不值这个价。
"""

#: 时间戳只留前 10 个字符，也就是 `2026-08-29`。
#: 原样交出去的话是 `2026-08-29 10:30:31.444795+00:00`——看自选、看持仓
#: 没有人需要知道微秒和时区偏移。
_DATE_LEN = 10


def compact_row(row: dict, digits: dict, date_only=()) -> dict:
    """把一行压成交出去的形状。

    row:       原样从数据库读出来的一行
    digits:    `{字段名: 保留几位小数}`。**逐个字段写**，理由见模块注释
    date_only: 这些字段只保留日期部分

    没列进 `digits` / `date_only` 的字段原样带出去，空值也原样保留。
    """
    out = {}
    for key, value in row.items():
        if value is None:
            out[key] = None
        elif key in digits:
            out[key] = round(float(value), digits[key])
        elif key in date_only:
            out[key] = str(value)[:_DATE_LEN]
        else:
            out[key] = value
    return out


def compact_rows(rows, digits: dict, date_only=()) -> list[dict]:
    """`compact_row` 的批量版。"""
    return [compact_row(row, digits, date_only) for row in rows]
