# -*- coding: utf-8 -*-
"""core/alerts.py 的测试。

这个模块决定「什么时候叫醒用户」。写松了会刷屏（用户两天就静音，
等于没有），写紧了会漏掉真正该动手的时刻。所以每条降噪规则
都单独有测试。

回归重点：
* 盘中量比必须按时间折算，否则「有效突破」永远判不出来。
* 预警必须是「穿越触发」，在预警线附近磨蹭不能反复报。
* 盘前不判「有效突破」，因为盘前成交量只占全天 1%~2%，量能不可比。
* 接近预警同样要采样点确认，一根孤立的插针穿过预警线不算数。
* 冷却期数交易日，不数自然日——周末不该把冷却提前两天解除。
"""
import datetime

from core import alerts


def _quote(last, high=None, low=None, session="cn", volume=0.0):
    return {"last": last,
            "high": high if high is not None else last,
            "low": low if low is not None else last,
            "open": last, "prev_close": last,
            "volume": volume, "session": session, "source": "test"}


def _level(price, side, warn_dist=0.01, label="测试位"):
    warn = price * (1 - warn_dist) if side == "resistance" \
        else price * (1 + warn_dist)
    return {"price": price, "label": label, "side": side, "warn_line": warn}


def _state():
    return {"fired": {}, "warned": {}, "prev_last": {}, "ticks": {}}


class TestIntradayVolumeRatio:
    def test_scales_by_elapsed_time(self):
        """盘中成交量是累计值，直接跟 20 日均量比会永远偏低，
        「有效突破」就永远判不出来。必须按已开盘时间折算。

        开盘半小时（240 分钟里的 30 分钟）跑了 20 日均量的 1/8，
        折算后正好是 1.0 倍——不多不少。
        """
        got = alerts.intraday_volume_ratio(
            volume=1_000_000, avg_volume=8_000_000,
            elapsed_minutes=30, session_name="cn")
        assert round(got, 2) == 1.0

    def test_without_scaling_it_would_look_tiny(self):
        """对照：不折算的话同样的量只有 0.125 倍，永远够不到 1.2 的门槛。"""
        assert 1_000_000 / 8_000_000 < alerts.VOL_CONFIRM

    def test_returns_none_when_too_early(self):
        """开盘头 15 分钟分母太小，量比会剧烈跳动，不做判定。"""
        assert alerts.intraday_volume_ratio(
            volume=100, avg_volume=8_000_000,
            elapsed_minutes=5, session_name="cn") is None

    def test_returns_none_for_premarket(self):
        """盘前没有可比的均量口径（SESSION_MINUTES 里就没有它）。"""
        assert alerts.intraday_volume_ratio(
            volume=100, avg_volume=8_000_000,
            elapsed_minutes=100, session_name="us_pre") is None


class TestClassify:
    def test_fake_breakout(self):
        """冲过关键位又跌回下方——这就是 601138 在 2026-09-04 的情况。"""
        assert alerts.classify("resistance", 64.84, _quote(63.55, high=65.30),
                               vol_ratio=0.9, premarket=False) == "假突破"

    def test_valid_breakout_needs_volume(self):
        assert alerts.classify("resistance", 64.84, _quote(65.50, high=65.60),
                               vol_ratio=1.5, premarket=False) == "有效突破"

    def test_holding_above_without_volume_is_not_pushed(self):
        """站上去了但没量——是流水账，不推。"""
        assert alerts.classify("resistance", 64.84, _quote(65.50, high=65.60),
                               vol_ratio=0.9, premarket=False) is None

    def test_premarket_never_reports_valid_breakout(self):
        """盘前成交量只占全天 1%~2%，「放量突破」这个判断没有意义。"""
        assert alerts.classify("resistance", 64.84,
                               _quote(65.50, high=65.60, session="pr"),
                               vol_ratio=9.9, premarket=True) is None

    def test_break_support(self):
        assert alerts.classify("support", 62.16, _quote(61.90, low=61.80),
                               vol_ratio=1.0, premarket=False) == "跌破"

    def test_touching_support_and_holding_is_not_pushed(self):
        assert alerts.classify("support", 62.16, _quote(62.50, low=62.10),
                               vol_ratio=1.0, premarket=False) is None


class TestCrossingTrigger:
    def test_fires_when_crossing_from_below(self):
        """从远处走过来、穿过预警线的那一刻才报。"""
        assert alerts.crossed_warn_line(
            "resistance", 64.19, prev_last=63.90,
            now_quote=_quote(64.25)) is True

    def test_does_not_fire_when_already_inside(self):
        """本来就在预警线上方磨蹭，不报——这条把预警从 27 条压到 17 条。"""
        assert alerts.crossed_warn_line(
            "resistance", 64.19, prev_last=64.30,
            now_quote=_quote(64.25)) is False

    def test_support_crosses_downward(self):
        assert alerts.crossed_warn_line(
            "support", 62.78, prev_last=63.10,
            now_quote=_quote(62.70)) is True

    def test_no_previous_sample_does_not_fire(self):
        """进程刚起来没有上一个采样点，等下一轮，不瞎报。"""
        assert alerts.crossed_warn_line(
            "resistance", 64.19, prev_last=None,
            now_quote=_quote(64.25)) is False


class TestEvaluateDedup:
    def test_same_level_not_repeated_within_cooldown(self):
        state = _state()
        lv = {"resistance": [_level(64.84, "resistance")], "support": []}
        state["prev_last"] = {"601138.SH": 63.90}

        # 穿过预警线只算第一个确认点，常规时段要连着站住两个才推。
        first = alerts.evaluate("601138.SH", _quote(64.25), lv, state,
                                "cn", avg_volume=8e7, elapsed_minutes=60,
                                today="2026-09-04")
        assert first == [], "穿一次线不够，还要再站住一个采样点"
        second = alerts.evaluate("601138.SH", _quote(64.25), lv, state,
                                 "cn", avg_volume=8e7, elapsed_minutes=61,
                                 today="2026-09-04")
        assert [a["kind"] for a in second] == ["接近预警"]

        # 假装价格跑远了又走回来，重新攒够确认——冷却期内仍然不该再报。
        state["prev_last"]["601138.SH"] = 63.90
        for elapsed in (62, 63):
            again = alerts.evaluate("601138.SH", _quote(64.25), lv, state,
                                    "cn", avg_volume=8e7,
                                    elapsed_minutes=elapsed,
                                    today="2026-09-04")
            assert again == [], "冷却期内同一个位不该重复报"

    def test_confirm_ticks_required_in_premarket(self):
        """盘前要连续 3 个采样点确认，前两次不报。"""
        state = _state()
        lv = {"resistance": [], "support": [_level(62.16, "support")]}
        quote = _quote(61.90, low=61.80, session="pr")
        for _ in range(2):
            assert alerts.evaluate("AAPL", quote, lv, state, "us_pre",
                                   avg_volume=1e6, elapsed_minutes=100,
                                   today="2026-09-04") == []
        third = alerts.evaluate("AAPL", quote, lv, state, "us_pre",
                                avg_volume=1e6, elapsed_minutes=100,
                                today="2026-09-04")
        assert [a["kind"] for a in third] == ["跌破"]

    def test_regular_session_needs_only_two_ticks(self):
        state = _state()
        lv = {"resistance": [], "support": [_level(62.16, "support")]}
        quote = _quote(61.90, low=61.80)
        assert alerts.evaluate("601138.SH", quote, lv, state, "cn",
                               avg_volume=8e7, elapsed_minutes=60,
                               today="2026-09-04") == []
        second = alerts.evaluate("601138.SH", quote, lv, state, "cn",
                                 avg_volume=8e7, elapsed_minutes=61,
                                 today="2026-09-04")
        assert [a["kind"] for a in second] == ["跌破"]


class TestWarnNeedsConfirmation:
    """接近预警也要采样点确认——盘前尤其要，那里最容易出孤立的假价格。

    盘前成交量只占全天 1%~2%，几百股就能把价格打出去一大截；而预警线
    比关键位还低 1%，是最容易被一根插针穿过的一道线。
    """

    def test_premarket_needs_three_samples(self):
        """盘前要连着 3 个采样点站在预警线内侧才推，前两次不推。"""
        state = _state()
        lv = {"resistance": [_level(200.0, "resistance")], "support": []}
        state["prev_last"] = {"AAPL": 196.0}      # 预警线 198.0，起点在线外

        # 第 1 根穿过预警线，第 2 根还站着，都不该推
        for last in (198.5, 198.6):
            got = alerts.evaluate("AAPL", _quote(last, session="pr"), lv,
                                  state, "us_pre", avg_volume=1e6,
                                  elapsed_minutes=100, today="2026-09-04")
            assert got == [], "盘前只站住两个采样点还不够"

        third = alerts.evaluate("AAPL", _quote(198.7, session="pr"), lv,
                                state, "us_pre", avg_volume=1e6,
                                elapsed_minutes=100, today="2026-09-04")
        assert [a["kind"] for a in third] == ["接近预警"]

    def test_isolated_spike_never_warns(self):
        """一根孤立的插针穿过预警线又掉回去——一条都不该推。

        这正是加这道闸要挡的东西：盘前几百股就能打出这么一根。
        """
        state = _state()
        lv = {"resistance": [_level(200.0, "resistance")], "support": []}
        state["prev_last"] = {"AAPL": 196.0}
        kinds = []
        for last in (198.5, 196.2, 196.1, 198.4, 196.0):   # 穿一下就掉回去
            got = alerts.evaluate("AAPL", _quote(last, session="pr"), lv,
                                  state, "us_pre", avg_volume=1e6,
                                  elapsed_minutes=100, today="2026-09-04")
            kinds.extend(a["kind"] for a in got)
        assert kinds == [], "插针不该报，实际 %r" % kinds


class TestCooldownCountsTradingDays:
    """冷却期数的是**交易日**，不是自然日。

    按自然日的话，周五报过的位下周三（+5 天）就解冻了，比设计文档说的
    5 个交易日早两天。设计阶段那组「45 个交易日、单只票 35 条、47% 的天
    在响」的噪音实测就是交易日口径跑出来的。
    """

    def test_friday_level_still_cooling_on_wednesday(self):
        """周五 09-04 报过，下周三 09-09 只隔了 3 个交易日，还在冷却。"""
        store = {"601138.SH|64.84": "2026-09-04"}
        assert alerts._on_cooldown(store, "601138.SH|64.84",
                                   "2026-09-09", alerts.FIRE_COOLDOWN_DAYS)

    def test_friday_level_released_next_friday(self):
        """下周五 09-11 正好隔满 5 个交易日，解除冷却。"""
        store = {"601138.SH|64.84": "2026-09-04"}
        assert not alerts._on_cooldown(store, "601138.SH|64.84",
                                       "2026-09-11", alerts.FIRE_COOLDOWN_DAYS)

    def test_weekend_is_not_a_trading_day(self):
        """周五到周一只算 1 个交易日，中间的周六周日不算。"""
        assert alerts._trading_days_between(
            datetime.date(2026, 9, 4), datetime.date(2026, 9, 7)) == 1


class TestRealCase20260904:
    """验收标准：601138 在 2026-09-04 必须推两条，先预警再假突破。"""

    def test_two_alerts_in_order(self):
        state = _state()
        lv = {"resistance": [_level(64.84, "resistance",
                                    label="近期高收08-31")],
              "support": [_level(62.16, "support", label="半年低04-30")]}
        state["prev_last"] = {"601138.SH": 63.20}   # 昨收，在预警线之外

        # 盘中涨到 64.20，穿过预警线 64.19；常规时段要两个采样点确认，
        # 所以第一根只是攒确认，站住第二根才推。
        assert alerts.evaluate("601138.SH", _quote(64.20, high=64.20),
                               lv, state, "cn", avg_volume=9e7,
                               elapsed_minutes=60, today="2026-09-04") == []
        first = alerts.evaluate("601138.SH", _quote(64.20, high=64.20),
                                lv, state, "cn", avg_volume=9e7,
                                elapsed_minutes=61, today="2026-09-04")
        assert [a["kind"] for a in first] == ["接近预警"]
        assert round(first[0]["price"], 2) == 64.84

        # 冲到 65.30 又跌回 63.55，两个采样点确认
        alerts.evaluate("601138.SH", _quote(63.55, high=65.30), lv, state,
                        "cn", avg_volume=9e7, elapsed_minutes=120,
                        today="2026-09-04")
        second = alerts.evaluate("601138.SH", _quote(63.55, high=65.30),
                                 lv, state, "cn", avg_volume=9e7,
                                 elapsed_minutes=121, today="2026-09-04")
        assert [a["kind"] for a in second] == ["假突破"]
