# -*- coding: utf-8 -*-
"""AI 模型配置的来源：本地 JSON 文件 `config/models.json`。

来龙去脉：这套配置原来存在本地 DuckDB 的 `ai_models` 表里。迁到自建 Postgres
的时候这张表漏掉了，线上库里根本没有，于是 `get_model()` 永远返回 None ——
网页里的 AI 分析完全不能用，界面上还只显示「没配置模型」，让人以为是自己填错了。

中途试过改读环境变量，但那样一个角色只能配一个模型，原来那九个模型（不同厂商、
不同价位、分析和情绪打分各用各的）就没地方放了。所以最终改成读一个本地 JSON。

**密钥不在 JSON 里。** 每个模型带一个 `api_key_env` 字段，写的是环境变量的
名字（比如 `ANTHROPIC_API_KEY`），真实的值在 `.env`。`get_model()` 拿到配置
之后再去 `os.getenv` 取密钥。这样 JSON 可以进版本库而不泄密。

环境变量那一套（`AI_API_BASE` / `AI_MODEL` 等）保留成兜底：JSON 文件不存在、
或者里面一个模型都没有的时候才走。
"""
import json

import pytest

import ai_client


# 测试用的假模型清单。用明显是假的地址和模型名，免得跟真配置混淆。
FAKE_MODELS = {
    "_说明": "测试用",
    "models": [
        {
            "id": "fake-analysis-default",
            "display_name": "假·分析默认",
            "api_base": "https://fake.invalid/analysis",
            "model_id": "fake-analysis-1",
            "api_format": "openai",
            "role": "analysis",
            "supports_thinking": True,
            "is_default_sentiment": False,
            "is_default_analysis": True,
            "enabled": True,
            "api_key_env": "FAKE_KEY_A",
        },
        {
            "id": "fake-analysis-other",
            "display_name": "假·分析备选",
            "api_base": "https://fake.invalid/other",
            "model_id": "fake-analysis-2",
            "api_format": "anthropic",
            "role": "analysis",
            "supports_thinking": False,
            "is_default_sentiment": False,
            "is_default_analysis": False,
            "enabled": True,
            "api_key_env": "FAKE_KEY_B",
        },
        {
            "id": "fake-sentiment",
            "display_name": "假·情绪打分",
            "api_base": "https://fake.invalid/sentiment",
            "model_id": "fake-sentiment-1",
            "api_format": "openai",
            "role": "sentiment",
            "supports_thinking": False,
            "is_default_sentiment": True,
            "is_default_analysis": False,
            "enabled": True,
            "api_key_env": "FAKE_KEY_A",
        },
        {
            "id": "fake-both",
            "display_name": "假·两用",
            "api_base": "https://fake.invalid/both",
            "model_id": "fake-both-1",
            "api_format": "openai",
            "role": "both",
            "supports_thinking": False,
            "is_default_sentiment": False,
            "is_default_analysis": False,
            "enabled": True,
            "api_key_env": "FAKE_KEY_A",
        },
        {
            "id": "fake-disabled",
            "display_name": "假·已停用",
            "api_base": "https://fake.invalid/off",
            "model_id": "fake-off-1",
            "api_format": "openai",
            "role": "both",
            "supports_thinking": False,
            "is_default_sentiment": False,
            "is_default_analysis": False,
            "enabled": False,
            "api_key_env": "FAKE_KEY_A",
        },
    ],
}


@pytest.fixture
def models_file(tmp_path, monkeypatch):
    """把模型清单指到一个临时文件，测试不碰真的 config/models.json。"""
    path = tmp_path / "models.json"

    def write(payload):
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    write(FAKE_MODELS)
    monkeypatch.setattr(ai_client, "MODELS_JSON", path)
    monkeypatch.setenv("FAKE_KEY_A", "key-aaa")
    monkeypatch.setenv("FAKE_KEY_B", "key-bbb")
    return write


class TestModelsComeFromJson:

    def test_default_analysis_model_is_picked(self, models_file):
        cfg = ai_client.get_model(role="analysis")
        assert cfg is not None
        assert cfg["id"] == "fake-analysis-default"
        assert cfg["api_base"] == "https://fake.invalid/analysis"
        assert cfg["model_id"] == "fake-analysis-1"
        assert cfg["api_format"] == "openai"
        assert cfg["supports_thinking"] is True

    def test_default_sentiment_model_is_picked(self, models_file):
        cfg = ai_client.get_model(role="sentiment")
        assert cfg["id"] == "fake-sentiment"
        assert cfg["model_id"] == "fake-sentiment-1"

    def test_explicit_id_wins(self, models_file):
        cfg = ai_client.get_model(model_id="fake-analysis-other")
        assert cfg["id"] == "fake-analysis-other"
        assert cfg["api_format"] == "anthropic"

    def test_unknown_id_falls_back_to_role_default(self, models_file):
        """网页上换过模型之后又删掉那条配置，不该整个分析都用不了。"""
        cfg = ai_client.get_model(model_id="no-such-model", role="analysis")
        assert cfg["id"] == "fake-analysis-default"

    def test_role_both_counts_for_either_role(self, models_file, monkeypatch):
        """role=both 的模型在两个角色里都能被选中。"""
        only_both = {"models": [m for m in FAKE_MODELS["models"]
                                if m["id"] == "fake-both"]}
        models_file(only_both)
        assert ai_client.get_model(role="analysis")["id"] == "fake-both"
        assert ai_client.get_model(role="sentiment")["id"] == "fake-both"

    def test_no_default_flag_uses_first_usable(self, models_file):
        no_default = {"models": [
            dict(m, is_default_analysis=False, is_default_sentiment=False)
            for m in FAKE_MODELS["models"] if m["role"] == "analysis"]}
        models_file(no_default)
        assert ai_client.get_model(role="analysis")["id"] == "fake-analysis-default"

    def test_role_with_no_model_returns_none(self, models_file):
        """配了模型但没有能干这活的，就是没有——不要假装有。"""
        analysis_only = {"models": [m for m in FAKE_MODELS["models"]
                                    if m["role"] == "analysis"]}
        models_file(analysis_only)
        assert ai_client.get_model(role="sentiment") is None


class TestApiKeyStaysOutOfJson:
    """密钥绝不写进 JSON，只放环境变量名，取的时候再去环境里拿。"""

    def test_key_read_from_the_named_env_var(self, models_file):
        assert ai_client.get_model(model_id="fake-analysis-default")["api_key"] == "key-aaa"
        assert ai_client.get_model(model_id="fake-analysis-other")["api_key"] == "key-bbb"

    def test_missing_env_var_gives_empty_key_not_a_crash(self, models_file, monkeypatch):
        monkeypatch.delenv("FAKE_KEY_A", raising=False)
        cfg = ai_client.get_model(model_id="fake-analysis-default")
        assert cfg is not None
        assert cfg["api_key"] == ""

    def test_real_config_file_contains_no_secrets(self):
        """真正进版本库的那个文件里不许出现任何看起来像密钥的东西。"""
        raw = ai_client.MODELS_JSON.read_text(encoding="utf-8")
        data = json.loads(raw)
        for model in data["models"]:
            assert "api_key" not in model, f"{model['id']} 里写了密钥"
            assert model.get("api_key_env"), f"{model['id']} 没写 api_key_env"
        for marker in ("sk-", "sk_", "Bearer "):
            assert marker not in raw, f"配置文件里疑似有密钥：{marker}"


class TestListModels:

    def test_lists_enabled_models_only(self, models_file):
        ids = {m["id"] for m in ai_client.list_models()}
        assert "fake-disabled" not in ids
        assert ids == {"fake-analysis-default", "fake-analysis-other",
                       "fake-sentiment", "fake-both"}

    def test_role_filter_includes_both(self, models_file):
        ids = {m["id"] for m in ai_client.list_models(role="sentiment")}
        assert ids == {"fake-sentiment", "fake-both"}

    def test_entries_carry_what_the_settings_page_needs(self, models_file):
        row = next(m for m in ai_client.list_models()
                   if m["id"] == "fake-analysis-default")
        for key in ("display_name", "model_id", "api_format", "role",
                    "supports_thinking", "is_default_analysis",
                    "is_default_sentiment", "api_base", "has_key"):
            assert key in row, f"缺字段 {key}"
        assert row["has_key"] is True

    def test_has_key_false_when_env_missing(self, models_file, monkeypatch):
        monkeypatch.delenv("FAKE_KEY_A", raising=False)
        row = next(m for m in ai_client.list_models()
                   if m["id"] == "fake-analysis-default")
        assert row["has_key"] is False

    def test_listing_never_carries_a_secret(self, models_file):
        """列表是给网页用的，不能把密钥带出去。"""
        for row in ai_client.list_models():
            assert "api_key" not in row


class TestEnvFallback:
    """JSON 不存在或者是空的，才回到环境变量那一套。"""

    def test_missing_file_falls_back_to_env(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ai_client, "MODELS_JSON", tmp_path / "nope.json")
        monkeypatch.setattr(ai_client.config, "AI_API_BASE", "https://x/v1")
        monkeypatch.setattr(ai_client.config, "AI_API_KEY", "k1")
        monkeypatch.setattr(ai_client.config, "AI_MODEL", "gpt-4o")
        cfg = ai_client.get_model(role="analysis")
        assert cfg["api_base"] == "https://x/v1"
        assert cfg["api_key"] == "k1"
        assert cfg["model_id"] == "gpt-4o"

    def test_empty_model_list_falls_back_to_env(self, models_file, monkeypatch):
        models_file({"models": []})
        monkeypatch.setattr(ai_client.config, "SENTIMENT_API_BASE", "https://s/v1")
        monkeypatch.setattr(ai_client.config, "SENTIMENT_API_KEY", "k2")
        monkeypatch.setattr(ai_client.config, "SENTIMENT_MODEL", "mini")
        assert ai_client.get_model(role="sentiment")["model_id"] == "mini"

    def test_broken_json_falls_back_instead_of_crashing(self, tmp_path, monkeypatch):
        bad = tmp_path / "bad.json"
        bad.write_text("{ this is not json", encoding="utf-8")
        monkeypatch.setattr(ai_client, "MODELS_JSON", bad)
        monkeypatch.setattr(ai_client.config, "AI_API_BASE", "https://x/v1")
        monkeypatch.setattr(ai_client.config, "AI_MODEL", "gpt-4o")
        assert ai_client.get_model(role="analysis")["model_id"] == "gpt-4o"

    def test_nothing_configured_anywhere_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ai_client, "MODELS_JSON", tmp_path / "nope.json")
        for key in ("AI_API_BASE", "AI_API_KEY", "AI_MODEL"):
            monkeypatch.setattr(ai_client.config, key, "")
        assert ai_client.get_model(role="analysis") is None

    def test_list_models_empty_when_nothing_configured(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ai_client, "MODELS_JSON", tmp_path / "nope.json")
        for key in ("AI_API_BASE", "AI_MODEL",
                    "SENTIMENT_API_BASE", "SENTIMENT_MODEL"):
            monkeypatch.setattr(ai_client.config, key, "")
        assert ai_client.list_models() == []


class TestNoDatabase:
    def test_never_touches_the_database(self):
        """这几个函数一行数据库代码都不该有——那张表不存在，
        而且 db.init 已经随采集代码一起删掉了。"""
        import inspect
        src = "".join(inspect.getsource(fn) for fn in (
            ai_client.get_model, ai_client.list_models,
            ai_client._load_models_file))
        for banned in ("db.init", "get_conn", "ai_models", "duckdb", "pg."):
            assert banned not in src, "还在碰数据库: %s" % banned


class TestLiveConfigFile:
    """真的那份 config/models.json 得是能用的，不然网页上还是「没配置模型」。"""

    def test_default_analysis_and_sentiment_both_resolve(self):
        assert ai_client.get_model(role="analysis") is not None
        assert ai_client.get_model(role="sentiment") is not None

    def test_lists_nine_models(self):
        assert len(ai_client.list_models()) == 9
