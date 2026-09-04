# -*- coding: utf-8 -*-
"""端到端验收：2026-09-04 的 601138 必须推两条。

对应设计文档第十节的验收标准。这个文件不测某个函数怎么写，
只测**用户当天真实遇到的那件事，现在会不会被抓住**：

    昨收 63.20 → 今开 63.84 → 盘中最高 65.30 → 回落到 63.55

用户原话：「今天到了一个关键的突破位，我不知道这是突破位，我没有卖，
导致到了突破位后就直接下跌了。」

期望：先在 64.19 报「快到坎了」，再在冲高回落后报「假突破」。
以后谁改算法把这两条改红了，就是改坏了。

文件末尾还有一组**支撑位方向**的端到端用例——601138 这份回归数据的
支撑位恰好是空的（垫底数据被 12% 距离过滤器筛掉了），所以
`core/levels.py` 的 `merge_levels(reverse=True)`（支撑位的合并与排序）
这条路径至今没有被任何端到端测试走过。那组用例专门补这个洞，
用的是手造数据，不改 `core/levels.py` 一个字。
"""
import datetime

from core import alerts, levels, notify

#: 601138 真实日线，2026-08-25 ~ 2026-09-03
REAL_TAIL = [
    ("2026-08-25", 58.75, 60.85, 58.75, 60.15, 75_599_449.0),
    ("2026-08-26", 60.20, 60.80, 59.38, 60.57, 73_340_525.0),
    ("2026-08-27", 62.00, 64.50, 62.00, 63.86, 149_038_810.0),
    ("2026-08-28", 64.04, 66.69, 63.80, 64.04, 157_964_468.0),
    ("2026-08-31", 63.15, 65.00, 62.50, 64.84, 108_990_366.0),
    ("2026-09-01", 64.35, 64.50, 62.80, 62.94, 74_991_338.0),
    ("2026-09-02", 61.72, 62.40, 61.40, 61.80, 61_973_156.0),
    ("2026-09-03", 63.00, 63.99, 62.30, 63.20, 91_922_012.0),
]

POSITION = {"ticker": "601138.SH", "market": "CN", "avg_cost": 61.52,
            "shares": 100.0, "currency": "CNY"}


def _bars():
    pad = [{"date": datetime.date(2026, 1, 1), "open": 50.0, "high": 50.2,
            "low": 49.8, "close": 50.0, "volume": 8e7} for _ in range(130)]
    tail = [{"date": datetime.date.fromisoformat(d), "open": o, "high": h,
             "low": l, "close": c, "volume": v}
            for d, o, h, l, c, v in REAL_TAIL]
    return pad + tail


def _quote(last, high, low=63.30):
    return {"last": last, "high": high, "low": low, "open": 63.84,
            "prev_close": 63.20, "volume": 6e7, "session": "cn",
            "source": "test"}


def _state():
    return {"fired": {}, "warned": {}, "prev_last": {"601138.SH": 63.20},
            "ticks": {}}


def test_top_level_is_the_close_high():
    """验收标准：开盘前算出来的头号阻力就是 64.84。"""
    lv = levels.compute(_bars(), warn_dist=0.01)
    assert round(lv["resistance"][0]["price"], 2) == 64.84
    assert round(lv["resistance"][0]["warn_line"], 2) == 64.19


def test_the_whole_day_produces_exactly_two_alerts():
    """验收标准 1、6：当天推两条，先预警后假突破。"""
    lv = levels.compute(_bars(), warn_dist=0.01)
    state = _state()
    kinds = []
    # 盘中一路走高再回落：63.84 → 64.20（穿过预警线）→ 65.30 → 63.55
    for last, high in [(63.84, 63.90), (64.20, 64.25), (65.30, 65.30),
                       (64.10, 65.30), (63.55, 65.30), (63.55, 65.30)]:
        got = alerts.evaluate("601138.SH", _quote(last, high), lv, state,
                              "cn", avg_volume=9e7, elapsed_minutes=120,
                              today="2026-09-04")
        kinds.extend(a["kind"] for a in got)
    assert kinds == ["接近预警", "假突破"], "实际拿到 %r" % kinds


def test_no_repeat_while_hovering_near_the_warn_line():
    """验收标准 7：在预警线附近震荡不重复报。"""
    lv = levels.compute(_bars(), warn_dist=0.01)
    state = _state()
    kinds = []
    for last in (64.20, 64.15, 64.22, 64.18, 64.25, 64.10):
        got = alerts.evaluate("601138.SH", _quote(last, 64.25), lv, state,
                              "cn", avg_volume=9e7, elapsed_minutes=120,
                              today="2026-09-04")
        kinds.extend(a["kind"] for a in got)
    assert kinds == ["接近预警"], "只该报一次，实际 %r" % kinds


def test_push_text_says_everything_the_user_needs():
    """推送文案要说清：哪只票、发生了什么、手上有多少。"""
    alert = {"ticker": "601138.SH", "kind": "假突破", "price": 64.84,
             "label": "近期高收08-31", "side": "resistance", "session": "cn",
             "quote": _quote(63.55, 65.30)}
    text = notify.format_alert(alert, POSITION)
    for token in ("601138.SH", "假突破", "65.30", "64.84", "63.55", "61.52"):
        assert token in text, "文案里缺了 %s：\n%s" % (token, text)


# ---------------------------------------------------------------------------
# 支撑位方向：merge_levels(reverse=True) 至今没被端到端测过，这里补上。
# 601138 那份真实数据的支撑位是空的（垫底数据被 12% 距离过滤器筛掉了），
# 所以只能手造一份「找得到支撑位」的日线。不改 core/levels.py 一个字，
# 只是让这条一直存在、却从没人真正测过的代码路径第一次被走一遍。
# ---------------------------------------------------------------------------

#: 现价（也是数据最后一天的收盘价）。
_SUPPORT_BASE = 90.0


def _support_bar(day, close, volume=8e7):
    """造一根日线。open/high/low 都等于 close——这组用例只关心 close
    构成的局部低点，不需要盘中区间掺进来添乱。
    """
    return {"date": day, "open": close, "high": close, "low": close,
            "close": close, "volume": volume}


def _support_bars():
    """构造一份**算得出支撑位**的日线：

    * 前 90 天铺平在 `_SUPPORT_BASE`（跟现价一样）——这些天会被
      `compute()` 的方向过滤器天然滤掉（支撑要求价格严格低于现价，
      等于现价的不算），起「垫场」作用，不产生干扰。
    * 后 40 天是真正的戏肉：两个靠得很近的低点（85.00 和 85.90，
      相差 1.06%，在 1.2% 合并阈值以内——应该被合并成一个）+
      一个离得更远的独立低点（81.00，相差 6.05%——应该单独留下）。
    * 最后一天收回 `_SUPPORT_BASE`，也就是当前价。

    这份数据是先用脚本跑过 `levels.compute()` 验证过的：算出来
    正是「85.90 + 81.00」两档，跟下面测试里断言的一致。
    """
    start = datetime.date(2026, 1, 1)
    bars = [_support_bar(start + datetime.timedelta(days=i), _SUPPORT_BASE)
            for i in range(90)]

    day0 = start + datetime.timedelta(days=len(bars))
    closes = [_SUPPORT_BASE] * 40
    # 低点组一：两个局部低点，价格靠得很近 → 应该被合并成一个
    closes[10] = 85.00
    closes[16] = 85.90
    # 低点组二：一个独立低点，离组一够远 → 应该单独留下
    closes[24] = 81.00
    # 给两组低点搭个像样的坡，让它们经得起前后两天不低于它的局部低点判定
    ramp = {
        8: 87.5, 9: 86.2, 11: 86.3, 12: 87.0,
        14: 87.5, 15: 86.5, 17: 86.6, 18: 87.3,
        22: 84.0, 23: 82.5, 25: 82.6, 26: 84.0,
    }
    for i, v in ramp.items():
        closes[i] = v
    # 27 天之后一路走平回到现价，直到最后一天
    for i in range(27, 40):
        closes[i] = _SUPPORT_BASE

    bars += [_support_bar(day0 + datetime.timedelta(days=i), c)
             for i, c in enumerate(closes)]
    return bars


def test_support_levels_are_sorted_from_near_to_far():
    """支撑位按「离现价从近到远」排序，也就是价格从高到低降序。

    支撑都在现价下方，所以「离现价最近」= 价格最高的那个排最前面。
    """
    lv = levels.compute(_support_bars(), warn_dist=0.01)
    prices = [item["price"] for item in lv["support"]]
    assert len(prices) >= 2, "这份数据该算出至少两档支撑，实际 %r" % prices
    assert prices == sorted(prices, reverse=True), \
        "支撑位没有按价格降序排列：%r" % prices


def test_support_levels_within_merge_band_are_combined():
    """挨得太近（1.2% 以内）的支撑位被合并成一个。

    85.00 和 85.90 只差 1.06%，在合并阈值以内，应该只剩一个
    （留下离现价更近的 85.90，85.00 被吞掉）。81.00 离 85.90 有 6.05%，
    在阈值以外，应该单独留下。
    """
    lv = levels.compute(_support_bars(), warn_dist=0.01)
    prices = [round(item["price"], 2) for item in lv["support"]]
    assert prices == [85.90, 81.0], "实际算出来的支撑位是 %r" % prices


def test_support_warn_line_sits_above_the_price():
    """支撑位的预警线画在上方（price * (1 + warn_dist)），跟阻力位反着来。

    阻力位是「价格涨上去撞到」，预警线要往下退，提前反应；
    支撑位是「价格跌下去撞到」，预警线自然要往上退，同样是提前反应。
    """
    lv = levels.compute(_support_bars(), warn_dist=0.01)
    assert lv["support"], "这份数据应该算得出支撑位"
    for item in lv["support"]:
        assert item["side"] == "support"
        expected = round(item["price"] * 1.01, 6)
        assert round(item["warn_line"], 6) == expected
        assert item["warn_line"] > item["price"]
