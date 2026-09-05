# -*- coding: utf-8 -*-
"""出网请求的公共设施：代理地址怎么规范、opener 怎么建。

抽出来只为一件事：**这段逻辑不能有第二份。**

要出国的有两路——`core/notify.py`（Telegram 推送）和 `core/uw.py`
（Unusual Whales 实时行情），两边都得处理同样的两种配错（不写协议头、
配成 SOCKS），两种都是静默失效、查起来要命的那类。原来这段代码只长在
notify 里，uw 要用就得 `from core.notify import ...`——一个「行情客户端」
去 import「推送模块」拿代理解析，半年后没人看得懂这条依赖为什么存在。
所以搬到这里，两边平等地依赖它。

`core/akshare.py` 不用这里的东西，它的需求正好相反：AKTools 是局域网
地址，必须【绕开】代理，见那边的注释。
"""
import urllib.request


def normalize_proxy(proxy: str):
    """把配置里的代理地址规范成 urllib 认得的样子。

    返回 `""` 表示直连（合法配置），返回 `None` 表示**这个代理根本用不了**，
    调用方必须明确报错，不能假装直连。

    两种配错要在这里挡住，都是 2026-09-04 首次冒烟真实踩到的：

    1. **没写协议头**（`127.0.0.1:7890`）。urllib 会把冒号前面的
       `127.0.0.1` 当成协议名去解析，代理配置直接失效、退化成直连，
       然后连 Telegram 超时。补一个 `http://` 就好。

    2. **SOCKS 代理**（`socks5://…`）。**Python 标准库不支持 SOCKS**，
       `urllib.request.ProxyHandler` 只认 HTTP/HTTPS 代理。配了也是白配，
       表现跟第一种一样是静默超时。这里直接挡下并说清原因——多数代理软件
       同时开着 HTTP 端口，换一个端口就行，不值得为此引入新依赖。
    """
    text = (proxy or "").strip()
    if not text:
        return ""                       # 空 = 直连，合法
    scheme = text.split("://", 1)[0].lower() if "://" in text else ""
    if scheme.startswith("socks"):
        return None                     # 标准库用不了，交给调用方报错
    return text if scheme else "http://" + text


def build_opener(proxy: str, *, env_when_empty: bool = False):
    """建一个按 `proxy` 出网的 opener。`proxy` 要先过 `normalize_proxy`。

    `env_when_empty` 决定「没配代理」是什么意思。两个调用方要的不一样，
    所以做成参数而不是猜：

      * **Telegram 推送传 False**（默认）——没配就是**强制直连**，连
        `HTTP_PROXY` 环境变量也绕开。推送走哪条路必须由 `.env` 一处
        说了算，不能让某台机器上碰巧存在的环境变量改变行为。
      * **UW 行情传 True**——没配就**跟随环境变量**，也就是 urllib 的
        默认行为。这一路本来就是这么跑起来的（开发机上 `HTTP_PROXY`
        一直设着），一声不响改成强制直连，等于在没人察觉的情况下换掉
        现有部署的出网方式。

    两边都有的时候显式配置优先——不然「明明配了却没生效」这种事
    根本查不出来。
    """
    if proxy:
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    elif env_when_empty:
        handler = urllib.request.ProxyHandler()      # 不传参 = 读环境变量
    else:
        handler = urllib.request.ProxyHandler({})    # 空字典 = 谁都不走
    return urllib.request.build_opener(handler)
