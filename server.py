import secrets
from contextlib import asynccontextmanager
from urllib.parse import parse_qs

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.routing import Mount
from starlette.applications import Starlette
from starlette.types import ASGIApp, Receive, Scope, Send

from config import MCP_PORT, MCP_TOKEN, MCP_ALLOWED_HOSTS, BASE_DIR
from tools.screening import screen_stocks
from tools.watchlist import list_watchlist, add_to_watchlist, remove_from_watchlist
from tools.positions import list_positions, add_position, remove_position
from tools.review_positions import review_positions
from tools.scan_swing import scan_swing
from tools.analyze_ticker import analyze_ticker
from tools.detect_catalysts import detect_catalysts
from tools.watch_positions import watch_positions

# streamable_http_path 默认是 "/mcp"，而这个应用又把它整个 Mount 在 "/mcp"
# 底下，两段路径会叠起来，真实地址变成 "/mcp/mcp"——跟 README、CLAUDE.md
# 以及 web 的 /api/mcp-url 交出去的地址都对不上，照着填进 Claude.ai 会 404。
# 设成 "/" 之后，挂载点自己就是终点，地址回到文档里写的 "/mcp"。
# MCP SDK 的 Host 白名单默认只有 127.0.0.1 / localhost / [::1]，
# 局域网 IP 和隧道域名都会被 421 挡掉。允许的主机名从 .env 的
# MCP_ALLOWED_HOSTS 读（逗号分隔，端口可用 * 通配）。
#
# 不关掉这层防护、而是做成可配置的：它防的是「恶意网页诱导浏览器往本机服务
# 发请求」，令牌校验虽然也能挡，但多一层没坏处，代价只是配一行环境变量。
_ALLOWED_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"] + MCP_ALLOWED_HOSTS
_ALLOWED_ORIGINS = [f"{scheme}://{h}"
                    for h in _ALLOWED_HOSTS
                    for scheme in ("http", "https")]

mcp = FastMCP(
    "tt-ai-screener",
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_ALLOWED_HOSTS,
        allowed_origins=_ALLOWED_ORIGINS,
    ),
)

# 五个场景工具：围绕实际交易场景，直接返回算好的结论
mcp.tool()(review_positions)     # 我手上这几只票该不该动
mcp.tool()(scan_swing)           # 盘后找值得做的波段机会
mcp.tool()(analyze_ticker)       # 单独一只票的完整体检
mcp.tool()(detect_catalysts)     # 出了什么事，跟我有没有关系
mcp.tool()(watch_positions)      # 持仓上有没有要立刻处理的信号

# 选股（长线，本轮没动）
mcp.tool()(screen_stocks)

# 自选池的增删查
mcp.tool()(list_watchlist)
mcp.tool()(add_to_watchlist)
mcp.tool()(remove_from_watchlist)

# 持仓的增删查
mcp.tool()(list_positions)
mcp.tool()(add_position)
mcp.tool()(remove_position)

# 下线的三个：
#   analyze_watchlist -> scan_swing(scope="pool")
#   analyze_positions -> review_positions
#   get_hourly_setup  -> 并进 analyze_ticker（函数还在 tools/hourly.py，
#                        网页面板还在用，只是不再单独暴露给 MCP）


def _ensure_token() -> str:
    """Return MCP_TOKEN from env, or generate one and append to .env."""
    if MCP_TOKEN:
        return MCP_TOKEN
    token = secrets.token_urlsafe(32)
    env_file = BASE_DIR / ".env"
    with open(env_file, "a", encoding="utf-8") as f:
        f.write(f"\nMCP_TOKEN={token}\n")
    return token


class TokenAuthMiddleware:
    """Check ?token= query param or Authorization: Bearer header on /mcp paths."""

    def __init__(self, app: ASGIApp, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] in ("http", "websocket"):
            qs = parse_qs(scope.get("query_string", b"").decode())
            token_from_qs = qs.get("token", [None])[0]

            token_from_header = None
            for name, value in scope.get("headers", []):
                if name == b"authorization":
                    parts = value.decode().split(" ", 1)
                    if len(parts) == 2 and parts[0].lower() == "bearer":
                        token_from_header = parts[1]
                    break

            if token_from_qs != self.token and token_from_header != self.token:
                response = JSONResponse({"error": "unauthorized"}, status_code=401)
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)


class NormalizeMcpPath:
    """让 "/mcp" 和 "/mcp/" 都能进到 MCP 应用。

    Starlette 的 Mount("/mcp") 匹配的是 "/mcp/..."，**裸的 "/mcp" 不匹配**，
    会一路掉到 Mount("/", 网页) 上，返回网页的 404——鉴权中间件根本没被调用，
    所以现象是「错误令牌也返回 404 而不是 401」，很容易被当成路由不存在。

    而 README、CLAUDE.md 和网页 /api/mcp-url 交出去的地址都是不带斜杠的
    "/mcp?token=..."。用 307 跳转也能解决，但那要赌客户端在 POST 带 body 时
    会不会跟随跳转，不是所有 MCP 客户端都跟。所以这里直接改写路径，
    对客户端完全透明。

    lifespan 的 scope 不是 http/websocket，会原样透传，不受影响。
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] in ("http", "websocket") and scope.get("path") == "/mcp":
            scope = dict(scope, path="/mcp/", raw_path=b"/mcp/")
        await self.app(scope, receive, send)


def create_app(port: int | None = None) -> tuple[ASGIApp, str, int]:
    """Create combined ASGI app: Web on / + MCP on /mcp (with token auth)."""
    from web.app import app as web_app

    token = _ensure_token()
    actual_port = port or MCP_PORT

    mcp_app = mcp.streamable_http_app()
    mcp_app_authed = TokenAuthMiddleware(mcp_app, token)

    # Starlette 不会自动去跑被 Mount 进来的子应用的 lifespan，而 MCP 的
    # streamable-http 会话管理器正是在它自己的 lifespan 里启动的。不把这一层
    # 手动接上来，路由是通的、鉴权也过，但每一个 MCP 请求都会 500：
    #     RuntimeError: Task group is not initialized. Make sure to use run().
    #
    # 2026-09-01 第一次真正部署时踩到。本地测试全绿也发现不了——测试没有
    # 起过这个组合应用，走的是 stdio。
    @asynccontextmanager
    async def lifespan(_app):
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    combined = Starlette(
        routes=[
            Mount("/mcp", app=mcp_app_authed),
            Mount("/", app=web_app),
        ],
        lifespan=lifespan,
    )

    return NormalizeMcpPath(combined), token, actual_port


def run_server(port: int | None = None):
    """Start combined Web + MCP server."""
    import uvicorn

    app, token, actual_port = create_app(port)

    print(f"Web dashboard:  http://localhost:{actual_port}")
    print(f"MCP endpoint:   http://localhost:{actual_port}/mcp?token={token}")
    uvicorn.run(app, host="0.0.0.0", port=actual_port, log_level="info")


if __name__ == "__main__":
    import sys
    print("Hint: use 'python main.py' for full server, or 'python main.py --mcp' for stdio", file=sys.stderr)
    mcp.run()
