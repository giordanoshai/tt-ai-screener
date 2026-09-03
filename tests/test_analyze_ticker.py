# -*- coding: utf-8 -*-
"""个股分析：一只票的完整体检。"""
import json

from tools import analyze_ticker as at


def run(ticker, **kwargs):
    return json.loads(at.analyze_ticker(ticker, **kwargs))


def test_us_ticker_full_checkup():
    out = run("NVDA")
    assert out["ticker"] == "NVDA"
    assert out["market"] == "US"
    assert {"trend", "hourly", "fundamental", "news", "flow",
            "gamma", "earnings", "insider", "congress", "setup", "plan",
            "data_gaps"} <= set(out)


def test_short_horizon_omits_fundamentals():
    """短波段不看基本面，字段就该是 None，而不是查了再不用。"""
    out = run("NVDA", horizon="short")
    assert out["fundamental"] is None


def test_mid_horizon_includes_fundamentals():
    out = run("NVDA", horizon="mid")
    assert out["fundamental"] is not None


def test_cn_ticker_works_and_says_what_is_missing():
    out = run("600519.SH")
    assert out["market"] == "CN"
    assert out["cn"] is not None
    gaps = " ".join(out["data_gaps"])
    assert "基本面" in gaps or "板块" in gaps


def test_unknown_ticker_has_no_hourly_either():
    """假票连日线都没有，走的是最前面「没有行情数据」的提前返回，
    那条分支把 hourly 硬写成 None——测的是这条路，不是小时线本身。"""
    out = run("NOSUCHTICKERXYZ")
    assert out["hourly"] is None


def test_hourly_is_none_when_neither_live_nor_the_database_has_it(monkeypatch):
    """两条路都空的票：`hourly` 应该是 None 并且在 data_gaps 里说明。

    2026-09-03 之前这条测的是「小时线只采活跃池，池子外的票没有」；
    现在两个市场都先走实时，池子内外一视同仁，所以改成直接把两条路都掐掉——
    降级路径本身还在，测的就是它。
    """
    import pytest
    from db import pg

    row = pg.query_one("""
        select d.ticker
        from stock_ohlcv_daily d
        where d.market = 'US'
          and d.date = (select max(date) from stock_ohlcv_daily
                        where market = 'US')
          and not exists (select 1 from stock_ohlcv_min m
                          where m.ticker = d.ticker
                            and m.market = 'US'
                            and m.timeframe = '1h')
        limit 1
    """)
    if row is None:
        pytest.skip("所有有日线的美股都有小时线，测不出这条")
    monkeypatch.setattr("core.uw.hourly", lambda *a, **k: [])
    out = run(row["ticker"])
    assert out["hourly"] is None
    assert any("小时线" in g for g in out["data_gaps"])


def test_hourly_from_the_database_is_flagged_as_possibly_stale(monkeypatch):
    """★ 退回库里必须在 data_gaps 里说一声 ★

    上游 2026-09-03 把 `us_hourly` 改成按需跑之后，库里那份美股小时线不再
    每天更新。退回库里拿到的可能是好几天前的行情，而它算出来的支撑阻力、
    量比看着跟实时的一模一样——不说出来，用户会拿三天前的点位下单。
    跟上面期权流那条「取自数据库」的提示是同一个道理。
    """
    monkeypatch.setattr("core.uw.hourly", lambda *a, **k: [])   # 逼它退回库里
    out = run("AAPL")
    if out["hourly"] is None:
        import pytest
        pytest.skip("库里也没有 AAPL 的小时线，这条测不到")
    assert out["hourly"]["source"] == "db"
    assert any("小时线取自数据库" in g for g in out["data_gaps"]), out["data_gaps"]


def test_hourly_runs_off_the_main_thread(monkeypatch):
    """★ 小时线必须跟下面那几路实时接口并着跑 ★

    实测（2026-09-03，AAPL）：整只票体检 13.5 秒，而 UW 的 1h 接口单次就要
    12.9~14.0 秒。串着跑等于直接翻倍到 26 秒，用户一眼能感觉到。
    这条盯的就是「有没有人把它改回串行」。
    """
    import threading

    seen = {}
    real = at.hourly_mod.hourly_setup

    def spy(*args, **kwargs):
        seen["thread"] = threading.current_thread()
        return real(*args, **kwargs)

    monkeypatch.setattr(at.hourly_mod, "hourly_setup", spy)
    run("AAPL")
    assert seen.get("thread") is not None, "小时线压根没被调用"
    assert seen["thread"] is not threading.main_thread(), \
        "小时线又串回主线程了，整只票的体检会慢一倍"


def test_mid_horizon_never_returns_a_short_tier_setup():
    """要中线就给中线。原来的写法先跑短线形态，命中就直接返回短线结论，
    mid 和 both 除了回显字段之外一模一样。"""
    out = run("NVDA", horizon="mid")
    assert out["setup"] is None or out["setup"] in ("trend_continuation",
                                                     "bottom_reversal")


def test_cn_data_gaps_do_not_repeat_the_same_missing_thing():
    """同一个缺失不能在清单里出现两遍——用户会以为是两个问题。"""
    out = run("600519.SH")
    board_gaps = [g for g in out["data_gaps"] if "板块" in g or "行业" in g]
    assert len(board_gaps) <= 1, f"板块缺失报了 {len(board_gaps)} 遍：{board_gaps}"


def test_low_score_plan_carries_a_confidence_caveat():
    """价位精确不等于这笔值得做。低分 + 缺口多的时候，计划旁边必须有话说清楚。"""
    out = run("600519.SH")
    if out.get("plan") and (out["score"] < 50 or len(out["data_gaps"]) >= 3):
        assert any("机械算出来" in r for r in out["risks"])


def test_unknown_ticker_returns_error_not_exception():
    out = run("NOSUCHTICKERXYZ")
    assert "error" in out or out["trend"] is None


def test_gamma_is_none_for_most_tickers():
    """uw_gex_daily 只有 41 只票，绝大多数是 None，这是已知的。"""
    out = run("MP")
    assert out["gamma"] is None or "call_wall" in out["gamma"]


def test_empty_ticker_errors():
    assert "error" in json.loads(at.analyze_ticker(""))


def test_hourly_setup_returns_dict_now():
    from tools import hourly
    out = hourly.hourly_setup("NVDA", bars=30)
    assert isinstance(out, dict)


def test_get_hourly_setup_still_returns_json_string():
    """网页面板和现有测试还在用这个包装，不能删。"""
    from tools import hourly
    assert isinstance(hourly.get_hourly_setup("NVDA", bars=30), str)


# ================ 评审 C2：任何一路数据源挂了，其余结论照常给 ================

def _boom(*args, **kwargs):
    raise RuntimeError("探针：这一路挂了")


_US_SOURCES = [
    ("fundamental", "get", "基本面"),
    ("catalyst", "news_for", "新闻"),
    ("catalyst", "earnings_for", "财报日"),
    ("catalyst", "options_flow_live", "期权异动"),
    ("catalyst", "gamma_walls", "gamma 墙"),
    ("catalyst", "insider_for", "内部人交易"),
    ("catalyst", "congress_for", "国会交易"),
    ("regime", "freshness", "行情新鲜度"),
    ("hourly_mod", "hourly_setup", "小时线"),
]


def test_every_data_source_can_fail_without_killing_the_checkup(monkeypatch):
    """每一路取数各写一次「这一路挂了」：其余结论照常返回、`data_gaps` 里
    写明挂的是哪一路。

    评审 C2 实测：改之前，news 或 gamma 墙任意一路抛异常，返回体就只剩
    `{"error": ...}`——而 gamma 墙只覆盖约 40 只大盘股、代码注释自己写着
    「加分项，缺了不致命」，它却有本事干掉整只票的体检。
    """
    baseline = run("AAPL", horizon="both")
    assert "error" not in baseline
    assert baseline["score"] is not None

    for module_name, func_name, label in _US_SOURCES:
        module = getattr(at, module_name)
        with monkeypatch.context() as mp:
            mp.setattr(module, func_name, _boom)
            out = run("AAPL", horizon="both")
        assert "error" not in out, (
            f"{module_name}.{func_name} 挂了就把整只票的体检打死了："
            f"{out.get('error')}")
        assert out["score"] is not None and out["trend"], (
            f"{module_name}.{func_name} 挂了之后连趋势和评分都没有了")
        assert any(label in g and "失败" in g for g in out["data_gaps"]), (
            f"{module_name}.{func_name} 挂了，data_gaps 里没写明是"
            f"「{label}」这一路：{out['data_gaps']}")


def test_cn_data_sources_can_fail_without_killing_the_checkup(monkeypatch):
    """A股那几路同理。"""
    _CN = [("cn_signals", "fund_flow", "A股主力资金"),
           ("cn_signals", "lhb", "龙虎榜"),
           ("cn_signals", "board_of", "A股板块"),
           ("cn_signals", "limit_sentiment", "A股涨停情绪"),
           ("cn_signals", "signal_dates", "A股信号表日期")]
    baseline = run("000001.SZ", horizon="both")
    assert "error" not in baseline

    for module_name, func_name, label in _CN:
        module = getattr(at, module_name)
        with monkeypatch.context() as mp:
            mp.setattr(module, func_name, _boom)
            out = run("000001.SZ", horizon="both")
        assert "error" not in out, (
            f"{module_name}.{func_name} 挂了就把整只票的体检打死了："
            f"{out.get('error')}")
        assert out["score"] is not None
        assert any(label in g and "失败" in g for g in out["data_gaps"]), (
            f"{module_name}.{func_name} 挂了，data_gaps 里没写明："
            f"{out['data_gaps']}")


# ================ 评审 C4：分数旁边要写清楚「依据有多少」 ================

def test_evidence_pct_is_reported_and_matches_the_missing_blocks():
    """A股走中线档时，基本面 35 分 + 相对强度 20 分整块缺——满分 100 里
    只用上 45 分。返回体必须把这件事写出来，不能只给一个 82 分。"""
    out = run("000001.SZ", horizon="mid")
    assert "error" not in out
    assert out["evidence_pct"] == 45, (
        f"A股中线档应该只用上 45% 的评分表（趋势 25 + 消息面 20），"
        f"实际报的是 {out['evidence_pct']}")
    assert out["score"] > 0


def test_full_evidence_scores_report_one_hundred_percent():
    """反过来：依据齐全的美股中线档必须是 100%。缺了这一条，
    上面那条可以被「evidence_pct 永远写死 45」蒙混过去。"""
    out = run("ORCL", horizon="mid")
    assert "error" not in out
    assert out["evidence_pct"] == 100, (
        f"ORCL 基本面齐全，evidence_pct 应该是 100，实际 {out['evidence_pct']}")


def test_low_evidence_score_carries_an_explicit_warning():
    """依据比例低于门槛时必须有一条明写的提醒，不能光靠用户自己看数字。"""
    from core import scoring

    out = run("000001.SZ", horizon="mid")
    assert out["evidence_pct"] < scoring.LOW_EVIDENCE_PCT
    assert any("评分表" in r for r in out["risks"]), (
        f"依据只有 {out['evidence_pct']}%，risks 里却没有任何提醒："
        f"{out['risks']}")


def test_confidence_warning_fires_even_when_there_is_no_plan(monkeypatch):
    """低置信度提示不能挂在 `plan` 的存在与否上。

    原来写的是 `if plan and (...)`——没识别出形态时 `plan` 是 None，整条提示
    被跳过，而「没形态 + 缺一堆依据 + 分还不低」恰恰是最该被警告的一类，
    它成了唯一拿不到警告的一类（评审 C4）。
    """
    from core import scoring

    # 造一个「认不出形态、依据缺一堆、分数不低」的场景
    monkeypatch.setattr(at.scoring, "detect_short_setup", lambda snap: None)
    monkeypatch.setattr(at.scoring, "detect_mid_setup",
                        lambda snap, fund: None)
    monkeypatch.setattr(
        at.scoring, "score_mid",
        lambda snap, fund=None, news=None: {
            "score": 82, "evidence_pct": 45, "reasons": [], "risks": [],
            "gaps": ["探针缺口 1", "探针缺口 2", "探针缺口 3"]})

    out = run("000001.SZ", horizon="mid")
    assert out["plan"] is None, "这条测试的前提就是没有 plan"
    assert out["score"] == 82
    assert any("依据" in r for r in out["risks"]), (
        f"没有 plan 时低置信度提示被整条跳过了：{out['risks']}")
    assert scoring is not None


# ================ 评审 I4：这个工具原来是五个里唯一没有 warnings 的 ================

def test_freshness_warnings_are_reported(monkeypatch):
    """2026-08-30 `us_news` 采集失败、5 个任务只跑了一半，其余四个工具都报
    了，个股体检一个字不提——用户会以为这份体检的消息面是完整的。"""
    from core import regime as regime_mod

    real = regime_mod.freshness

    def fresh_but_broken(market):
        out = dict(real(market))
        out["stale"] = False
        out["failed_jobs"] = ["us_news(failed)"]
        out["partial_jobs"] = []
        return out

    monkeypatch.setattr(at.regime, "freshness", fresh_but_broken)
    out = run("AAPL", horizon="short")
    assert any("采集任务失败" in w and "us_news" in w
               for w in out["warnings"]), (
        f"采集失败必须无条件报进 warnings：{out['warnings']}")


def test_no_fundamental_gap_is_not_listed_twice():
    """「没有基本面数据」这句话在 core 和 tools 两处都会说。措辞差一个字的话
    `sorted(set(...))` 去不掉重，同一件事在 data_gaps 里出现两遍。"""
    out = run("000001.SZ", horizon="mid")
    hits = [g for g in out["data_gaps"] if "没有基本面数据" in g]
    assert len(hits) == 1, f"「没有基本面数据」出现了 {len(hits)} 次：{hits}"


def test_trend_numbers_are_rounded():
    """`"rsi": 55.8322681571604` 这种十几位小数别糊到用户脸上。"""
    out = run("AAPL", horizon="short")
    checked = 0
    for key in ("close", "rsi_14", "vol_ratio", "atr_pct", "ma_20"):
        value = out["trend"][key]
        if value is None:
            continue
        assert round(value, 2) == value, f"{key} = {value} 没四舍五入"
        checked += 1
    assert checked >= 4


def test_news_items_are_flagged_when_they_are_market_wide():
    """挂了很多只票的新闻要标出来，别让人当成这只票的专属消息。"""
    import pytest

    out = run("AAPL", horizon="short")
    if not out["news"]:
        pytest.skip("AAPL 近 14 天没有新闻")
    assert all("market_wide" in n for n in out["news"])


def test_gap_count_in_the_warning_matches_the_gap_list(monkeypatch):
    """★ 提示里说的「另有 N 项依据缺失」必须等于 data_gaps 的条数 ★

    缺口分三层：工具层 `gaps`（取数失败、没小时线、没基本面）、评分层
    `scored["gaps"]`、计划层 `plan["gaps"]`。data_gaps 是三者合并。

    原来触发条件和提示文案只数了工具层那一个，两个后果：
      1. 列 5 条却说 1 条，用户对不上；
      2. 更糟——**提示会在数据变好之后失灵**。2026-09-01 真踩到：
         早上 000001.SZ 工具层缺三四项，条件碰巧成立；下午 A股数据补齐、
         小时线改走实时之后那些缺口消失，`len(gaps)` 掉到 1，于是
         「82 分但只用上 45% 评分表」这种最该被警告的结果反而不警告了。
    """
    import re

    monkeypatch.setattr(at.scoring, "detect_short_setup", lambda snap: None)
    monkeypatch.setattr(at.scoring, "detect_mid_setup",
                        lambda snap, fund: None)
    monkeypatch.setattr(
        at.scoring, "score_mid",
        lambda snap, fund=None, news=None: {
            "score": 82, "evidence_pct": 45, "reasons": [], "risks": [],
            "gaps": ["探针缺口 1", "探针缺口 2", "探针缺口 3"]})

    out = run("000001.SZ", horizon="mid")
    said = [int(m.group(1)) for m in
            (re.search(r"另有 (\d+) 项依据缺失", r) for r in out["risks"]) if m]
    assert said, f"没有发出低置信度提示：{out['risks']}"
    assert said[0] == len(out["data_gaps"]), (
        f"提示里说缺 {said[0]} 项，data_gaps 实际有 "
        f"{len(out['data_gaps'])} 条：{out['data_gaps']}")
