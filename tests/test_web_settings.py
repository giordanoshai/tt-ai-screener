# -*- coding: utf-8 -*-
"""设置页那几块：模型清单、技能提示词，以及被删掉的那些端点。

三张表（`ai_models` / `skill_prompts` / `app_settings`）是本地 DuckDB 时代的
东西，迁到自建 Postgres 时漏掉了，线上根本没有，所以这三块功能一直是坏的，
界面上却只表现为「没配置」。2026-08-29 用户拍板：

* 模型配置 → 挪到 `config/models.json`，网页上**只读**（改文件即可），
  原来那套增删改的接口删掉；
* 技能提示词 → 挪到 `config/skill_prompts.json`，网页上照旧能看能改能重置；
* 语言切换 → **整个功能不要了**，语言固定从环境变量来；
* 采集三兄弟（`/update`、`/update/status`、`/update/stream`）→ 采集早就搬去
  tt-data-hub 了，留着的 410 存根和页面上的按钮都是假的，一起删。
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import skills

TEMPLATES = Path(__file__).parent.parent / "web" / "templates"


@pytest.fixture
def client():
    from web.app import app
    return TestClient(app)


@pytest.fixture
def prompts_file(tmp_path, monkeypatch):
    """自定义提示词指到临时文件，测试不动真的 config/skill_prompts.json。"""
    path = tmp_path / "skill_prompts.json"
    monkeypatch.setattr(skills, "PROMPTS_JSON", path)
    return path


# ---------------------------------------------------------------- 模型：只读

class TestModelsAreReadOnly:

    def test_list_comes_from_the_json_file(self, client):
        data = client.get("/models").json()
        ids = {m["id"] for m in data["models"]}
        assert len(ids) == 9, "config/models.json 里九个模型都该列出来"
        assert "qwen-max" in ids

    def test_note_tells_you_where_to_edit(self, client):
        assert "config/models.json" in client.get("/models").json().get("note", "")

    def test_listing_carries_no_secret(self, client):
        for model in client.get("/models").json()["models"]:
            assert "api_key" not in model
            assert "has_key" in model, "只说明配没配，不给值"

    def test_add_model_endpoint_is_gone(self, client):
        assert client.post("/models", json={"id": "x"}).status_code in (404, 405)

    def test_delete_model_endpoint_is_gone(self, client):
        assert client.delete("/models/qwen-max").status_code in (404, 405)

    def test_settings_page_has_no_model_editor(self):
        page = (TEMPLATES / "settings.html").read_text(encoding="utf-8")
        assert "Add Model" not in page, "模型现在只读，不该还有添加按钮"
        assert "deleteModel" not in page
        assert "saveModel" not in page
        assert "config/models.json" in page, "得告诉用户去哪儿改"


# ---------------------------------------------------------------- 提示词：JSON

class TestPromptsLiveInJson:

    def test_get_returns_default_custom_and_active(self, client, prompts_file):
        data = client.get("/settings/prompts").json()
        entry = data["prompts"]["swing_screen"]
        assert len(entry["default"]) > 100
        assert entry["custom"] == ""
        assert entry["active"] == entry["default"]
        assert entry["is_custom"] is False

    def test_get_reflects_a_saved_custom_prompt(self, client, prompts_file):
        prompts_file.write_text(json.dumps(
            {"swing_screen": {"en": "CUSTOM-FOR-TEST"}}), encoding="utf-8")
        entry = client.get("/settings/prompts").json()["prompts"]["swing_screen"]
        assert entry["custom"] == "CUSTOM-FOR-TEST"
        assert entry["active"] == "CUSTOM-FOR-TEST"
        assert entry["is_custom"] is True
        assert len(entry["default"]) > 100, "默认的还得能看"

    def test_save_writes_the_json_file(self, client, prompts_file):
        resp = client.post("/settings/prompts", json={
            "skill": "swing_screen", "prompt": "SAVED-BY-TEST"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert json.loads(prompts_file.read_text(encoding="utf-8")) == \
            {"swing_screen": {"en": "SAVED-BY-TEST"}}
        assert skills.load_skill_prompt("swing_screen", "en") == "SAVED-BY-TEST"

    def test_save_never_touches_the_database(self, client, prompts_file, monkeypatch):
        def boom(*_a, **_kw):
            raise AssertionError("提示词不该写库")
        monkeypatch.setattr("web.app.pg.execute_write", boom)
        monkeypatch.setattr("web.app.pg.fetch_write", boom)
        client.post("/settings/prompts", json={"skill": "swing_screen",
                                               "prompt": "X"})
        assert prompts_file.exists()

    def test_reset_removes_the_entry(self, client, prompts_file):
        client.post("/settings/prompts", json={"skill": "swing_screen",
                                               "prompt": "TEMP"})
        resp = client.post("/settings/prompts/reset", json={"skill": "swing_screen"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert json.loads(prompts_file.read_text(encoding="utf-8")) == {}
        assert skills.load_skill_prompt("swing_screen", "en") == \
            skills.load_default_skill_prompt("swing_screen", "en")

    def test_reset_when_nothing_saved_is_still_ok(self, client, prompts_file):
        resp = client.post("/settings/prompts/reset", json={"skill": "swing_screen"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_unknown_skill_rejected(self, client, prompts_file):
        assert client.post("/settings/prompts", json={
            "skill": "no_such_skill", "prompt": "X"}).status_code == 400
        assert client.post("/settings/prompts/reset", json={
            "skill": "no_such_skill"}).status_code == 400


# ---------------------------------------------------------------- 删掉的端点

class TestLanguageSwitchingIsGone:

    def test_both_lang_endpoints_are_gone(self, client):
        assert client.get("/settings/lang").status_code in (404, 405)
        assert client.post("/settings/lang", json={"lang": "cn"}).status_code \
            in (404, 405)

    def test_settings_page_has_no_language_switch(self):
        page = (TEMPLATES / "settings.html").read_text(encoding="utf-8")
        assert "/settings/lang" not in page
        assert "setLang" not in page

    def test_no_lang_route_left_in_the_source(self):
        """注释里提一句历史没问题，路由不该还在。"""
        src = (Path(__file__).parent.parent / "web" / "app.py").read_text(
            encoding="utf-8")
        assert '@app.get("/settings/lang")' not in src
        assert '@app.post("/settings/lang")' not in src


class TestUpdateStubsAreGone:

    @pytest.mark.parametrize("method,path", [
        ("post", "/update"), ("get", "/update/status"), ("get", "/update/stream")])
    def test_stub_endpoints_removed(self, client, method, path):
        assert getattr(client, method)(path).status_code in (404, 405)

    @pytest.mark.parametrize("name", ["home.html", "index.html"])
    def test_no_fake_update_button_left(self, name):
        page = (TEMPLATES / name).read_text(encoding="utf-8")
        assert "'/update'" not in page
        assert "/update/stream" not in page
        assert "triggerUpdate" not in page

    def test_last_update_card_still_works(self, client):
        """采集是 tt-data-hub 干的，但首页那张「最近一次采集」的卡片还留着。"""
        assert client.get("/api/update/last").status_code == 200


# ---------------------------------------------------------------- 页面还能开

class TestPagesStillRender:

    @pytest.mark.parametrize("path", ["/", "/settings", "/history", "/positions"])
    def test_page_renders(self, client, path):
        assert client.get(path).status_code == 200
