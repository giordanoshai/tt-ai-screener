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
        "screen_stocks",
        "list_watchlist", "add_to_watchlist", "remove_from_watchlist",
        "analyze_watchlist",
        "list_positions", "add_position", "remove_position",
        "analyze_positions",
        "get_hourly_setup",
    }

    def test_registered_tools(self):
        # list_tools 是协程；项目里没装 pytest-asyncio，直接跑一次事件循环
        tools = asyncio.run(server.mcp.list_tools())
        assert {t.name for t in tools} == self.EXPECTED
