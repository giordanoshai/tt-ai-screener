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
    """发送路径的测试。

    每条都必须把 `config.TG_PROXY` 一起 patch 掉——否则测试会读到运行这台
    机器上的真实代理配置，别人换个代理就能把测试弄红。2026-09-04 真的发生
    过：本机配置从裸地址改成 SOCKS 地址之后，`test_send_calls_post_once`
    立刻红了。红得有道理（SOCKS 确实发不出去），但那不是这条用例想测的东西。
    """

    def test_send_calls_post_once(self):
        with patch.object(config, "TG_BOT_TOKEN", "T"), \
             patch.object(config, "TG_CHAT_ID", "C"), \
             patch.object(config, "TG_PROXY", ""), \
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
             patch.object(config, "TG_PROXY", ""), \
             patch.object(notify, "_post", side_effect=RuntimeError("boom")):
            assert notify.send("hi") is False


class TestProxyNormalization:
    """代理地址的规范化。

    下面两种配错都是 2026-09-04 首次真实冒烟踩到的，共同点是**静默失败**——
    推送发不出去，但只表现为超时后返回 False，谁都看不出真正的原因。
    """

    def test_empty_means_direct_connection(self):
        """空值是合法配置，表示直连。"""
        assert notify.normalize_proxy("") == ""
        assert notify.normalize_proxy(None) == ""
        assert notify.normalize_proxy("   ") == ""

    def test_bare_host_port_gets_http_prefix(self):
        """没写协议头时，urllib 会把冒号前的 `127.0.0.1` 当成协议名去解析，
        代理配置直接失效、退化成直连。补一个 http:// 才认得。"""
        assert notify.normalize_proxy("127.0.0.1:7890") == "http://127.0.0.1:7890"

    def test_existing_scheme_is_left_alone(self):
        assert notify.normalize_proxy(
            "http://127.0.0.1:1081") == "http://127.0.0.1:1081"

    def test_socks_is_rejected_not_silently_ignored(self):
        """**Python 标准库不支持 SOCKS**，`ProxyHandler` 只认 HTTP/HTTPS。
        必须返回 None 让调用方明确报错，不能假装能用。"""
        assert notify.normalize_proxy("socks5://127.0.0.1:1080") is None
        assert notify.normalize_proxy("socks4://127.0.0.1:1080") is None
        assert notify.normalize_proxy("SOCKS5://127.0.0.1:1080") is None

    def test_send_refuses_socks_and_explains_why(self, capsys):
        """配了 SOCKS 时绝不能静默走直连——那会超时十几秒然后返回 False，
        看日志完全不知道该改什么。必须一行说清。"""
        with patch.object(config, "TG_BOT_TOKEN", "T"), \
             patch.object(config, "TG_CHAT_ID", "C"), \
             patch.object(config, "TG_PROXY", "socks5://127.0.0.1:1080"), \
             patch.object(notify, "_post") as spy:
            assert notify.send("hi") is False
        spy.assert_not_called(), "配了 SOCKS 就不该再去发请求"
        err = capsys.readouterr().err
        assert "SOCKS" in err and "HTTP" in err

    def test_send_hands_post_the_normalized_address(self):
        """裸地址补上 http:// 之后才交给发请求那一层。"""
        with patch.object(config, "TG_BOT_TOKEN", "T"), \
             patch.object(config, "TG_CHAT_ID", "C"), \
             patch.object(config, "TG_PROXY", "127.0.0.1:1081"), \
             patch.object(notify, "_post", return_value={"ok": True}) as spy:
            assert notify.send("hi") is True
        assert spy.call_args[0][2] == "http://127.0.0.1:1081"

    def test_send_failure_now_says_which_route_it_tried(self, capsys):
        """失败日志要说清是直连还是经代理——这是排查的第一个分岔口。"""
        with patch.object(config, "TG_BOT_TOKEN", "T"), \
             patch.object(config, "TG_CHAT_ID", "C"), \
             patch.object(config, "TG_PROXY", "127.0.0.1:1081"), \
             patch.object(notify, "_post", side_effect=RuntimeError("boom")):
            assert notify.send("hi") is False
        assert "http://127.0.0.1:1081" in capsys.readouterr().err


class TestTitle:
    """推送标题的格式。

    这个 Telegram 机器人是跟别的系统共用的（交易执行那套发出来的是
    「【SOS】✅ 已买入 · SLV …」），所以盯盘发的每一条都必须带自己的来源
    标识，否则手机上几个系统的通知混在一起没法看。
    """

    def test_every_alert_carries_the_source_prefix(self):
        for kind in ("接近预警", "有效突破", "假突破", "跌破"):
            text = notify.format_alert(_alert(kind=kind), POSITION)
            assert text.startswith(notify.PREFIX), (
                "%s 这一类的推送没带来源标识：%s" % (kind, text.split("\n")[0]))

    def test_each_kind_gets_a_distinct_icon(self):
        """四类信号四个图标——扫一眼就知道是好事还是坏事，不用读完整句。"""
        icons = {}
        for kind in ("接近预警", "有效突破", "假突破", "跌破"):
            head = notify.format_alert(
                _alert(kind=kind), POSITION).split("\n")[0]
            icons[kind] = head[len(notify.PREFIX):].split(" ")[0]
        assert len(set(icons.values())) == 4, (
            "四类信号的图标应该各不相同，实际是 %r" % icons)

    def test_title_helper_shape(self):
        assert notify.title("🟢", "已启动") == "【盯盘】🟢 已启动"

    def test_session_tag_still_shows_after_the_title(self):
        """盘前盘后的标记不能被标题挤掉——那是信号成色的提示。"""
        head = notify.format_alert(
            _alert(session="po"), POSITION).split("\n")[0]
        assert head.startswith(notify.PREFIX)
        assert "盘后" in head

    def test_ticker_is_in_the_first_line(self):
        """手机通知栏经常只显示第一行，票代码必须在里面。"""
        head = notify.format_alert(_alert(), POSITION).split("\n")[0]
        assert "601138.SH" in head


class TestDataDateNotice:
    """数据不是「今天」的时候，文案必须说清楚。

    盘前 UW 常常还没有当天的分钟线，取到的是前一天收盘那批。那条信息本身
    有价值（持仓的票撞了坎没过去），不该压掉；但不标明时点就是误导——
    用户会以为正在发生。
    """

    def _with_as_of(self, as_of):
        alert = _alert()
        alert["quote"] = dict(alert["quote"], as_of=as_of)
        return alert

    def test_stale_data_gets_a_timestamp_line(self):
        text = notify.format_alert(
            self._with_as_of("2026-09-03"), POSITION, today="2026-09-04")
        assert "09/03" in text, text
        assert "不是刚刚发生" in text, text

    def test_same_day_data_gets_no_extra_line(self):
        text = notify.format_alert(
            self._with_as_of("2026-09-04"), POSITION, today="2026-09-04")
        assert "不是刚刚发生" not in text

    def test_without_today_it_stays_quiet(self):
        """没传「今天」就不判断——不能凭空猜今天是哪天，那要时区。"""
        text = notify.format_alert(self._with_as_of("2026-09-03"), POSITION)
        assert "不是刚刚发生" not in text

    def test_missing_as_of_stays_quiet(self):
        """报价里没有 as_of（日期解析不出来）时不标注。"""
        text = notify.format_alert(
            self._with_as_of(None), POSITION, today="2026-09-04")
        assert "不是刚刚发生" not in text

    def test_the_notice_sits_before_the_position_line(self):
        """读的顺序是：什么事 → 细节 → 什么时候的 → 我手上有多少。"""
        text = notify.format_alert(
            self._with_as_of("2026-09-03"), POSITION, today="2026-09-04")
        lines = text.split("\n")
        when = next(i for i, ln in enumerate(lines) if "不是刚刚发生" in ln)
        held = next(i for i, ln in enumerate(lines) if "持仓" in ln)
        assert when < held, "时点说明应该排在持仓那行之前：\n%s" % text
