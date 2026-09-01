# -*- coding: utf-8 -*-
"""main.py 和 server.py 的入口测试。

采集搬到 tt-data-hub 之后，这两个文件里但凡还留着一句指向采集代码的 import，
`python main.py` 就会在启动的第一秒炸掉——而且报的是
`ImportError: cannot import name 'DB_PATH' from 'config'` 这种看不出所以然的错。
所以这里钉死两件事：入口能 import、命令行不认已经删掉的开关。
"""
import ast
import asyncio
import inspect
import subprocess
import sys
from pathlib import Path

import pytest

import main
import server

BASE_DIR = Path(__file__).resolve().parent.parent

# 采集那一摊的模块名。入口文件里再出现任何一个都说明没清干净。
COLLECTOR_MODULES = ["db.init", "db.update", "db.fetch",
                     "db.universe", "db.migrate", "data_sources"]


def _code(module) -> str:
    """源码去掉文档字符串和注释，只留真正会跑的代码。"""
    src = inspect.getsource(module)
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                src = src.replace(doc, "")
    return "\n".join(line.split("#", 1)[0] for line in src.splitlines())


def _run_main(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "main.py", *args],
        cwd=BASE_DIR, capture_output=True, text=True, timeout=60)


class TestNoCollectorReferences:
    @pytest.mark.parametrize("module", COLLECTOR_MODULES)
    def test_main_free_of_collector(self, module):
        assert module not in _code(main)

    @pytest.mark.parametrize("module", COLLECTOR_MODULES)
    def test_server_free_of_collector(self, module):
        assert module not in _code(server)

    def test_main_does_not_create_tables(self):
        """表结构由 tt-data-hub 的建表脚本管，消费端不该建表。"""
        assert "init_db" not in _code(main)

    def test_main_has_no_update_or_cron(self):
        """采集搬走了，定时更新和情绪打分线程一并没了意义。"""
        code = _code(main)
        for gone in ("run_update", "_cron", "start_cron",
                     "_harvester", "start_harvester"):
            assert gone not in code, f"{gone} 应该已经删掉了"


class TestCommandLine:
    def test_help_exits_clean(self):
        result = _run_main("--help")
        assert result.returncode == 0, result.stderr
        assert "--mcp" in result.stdout
        assert "--port" in result.stdout

    @pytest.mark.parametrize("flag", ["--update", "--cron"])
    def test_removed_flags_rejected(self, flag):
        """已经删掉的开关必须明确报错，不能被静默忽略。"""
        result = _run_main(flag, "17:30")
        assert result.returncode != 0
        assert "unrecognized arguments" in result.stderr


class TestServerTools:
    """server.py 注册的工具列表就是这个 MCP 服务对外的全部能力。"""

    EXPECTED = {
        "review_positions",
        "scan_swing",
        "analyze_ticker",
        "detect_catalysts",
        "watch_positions",
        "screen_stocks",
        "list_watchlist", "add_to_watchlist", "remove_from_watchlist",
        "list_positions", "add_position", "remove_position",
    }

    def test_registered_tools(self):
        # list_tools 是协程；项目里没装 pytest-asyncio，直接跑一次事件循环
        tools = asyncio.run(server.mcp.list_tools())
        assert {t.name for t in tools} == self.EXPECTED


MCP_TEST_HOST = "127.0.0.1:8766"


@pytest.fixture(scope="module")
def client_and_token():
    """组合应用的测试客户端，整个模块共用一个。

    必须用 with：TestClient 只有作为上下文管理器时才会跑 lifespan，而 MCP 的
    会话管理器就在 lifespan 里启动；少了这个 with，测出来的就是部署时踩的
    那个 500。

    scope 是 module 而不是 function：``StreamableHTTPSessionManager.run()``
    一个实例只能跑一次，而 ``server.mcp`` 是模块级单例，所以整个测试进程里
    ``create_app()`` 只能起一次。
    """
    from starlette.testclient import TestClient
    app, token, _ = server.create_app()
    with TestClient(app, base_url=f"http://{MCP_TEST_HOST}") as client:
        yield client, token


class TestCombinedAppServesMCP:
    """网页 + MCP 共用一个端口的那个组合应用，必须真的能应答 MCP 请求。

    2026-09-01 第一次部署到服务器时，这里接连炸了三次，而本地测试全绿——
    因为在此之前**没有任何测试起过这个组合应用**，测的都是 stdio 那条路。
    三次分别是：

    1. Starlette 不会自动去跑被 Mount 进来的子应用的 lifespan，MCP 的会话
       管理器压根没启动，每个请求 500，报
       ``RuntimeError: Task group is not initialized``。
    2. ``Mount("/mcp")`` 匹配的是 ``/mcp/...``，**裸的 /mcp 不匹配**，请求
       掉到网页应用上返回 404，鉴权中间件根本没被调用——现象是「错误令牌
       返回 404 而不是 401」，看起来像路由不存在。
    3. MCP SDK 自带的 Host 白名单默认只有 127.0.0.1 / localhost，局域网 IP
       和隧道域名一律 421 Misdirected Request。

    注意：``StreamableHTTPSessionManager.run()`` 一个实例只能跑一次，而
    ``server.mcp`` 是模块级单例，所以整个测试进程里 ``create_app()`` 只能
    起一次——用 class 级 fixture 共用同一个客户端，别在别处再起一个。
    """

    BODY = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "pytest", "version": "0"}},
    }
    HOST = MCP_TEST_HOST

    def _headers(self, host=None):
        return {"Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "Host": host or self.HOST}

    def test_initialize_over_http(self, client_and_token):
        """文档和网页 /api/mcp-url 给出的都是不带斜杠的 /mcp，它必须能用。"""
        client, token = client_and_token
        r = client.post(f"/mcp?token={token}", json=self.BODY,
                        headers=self._headers())
        assert r.status_code == 200, r.text

    def test_trailing_slash_also_works(self, client_and_token):
        client, token = client_and_token
        r = client.post(f"/mcp/?token={token}", json=self.BODY,
                        headers=self._headers())
        assert r.status_code == 200, r.text

    def test_wrong_token_is_401_not_404(self, client_and_token):
        """401 而不是 404——404 说明请求没走到鉴权，路由挂了。"""
        client, _ = client_and_token
        r = client.post("/mcp?token=WRONG", json=self.BODY,
                        headers=self._headers())
        assert r.status_code == 401

    def test_missing_token_is_401(self, client_and_token):
        client, _ = client_and_token
        r = client.post("/mcp", json=self.BODY, headers=self._headers())
        assert r.status_code == 401

    def test_unlisted_host_is_rejected(self, client_and_token):
        """没配进 MCP_ALLOWED_HOSTS 的 Host 必须被挡（防 DNS 重绑定）。"""
        client, token = client_and_token
        r = client.post(f"/mcp?token={token}", json=self.BODY,
                        headers=self._headers(host="evil.example.net"))
        assert r.status_code == 421

    def test_web_dashboard_still_served(self, client_and_token):
        """MCP 挂上去之后网页不能被挤掉。"""
        client, _ = client_and_token
        assert client.get("/").status_code == 200
