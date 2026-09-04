# -*- coding: utf-8 -*-
"""pytest 共享配置。

## 这里做的唯一一件事：默认拦住所有实时行情请求（A股走新浪、美股走 UW）

2026-09-01 把 A股小时线改成「先走实时接口，拿不到才查库」之后，
`tests/test_hourly.py::TestMarketInference` 三个用例当场红了——它们
mock 掉 `pg.query` 然后断言「查库时传的市场是 CN」，而实时那一路直接
拿到了真数据，`pg.query` **一次都没被调用**，`call_args` 是 None。

问题不只是这三个用例。**只要跑测试的机器上配了 `AKSHARE_BASE_URL`，
任何一个拿 A股代码走 `hourly_setup` 的用例都会静悄悄地发真实网络请求**，
结果随线上行情变化——同一份代码今天绿明天红，而且看不出为什么。

所以默认全局拦掉：`core.akshare._get` 换成抛异常的桩。
`core/akshare.py` 本来就承诺「取不到返回空」，所以对被测代码来说
效果等同于「没配这个接口」，跟改造之前的行为一致，不污染任何断言。

需要验证实时那条路的用例（`TestCnHourlyGoesLiveFirst`）自己 patch
更高一层的 `core.akshare.hourly`，天然绕过这个桩，互不干扰。

## 2026-09-03 起美股也一样

美股小时线那天也改成了「先实时后查库」（上游把 `us_hourly` 改成按需，
库里那份不再每天更新），实时那头走 `core/uw.py`。于是同一个坑原样长在了
美股这边：`tests/test_hourly.py` 里几十个用例拿的是 "AAPL"、mock 掉 `pg.query`
之后断言假数据算出来的结果，而配了真密钥的机器会先去打一次真 UW 接口，
拿回真行情把断言全打红。

拦的是 `core.uw._fetch`（真正发请求的那一层），不是 `uw.get`——`get()` 承诺
「任何失败都返回 None」，桩抛异常正好走它的降级分支，
`test_get_bad_path_returns_none_not_raise` 那条回归测试仍然在测真行为。
需要真联网的冒烟用例加一个 `live_uw` fixture 就能解除拦截。

## 2026-09-04 起也拦 Telegram 推送

盯盘守护进程（watchdog.py）新增之后，测试机的 `.env` 里配的是**真的**
`TG_BOT_TOKEN` / `TG_CHAT_ID`——复用用户已有的机器人。`tests/test_watchdog.py`
会真的走到 `core.notify.send`，一条测试没打桩，就是每跑一次测试给用户
手机发一条真消息。跟 AKShare / UW 同一个道理：拦的是 `core.notify._post`
（真正发 HTTP 请求的那一层），不是 `send()`——`send()` 承诺「任何失败都
返回 False，不抛异常」，桩抛异常正好走它自己的降级分支，行为跟没配置
Telegram 时一致，不污染任何断言。

**数据库不拦。** 这个仓库的测试本来就是冲着真 Postgres 跑的（只读账号），
那是有意的设计，不是疏漏。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import akshare, notify, uw  # noqa: E402  （必须等 sys.path 补好之后再导）

#: 拦截之前的真身，`live_uw` fixture 用它放行。
_REAL_UW_FETCH = uw._fetch


@pytest.fixture(autouse=True)
def _no_live_market_data(monkeypatch):
    """默认不让测试发真实的行情请求（新浪 + UW）、也不让它推真实的
    Telegram 消息。理由见模块注释。"""
    def blocked(name):
        def stub(*args, **kwargs):
            raise RuntimeError(
                f"测试里不许打真实的 {name} 接口（tests/conftest.py 拦下的）")
        return stub

    monkeypatch.setattr(akshare, "_get", blocked("AKShare"))
    monkeypatch.setattr(uw, "_fetch", blocked("Unusual Whales"))
    monkeypatch.setattr(notify, "_post", blocked("Telegram"))


@pytest.fixture
def live_uw(monkeypatch):
    """给真的要联网冒烟的用例用：解除上面那道 UW 拦截。

    非 autouse 的 fixture 在 autouse 之后建立，所以这里的 setattr 一定
    盖得住。用之前想清楚：这条用例的结果会随线上行情变化。
    """
    monkeypatch.setattr(uw, "_fetch", _REAL_UW_FETCH)
