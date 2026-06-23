import secrets
from urllib.parse import parse_qs

from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse
from starlette.routing import Mount
from starlette.applications import Starlette
from starlette.types import ASGIApp, Receive, Scope, Send

from config import MCP_PORT, MCP_TOKEN, BASE_DIR
from tools.screening import screen_stocks
from tools.watchlist import list_watchlist, add_to_watchlist, remove_from_watchlist, analyze_watchlist
from tools.positions import list_positions, add_position, remove_position, analyze_positions

mcp = FastMCP("tt-ai-screener")

# Screening
mcp.tool()(screen_stocks)

# Watchlist
mcp.tool()(list_watchlist)
mcp.tool()(add_to_watchlist)
mcp.tool()(remove_from_watchlist)
mcp.tool()(analyze_watchlist)

# Positions
mcp.tool()(list_positions)
mcp.tool()(add_position)
mcp.tool()(remove_position)
mcp.tool()(analyze_positions)


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


def create_app(port: int | None = None) -> tuple[ASGIApp, str, int]:
    """Create combined ASGI app: Web on / + MCP on /mcp (with token auth)."""
    from web.app import app as web_app

    token = _ensure_token()
    actual_port = port or MCP_PORT

    mcp_app = mcp.streamable_http_app()
    mcp_app_authed = TokenAuthMiddleware(mcp_app, token)

    combined = Starlette(
        routes=[
            Mount("/mcp", app=mcp_app_authed),
            Mount("/", app=web_app),
        ],
    )

    return combined, token, actual_port


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
