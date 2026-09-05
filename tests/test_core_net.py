# -*- coding: utf-8 -*-
"""出网公共设施：代理地址怎么规范、opener 怎么建。全是纯函数，不联网。"""
import urllib.request

from core import net, notify


# ---------------- 代理地址规范化 ----------------

class TestNormalizeProxy:
    """这几条原来长在 tests/test_core_notify.py 里，跟着实现搬过来。
    notify 那边保留了同名的重新导出，所以两处都得还能用。"""

    def test_empty_means_direct(self):
        assert net.normalize_proxy("") == ""
        assert net.normalize_proxy(None) == ""
        assert net.normalize_proxy("   ") == ""

    def test_bare_address_gets_http_prefix(self):
        """不写协议头的话 urllib 会把 127.0.0.1 当协议名，配置静默失效。"""
        assert net.normalize_proxy("127.0.0.1:7890") == "http://127.0.0.1:7890"

    def test_http_address_passes_through(self):
        assert net.normalize_proxy(
            "http://127.0.0.1:1081") == "http://127.0.0.1:1081"

    def test_socks_is_rejected_not_silently_ignored(self):
        """标准库不支持 SOCKS。返回 None 逼调用方明确报错。"""
        assert net.normalize_proxy("socks5://127.0.0.1:1080") is None
        assert net.normalize_proxy("SOCKS5://127.0.0.1:1080") is None

    def test_notify_still_exposes_it(self):
        """搬家不能把 notify.normalize_proxy 搬没了——别处还在用。"""
        assert notify.normalize_proxy is net.normalize_proxy


# ---------------- opener 怎么建 ----------------

def _proxies_of(opener):
    """从 opener 里把它实际认的那份代理表挖出来。空字典 = 谁都不走。

    找不到 ProxyHandler 也算空字典，不是「出错了」——这是 urllib 的一个
    坑：`ProxyHandler({})` 因为一个 `*_open` 方法都没生成，
    `OpenerDirector.add_handler` 判定它「什么都没接管」，压根不会把它放进
    `handlers` 列表。但代理确实被关掉了：`build_opener` 只要在参数里见过
    ProxyHandler 的实例，就不会再补那个读环境变量的默认 handler。
    所以「列表里没有」和「列表里有一个空表」是同一个意思。
    """
    for handler in opener.handlers:
        if isinstance(handler, urllib.request.ProxyHandler):
            return handler.proxies
    return {}


class TestBuildOpener:

    def test_explicit_proxy_covers_http_and_https(self):
        proxies = _proxies_of(net.build_opener("http://127.0.0.1:1081"))
        assert proxies == {"http": "http://127.0.0.1:1081",
                           "https": "http://127.0.0.1:1081"}

    def test_empty_means_hard_direct_by_default(self):
        """默认语义（Telegram 推送要的）：没配就是强制直连，
        连环境变量里的代理也绕开——走哪条路只由 .env 一处说了算。"""
        assert _proxies_of(net.build_opener("")) == {}

    def test_empty_can_follow_environment_instead(self, monkeypatch):
        """UW 行情要的语义：没配就跟随 HTTP_PROXY，也就是 urllib 的默认行为。"""
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1080")
        proxies = _proxies_of(
            net.build_opener("", env_when_empty=True))
        assert proxies.get("http") == "http://127.0.0.1:1080"

    def test_explicit_proxy_wins_over_environment(self, monkeypatch):
        """两边都有的时候，.env 里显式配的说了算——不然「配了没生效」
        这种事查起来要命。"""
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9999")
        proxies = _proxies_of(
            net.build_opener("http://127.0.0.1:1081", env_when_empty=True))
        assert proxies == {"http": "http://127.0.0.1:1081",
                           "https": "http://127.0.0.1:1081"}
