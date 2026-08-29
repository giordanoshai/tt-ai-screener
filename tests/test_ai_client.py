"""AI 模型配置的来源。

2026-08-29 实测：线上 Postgres 里【根本没有 ai_models 这张表】，而
get_model() / list_models() 一直在查它（而且用的是已被删掉的 db.init）。
后果是 get_model() 永远返回 None —— 网页里的 AI 分析是完全不能用的，
而且表现为「没配置模型」，会让人以为是自己配错了。

同时网页那套增删改模型的表单也存不进去（表不存在），填完就消失。

改成直接从环境变量读：config.py 里本来就有 AI_API_BASE / AI_API_KEY /
AI_MODEL 和一套 SENTIMENT_* ，.env.example 里也一直列着。
"""
import ai_client


class TestModelConfigComesFromEnv:

    def test_analysis_model_built_from_env(self, monkeypatch):
        monkeypatch.setattr(ai_client.config, "AI_API_BASE", "https://x/v1")
        monkeypatch.setattr(ai_client.config, "AI_API_KEY", "k1")
        monkeypatch.setattr(ai_client.config, "AI_MODEL", "gpt-4o")
        cfg = ai_client.get_model(role="analysis")
        assert cfg is not None
        assert cfg["api_base"] == "https://x/v1"
        assert cfg["api_key"] == "k1"
        assert cfg["model_id"] == "gpt-4o"

    def test_sentiment_role_uses_its_own_env(self, monkeypatch):
        monkeypatch.setattr(ai_client.config, "SENTIMENT_API_BASE", "https://s/v1")
        monkeypatch.setattr(ai_client.config, "SENTIMENT_API_KEY", "k2")
        monkeypatch.setattr(ai_client.config, "SENTIMENT_MODEL", "mini")
        cfg = ai_client.get_model(role="sentiment")
        assert cfg["api_base"] == "https://s/v1"
        assert cfg["model_id"] == "mini"

    def test_returns_none_when_not_configured(self, monkeypatch):
        """没配就是没配，返回 None 让上层显示「未配置」——这是诚实的。"""
        for k in ("AI_API_BASE", "AI_API_KEY", "AI_MODEL"):
            monkeypatch.setattr(ai_client.config, k, "")
        assert ai_client.get_model(role="analysis") is None

    def test_never_touches_the_database(self):
        """这两个函数一行数据库代码都不该有——那张表不存在，
        而且 db.init 已经随采集代码一起删掉了。"""
        import inspect
        src = inspect.getsource(ai_client.get_model) + inspect.getsource(ai_client.list_models)
        for banned in ("db.init", "get_conn", "ai_models", "duckdb"):
            assert banned not in src, "还在碰数据库: %s" % banned

    def test_list_models_reflects_what_is_configured(self, monkeypatch):
        monkeypatch.setattr(ai_client.config, "AI_API_BASE", "https://x/v1")
        monkeypatch.setattr(ai_client.config, "AI_MODEL", "gpt-4o")
        monkeypatch.setattr(ai_client.config, "SENTIMENT_API_BASE", "")
        monkeypatch.setattr(ai_client.config, "SENTIMENT_MODEL", "")
        ids = {m["id"] for m in ai_client.list_models()}
        assert ids == {"analysis"}, "只配了分析模型，就该只列出这一个"

    def test_list_models_empty_when_nothing_configured(self, monkeypatch):
        for k in ("AI_API_BASE", "AI_MODEL", "SENTIMENT_API_BASE", "SENTIMENT_MODEL"):
            monkeypatch.setattr(ai_client.config, k, "")
        assert ai_client.list_models() == []
