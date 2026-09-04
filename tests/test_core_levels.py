# -*- coding: utf-8 -*-
"""core/levels.py 的测试。

这个模块是整个盯盘功能的核心，也是最容易写错的地方——设计阶段的第一版
就因为「用最高价找关键位」而漏掉了 2026-09-04 那次真实的假突破。
所以这里的测试分两类：

* **行为测试**——用手工构造的日线，把每条规则单独测透（插针不算数、
  合并挨得近的位、只留最近的两个、预警线画在哪）。
* **回归测试**——用 601138 在 2026-09-04 之前的真实日线，断言算出来的
  头号阻力就是 64.84（8/31 的收盘高点）。这条是本功能的验收标准，
  写死在测试里，以后谁改算法把它改红了，就是改坏了。

不连数据库：真实日线以固定数据内联在测试里。这条测试的价值在于
「算法对不对」，不该因为库里数据变了而红。
"""
import datetime

from core import levels


def _bar(day, o, h, l, c, v=1_000_000.0):
    """造一根日线。day 是 '2026-08-28' 这样的字符串。"""
    return {
        "date": datetime.date.fromisoformat(day),
        "open": o, "high": h, "low": l, "close": c, "volume": v,
    }


class TestLocalExtremes:
    def test_finds_local_high_by_close(self):
        """前后各 wing 天的收盘价都不超过它，才算局部高点。"""
        bars = [
            _bar("2026-08-01", 10, 11, 9, 10.0),
            _bar("2026-08-02", 10, 11, 9, 10.5),
            _bar("2026-08-03", 10, 11, 9, 12.0),   # 局部最高收盘
            _bar("2026-08-04", 10, 11, 9, 10.5),
            _bar("2026-08-05", 10, 11, 9, 10.0),
        ]
        got = levels.local_extremes(bars, wing=2, field="close", kind="high")
        assert [v for _, v in got] == [12.0]

    def test_ignores_spike_high_when_using_close(self):
        """插针不算数：最高价冲很高但收盘吐回来的那天，不该被当成关键位。

        这就是设计阶段第一版翻车的地方——8/28 冲到 66.69 又收回 64.04，
        用最高价找位会把 66.69 当成阻力，把下面真正的坎 64.84 盖住。
        """
        bars = [
            _bar("2026-08-26", 60, 61.0, 59.5, 60.5),
            _bar("2026-08-27", 63, 64.50, 62.0, 63.86),
            _bar("2026-08-28", 64, 66.69, 63.8, 64.04),   # 长上影线，插针
            _bar("2026-08-31", 63, 65.00, 62.5, 64.84),   # 真正的高收盘
            _bar("2026-09-01", 64, 64.50, 62.8, 62.94),
            _bar("2026-09-02", 61, 62.40, 61.4, 61.80),
        ]
        by_close = [v for _, v in
                    levels.local_extremes(bars, wing=2, field="close", kind="high")]
        by_high = [v for _, v in
                   levels.local_extremes(bars, wing=2, field="high", kind="high")]
        assert by_close == [64.84], "用收盘价应该找到 64.84"
        assert by_high == [66.69], "用最高价会找到那根插针——这正是要避免的"

    def test_finds_local_low(self):
        bars = [
            _bar("2026-08-01", 10, 11, 9, 10.0),
            _bar("2026-08-02", 10, 11, 9, 9.5),
            _bar("2026-08-03", 10, 11, 9, 8.0),
            _bar("2026-08-04", 10, 11, 9, 9.5),
            _bar("2026-08-05", 10, 11, 9, 10.0),
        ]
        got = levels.local_extremes(bars, wing=2, field="close", kind="low")
        assert [v for _, v in got] == [8.0]

    def test_edges_have_no_room_to_judge(self):
        """开头和结尾各 wing 根窗口不完整，没法判断，必须跳过。"""
        bars = [_bar("2026-08-%02d" % (i + 1), 10, 11, 9, 10.0 + i)
                for i in range(5)]
        got = levels.local_extremes(bars, wing=2, field="close", kind="high")
        # 最后一根收盘 14.0 最高，但它在边缘，窗口不完整，不算
        assert got == []


class TestTouchCount:
    def test_counts_days_that_straddle_the_price(self):
        """被测试过几次 = 有几天的最高最低区间罩住了这个价。"""
        bars = [
            _bar("2026-08-01", 10, 11.0, 9.0, 10.0),   # 罩住 10.5
            _bar("2026-08-02", 10, 10.2, 9.0, 10.0),   # 罩不住
            _bar("2026-08-03", 10, 12.0, 10.4, 11.0),  # 罩住
        ]
        assert levels.touch_count(bars, 10.5, band=0.0) == 2


class TestFiltering:
    def test_merges_levels_that_are_too_close(self):
        """相差 1.2% 以内算同一道坎，只留一个，否则同一个位置报三次。"""
        merged = levels.merge_levels(
            [{"price": 64.84, "label": "a"},
             {"price": 65.00, "label": "b"},    # 距 64.84 只有 0.25%
             {"price": 70.00, "label": "c"}],
            reverse=False)
        assert [round(x["price"], 2) for x in merged] == [64.84, 70.00]

    def test_keeps_only_nearest_two(self):
        """上下各只留最近的两道坎，更远的今天碰不到。"""
        got = levels.trim([{"price": p, "label": str(p)} for p in
                           (64.0, 67.0, 70.0, 75.0)], keep=2)
        assert [x["price"] for x in got] == [64.0, 67.0]

    def test_warn_line_is_one_percent_inside(self):
        """阻力的预警线往下退 1%，支撑的往上退 1%。"""
        assert round(levels.warn_line_of(64.84, "resistance", 0.01), 2) == 64.19
        assert round(levels.warn_line_of(62.16, "support", 0.01), 2) == 62.78


class TestRealCase601138:
    """回归测试：2026-09-04 那次真实的假突破，本功能的验收标准。"""

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

    def _bars(self):
        # 前面垫 130 根低位平盘，保证窗口够长，且不会造出高于 64.84 的位
        pad = [_bar("2026-01-01", 50.0, 50.2, 49.8, 50.0) for _ in range(130)]
        tail = [_bar(d, o, h, l, c, v) for d, o, h, l, c, v in self.REAL_TAIL]
        return pad + tail

    def test_top_resistance_is_the_close_high_not_the_spike(self):
        """头号阻力必须是 8/31 的收盘高点 64.84，不能是 8/28 的插针 66.69。"""
        out = levels.compute(self._bars(), warn_dist=0.01)
        top = out["resistance"][0]
        assert round(top["price"], 2) == 64.84, (
            "头号阻力应该是 64.84（8/31 收盘高点），实际拿到 %s" % top["price"])
        assert round(top["warn_line"], 2) == 64.19

    def test_the_spike_66_69_is_not_a_level(self):
        """66.69 那根插针不能出现在任何一个关键位里。"""
        out = levels.compute(self._bars(), warn_dist=0.01)
        prices = [round(x["price"], 2)
                  for x in out["resistance"] + out["support"]]
        assert 66.69 not in prices

    def test_short_history_returns_nothing(self):
        """数据不够长时算出来的位不可信，宁可不报。"""
        out = levels.compute([_bar("2026-08-01", 10, 11, 9, 10.0)] * 30)
        assert out == {"resistance": [], "support": []}
