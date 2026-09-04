# -*- coding: utf-8 -*-
"""watchdog.py 的测试。

主循环只做编排，判断逻辑都在已经测透的纯函数里，所以这里测的是
**接线对不对**：

* 持仓每轮都重新读——用户在 Claude.ai 里加仓平仓，下一分钟就得生效。
  这是用户明确提的要求，写成回归测试。
* 非交易时段不发任何请求——省下的是真金白银的接口调用。
* 某一路挂了不能让整轮死掉。
* 状态能存能读，进程重启不重复轰炸。

数据库和网络全部 mock，不真连、不真发。
"""
import datetime
from unittest.mock import patch

import watchdog

UTC = datetime.timezone.utc


def _utc(text):
    return datetime.datetime.fromisoformat(text).replace(tzinfo=UTC)


POSITIONS = [
    {"ticker": "601138.SH", "market": "CN", "avg_cost": 61.52,
     "shares": 100.0, "currency": "CNY"},
    {"ticker": "RKLB", "market": "US", "avg_cost": 82.90,
     "shares": 17.0, "currency": "USD"},
]


class TestLoadPositions:
    def test_reads_every_round_not_cached(self):
        """持仓每轮都要重新读——加仓平仓下一分钟生效。"""
        with patch("watchdog.pg.query", return_value=POSITIONS) as spy:
            watchdog.load_positions()
            watchdog.load_positions()
        assert spy.call_count == 2, "不许缓存持仓"

    def test_query_uses_percent_placeholder_style(self):
        """这个仓库是 psycopg2，占位符是 %s 不是 ?。"""
        with patch("watchdog.pg.query", return_value=[]) as spy:
            watchdog.load_positions()
        assert "?" not in spy.call_args[0][0]


class TestLoadBars:
    def test_query_filters_by_market(self):
        """stock_ohlcv_daily 一张表装着美股和 A股，漏掉 market 会混进来。"""
        with patch("watchdog.pg.query", return_value=[]) as spy:
            watchdog.load_bars("601138.SH", "CN")
        sql, params = spy.call_args[0][0], spy.call_args[0][1]
        assert "market" in sql
        assert "CN" in params

    def test_no_correlated_subquery(self):
        """绝不用关联子查询取最新日期——实测 275 万行顺序扫描 6.7 秒。"""
        with patch("watchdog.pg.query", return_value=[]) as spy:
            watchdog.load_bars("601138.SH", "CN")
        sql = spy.call_args[0][0].lower()
        assert "max(date)" not in sql
        assert "order by date desc" in sql

    def test_returns_ascending_order(self):
        """查出来是倒序，交出去必须是正序（最后一根最新）。"""
        rows = [{"date": datetime.date(2026, 9, 3), "close": 63.20},
                {"date": datetime.date(2026, 9, 2), "close": 61.80}]
        with patch("watchdog.pg.query", return_value=rows):
            got = watchdog.load_bars("601138.SH", "CN")
        assert got[-1]["date"] == datetime.date(2026, 9, 3)


class TestElapsedMinutes:
    def test_cn_morning(self):
        """北京 10:00 = UTC 02:00 = 开盘后 30 分钟。"""
        assert watchdog.elapsed_minutes(_utc("2026-09-04 02:00"), "cn") == 30

    def test_cn_afternoon_skips_lunch(self):
        """北京 14:00 = UTC 06:00：上午 120 + 下午 60 = 180，午休不算。"""
        assert watchdog.elapsed_minutes(_utc("2026-09-04 06:00"), "cn") == 180

    def test_us_rth(self):
        """美东 10:30（夏令时）= UTC 14:30 = 开盘后 60 分钟。"""
        assert watchdog.elapsed_minutes(_utc("2026-09-04 14:30"), "us_rth") == 60


class TestRunOnce:
    def test_no_requests_outside_trading_hours(self):
        """非交易时段一个请求都不发。"""
        state = watchdog.empty_state()
        # 北京 08:05 = UTC 00:05，A股没开、美股盘后已过
        with patch("watchdog.quotes.get") as spy_quotes, \
             patch("watchdog.pg.query", return_value=POSITIONS):
            assert watchdog.run_once(state, _utc("2026-09-04 00:05")) == []
        spy_quotes.assert_not_called()

    def test_quote_failure_does_not_raise(self):
        """取价挂了这一轮就空过，不能把进程搞死。"""
        state = watchdog.empty_state()
        with patch("watchdog.pg.query", return_value=POSITIONS), \
             patch("watchdog.quotes.get", side_effect=RuntimeError("boom")):
            assert watchdog.run_once(state, _utc("2026-09-04 02:00")) == []

    def test_cn_ticker_skipped_during_us_only_hours(self):
        """美股时段不去拉 A股的价——A股那时候根本没开。"""
        state = watchdog.empty_state()
        with patch("watchdog.pg.query", return_value=POSITIONS), \
             patch("watchdog.quotes.get", return_value={}) as spy:
            watchdog.run_once(state, _utc("2026-09-04 15:00"))   # 美股常规
        assert spy.call_args[0][0].get("CN") is None

    def test_alert_gets_sent(self):
        """走通一条：有持仓、有报价、撞上关键位 → 发一条推送。"""
        state = watchdog.empty_state()
        state["prev_last"]["601138.SH"] = 63.20
        state["ticks"]["601138.SH|64.84"] = 1     # 已经确认过一次
        bars = [{"date": datetime.date(2026, 9, 3), "open": 63.0,
                 "high": 63.99, "low": 62.3, "close": 63.20,
                 "volume": 9e7}]
        quote = {"601138.SH": {"last": 63.55, "high": 65.30, "low": 63.30,
                               "open": 63.84, "prev_close": 63.20,
                               "volume": 6e7, "session": "cn",
                               "source": "test"}}
        fake_levels = {"resistance": [{"price": 64.84,
                                       "label": "近期高收08-31",
                                       "side": "resistance",
                                       "warn_line": 64.19}],
                       "support": []}
        with patch("watchdog.pg.query", return_value=POSITIONS[:1]), \
             patch("watchdog.load_bars", return_value=bars), \
             patch("watchdog.levels.compute", return_value=fake_levels), \
             patch("watchdog.quotes.get", return_value=quote), \
             patch("watchdog.notify.send", return_value=True) as spy_send:
            out = watchdog.run_once(state, _utc("2026-09-04 02:00"))
        assert [a["kind"] for a in out] == ["假突破"]
        assert spy_send.call_count == 1

    def test_daily_cap_blocks_before_send(self):
        """保险丝必须挡在推送之前——这是集成测试兜底，纯函数
        `_within_daily_cap` 本身已经在 TestDailyCap 测过，这里测的是
        「顺序」：以后谁重构把两行位置换了，这条测试要能红。"""
        state = watchdog.empty_state()
        state["prev_last"]["601138.SH"] = 63.20
        state["ticks"]["601138.SH|64.84"] = 1
        bars = [{"date": datetime.date(2026, 9, 3), "open": 63.0,
                 "high": 63.99, "low": 62.3, "close": 63.20,
                 "volume": 9e7}]
        quote = {"601138.SH": {"last": 63.55, "high": 65.30, "low": 63.30,
                               "open": 63.84, "prev_close": 63.20,
                               "volume": 6e7, "session": "cn",
                               "source": "test"}}
        fake_levels = {"resistance": [{"price": 64.84,
                                       "label": "近期高收08-31",
                                       "side": "resistance",
                                       "warn_line": 64.19}],
                       "support": []}
        with patch("watchdog.pg.query", return_value=POSITIONS[:1]), \
             patch("watchdog.load_bars", return_value=bars), \
             patch("watchdog.levels.compute", return_value=fake_levels), \
             patch("watchdog.quotes.get", return_value=quote), \
             patch("watchdog._within_daily_cap", return_value=False), \
             patch("watchdog.notify.send", return_value=True) as spy_send:
            out = watchdog.run_once(state, _utc("2026-09-04 02:00"))
        assert out == []
        spy_send.assert_not_called()

    def test_send_failure_is_not_silently_swallowed(self):
        """推送失败不能被吞掉：不能出现在返回列表里，也不能占掉
        冷却和配额——不然用户手机上什么都没有，但系统以为已经报过了，
        下一轮不会重试。这正是 config.py 里点名要避免的
        「以为在盯盘、其实什么都没发」。"""
        state = watchdog.empty_state()
        state["prev_last"]["601138.SH"] = 63.20
        state["ticks"]["601138.SH|64.84"] = 1
        bars = [{"date": datetime.date(2026, 9, 3), "open": 63.0,
                 "high": 63.99, "low": 62.3, "close": 63.20,
                 "volume": 9e7}]
        quote = {"601138.SH": {"last": 63.55, "high": 65.30, "low": 63.30,
                               "open": 63.84, "prev_close": 63.20,
                               "volume": 6e7, "session": "cn",
                               "source": "test"}}
        fake_levels = {"resistance": [{"price": 64.84,
                                       "label": "近期高收08-31",
                                       "side": "resistance",
                                       "warn_line": 64.19}],
                       "support": []}
        with patch("watchdog.pg.query", return_value=POSITIONS[:1]), \
             patch("watchdog.load_bars", return_value=bars), \
             patch("watchdog.levels.compute", return_value=fake_levels), \
             patch("watchdog.quotes.get", return_value=quote), \
             patch("watchdog.notify.send", return_value=False):
            out = watchdog.run_once(state, _utc("2026-09-04 02:00"))
        assert out == []
        assert "601138.SH|64.84" not in state["fired"], "冷却记录必须撤销，下一轮才能重试"
        assert state["daily"]["total"] == 0, "配额必须撤销"
        assert state["daily"]["by_ticker"].get("601138.SH", 0) == 0


class TestDailyCap:
    """保险丝：不是用来调节奏的，是防止算法出 bug 时把手机炸掉。"""

    def test_per_ticker_cap(self):
        state = watchdog.empty_state()
        for _ in range(4):
            assert watchdog._within_daily_cap(
                state, "AAPL", "2026-09-04") is True
        assert watchdog._within_daily_cap(state, "AAPL", "2026-09-04") is False

    def test_resets_next_day(self):
        state = watchdog.empty_state()
        for _ in range(4):
            watchdog._within_daily_cap(state, "AAPL", "2026-09-04")
        assert watchdog._within_daily_cap(state, "AAPL", "2026-09-05") is True

    def test_global_cap_across_tickers(self):
        """全天总量上限，跨票累计。用不同票名避开每票那道闸。"""
        state = watchdog.empty_state()
        for i in range(25):
            assert watchdog._within_daily_cap(
                state, "T%d" % i, "2026-09-04") is True
        assert watchdog._within_daily_cap(state, "T99", "2026-09-04") is False


class TestState:
    def test_round_trip(self, tmp_path):
        path = str(tmp_path / "state.json")
        state = watchdog.empty_state()
        state["fired"]["601138.SH|64.84"] = "2026-09-04"
        watchdog.save_state(path, state)
        assert watchdog.load_state(path)["fired"] == state["fired"]

    def test_missing_file_gives_empty_state(self, tmp_path):
        assert watchdog.load_state(
            str(tmp_path / "nope.json")) == watchdog.empty_state()

    def test_broken_file_gives_empty_state(self, tmp_path):
        """状态文件坏了不能让进程起不来——大不了重新攒。"""
        path = tmp_path / "bad.json"
        path.write_text("{ not json", encoding="utf-8")
        assert watchdog.load_state(str(path)) == watchdog.empty_state()


class TestTodayPerMarket:
    """「今天」按市场分别算，不能两个市场都用北京日期。

    北京零点正好是美东中午 12:00（夏令时），落在美股常规时段的正中间。
    都用北京日期的话，每天盘中会发生两件事：每日配额闸原地清零（一只
    美股上午 4 条、下午再 4 条，实际上限翻倍），关键位缓存也被清空重算。
    """

    def test_us_today_is_still_yesterday_just_after_beijing_midnight(self):
        # UTC 2026-09-04 16:05 = 北京 09-05 00:05 = 美东 09-04 12:05（夏令时）
        now = _utc("2026-09-04 16:05")
        assert now.astimezone(
            watchdog.session_mod.CN_TZ).date().isoformat() == "2026-09-05"
        assert watchdog._today_for("US", now) == "2026-09-04", \
            "北京跨了零点，美股还在同一个交易日的正中间"

    def test_cn_today_is_the_beijing_date(self):
        assert watchdog._today_for("CN", _utc("2026-09-04 02:00")) == "2026-09-04"


class TestObservability:
    """这个功能最典型的故障是「进程还在、但什么都没在看」。

    光看进程在不在完全分辨不出来，所以每轮留一行日志、连续取不到报价
    要主动告警、启动时报到一次。
    """

    def test_writes_one_summary_line_per_round(self, capsys):
        state = watchdog.empty_state()
        with patch("watchdog.pg.query", return_value=POSITIONS[:1]), \
             patch("watchdog.quotes.get", return_value={}):
            watchdog.run_once(state, _utc("2026-09-04 02:00"))
        err = capsys.readouterr().err
        assert "持仓 1" in err and "报价 0" in err

    def test_position_read_failure_is_logged(self, capsys):
        state = watchdog.empty_state()
        with patch("watchdog.pg.query", side_effect=RuntimeError("库连不上")):
            assert watchdog.run_once(state, _utc("2026-09-04 02:00")) == []
        assert "读持仓失败" in capsys.readouterr().err

    def test_quote_failure_alerts_after_ten_rounds(self):
        """连续 10 轮一只报价都拿不到，推一条 Telegram 告警，然后清零。"""
        state = watchdog.empty_state()
        now = _utc("2026-09-04 02:00")
        with patch("watchdog.notify.send", return_value=True) as spy:
            for _ in range(watchdog.QUOTE_FAIL_ALERT_ROUNDS - 1):
                watchdog._track_quote_failures(
                    state, {"CN": ["601138.SH"]}, {}, now)
            spy.assert_not_called()
            watchdog._track_quote_failures(
                state, {"CN": ["601138.SH"]}, {}, now)
            assert spy.call_count == 1
        assert state["quote_fail"]["CN"] == 0, "告警完要清零，不能每轮刷一条"

    def test_counter_resets_once_quotes_come_back(self):
        state = watchdog.empty_state()
        now = _utc("2026-09-04 02:00")
        watchdog._track_quote_failures(state, {"CN": ["601138.SH"]}, {}, now)
        watchdog._track_quote_failures(
            state, {"CN": ["601138.SH"]}, {"601138.SH": {"last": 63.5}}, now)
        assert "CN" not in state["quote_fail"]

    def test_startup_notice_counts_each_market(self):
        with patch("watchdog.pg.query", return_value=POSITIONS):
            text = watchdog.startup_notice()
        for token in ("2 只", "A股 1", "美股 1"):
            assert token in text, "启动通知里缺了 %s：%s" % (token, text)

    def test_startup_notice_survives_a_dead_database(self):
        """读不到持仓也要发得出去——这条消息的价值就在于「它活着」。"""
        with patch("watchdog.pg.query", side_effect=RuntimeError("库连不上")):
            text = watchdog.startup_notice()
        assert text.startswith("【盯盘】"), "要带来源标识，实际：%s" % text
        assert "已启动" in text
        assert "读不到持仓" in text, "还要说清为什么报不出票数：%s" % text
