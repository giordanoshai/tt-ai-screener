# -*- coding: utf-8 -*-
"""core/notify.py 的测试。

**绝不真发消息。** 真正发请求的那一层是 `notify._post`，测试一律把它
换掉——这跟 `core/uw.py` 把 `_fetch` 单独抽出来是同一个套路：
不这么做的话，配了真 token 的机器跑一次测试就真发一条消息给用户。

文案测试看重两件事：
* 该有的数字都在（触及价、关键位、现价、回落幅度、持仓盈亏）。
* 盘前盘后要标出来——那时候流动性差，用户得知道这条信号成色不同。
"""
from unittest.mock import patch

import config
from core import notify


def _alert(kind="假突破", session="cn", **over):
    base = {
        "ticker": "601138.SH", "kind": kind, "price": 64.84,
        "label": "近期高收08-31", "side": "resistance", "session": session,
        "quote": {"last": 63.55, "high": 65.30, "low": 63.30,
                  "open": 63.84, "prev_close": 63.20, "volume": 6e7,
                  "session": session, "source": "test"},
    }
    base.update(over)
    return base


POSITION = {"avg_cost": 61.52, "shares": 100.0, "currency": "CNY"}


class TestFormat:
    def test_fake_breakout_mentions_all_key_numbers(self):
        text = notify.format_alert(_alert(), POSITION)
        for token in ("601138.SH", "假突破", "65.30", "64.84", "63.55", "61.52"):
            assert token in text, "文案里缺了 %s：\n%s" % (token, text)

    def test_shows_pullback_from_high(self):
        """从高点回落了多少——这是「没站住」的量化表达。"""
        text = notify.format_alert(_alert(), POSITION)
        assert "2.7%" in text       # (65.30-63.55)/65.30

    def test_marks_after_hours(self):
        assert "盘后" in notify.format_alert(_alert(session="po"), POSITION)

    def test_marks_premarket(self):
        assert "盘前" in notify.format_alert(_alert(session="pr"), POSITION)

    def test_warning_says_how_far_to_go(self):
        alert = _alert(kind="接近预警")
        alert["quote"]["last"] = 64.20
        text = notify.format_alert(alert, POSITION)
        assert "还差" in text
        assert "64.84" in text

    def test_break_support_mentions_the_low(self):
        alert = _alert(kind="跌破", price=62.16, label="半年低04-30",
                       side="support")
        alert["quote"]["last"] = 61.90
        alert["quote"]["low"] = 61.80
        text = notify.format_alert(alert, POSITION)
        assert "62.16" in text and "61.80" in text

    def test_works_without_position(self):
        assert "601138.SH" in notify.format_alert(_alert(), None)


class TestSend:
    def test_send_calls_post_once(self):
        with patch.object(config, "TG_BOT_TOKEN", "T"), \
             patch.object(config, "TG_CHAT_ID", "C"), \
             patch.object(notify, "_post", return_value={"ok": True}) as spy:
            assert notify.send("hi") is True
        assert spy.call_count == 1

    def test_send_returns_false_when_not_configured(self):
        with patch.object(config, "TG_BOT_TOKEN", ""), \
             patch.object(config, "TG_CHAT_ID", ""):
            assert notify.send("hi") is False

    def test_network_failure_does_not_raise(self):
        """推送失败不能把盯盘进程搞挂——下一轮还要继续跑。"""
        with patch.object(config, "TG_BOT_TOKEN", "T"), \
             patch.object(config, "TG_CHAT_ID", "C"), \
             patch.object(notify, "_post", side_effect=RuntimeError("boom")):
            assert notify.send("hi") is False
