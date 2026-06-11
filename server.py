from mcp.server.fastmcp import FastMCP
from tools.screening import screen_longterm_candidates, screen_swing_candidates
from tools.trades import analyze_position, get_recent_trades

mcp = FastMCP("tt-trading-mcp")

mcp.tool()(screen_longterm_candidates)
mcp.tool()(screen_swing_candidates)
mcp.tool()(analyze_position)
mcp.tool()(get_recent_trades)

if __name__ == "__main__":
    mcp.run()
