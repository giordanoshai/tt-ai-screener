# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**tt-trading-mcp** is an open-source self-hostable MCP (Model Context Protocol) server for personal stock trading analysis. Anyone can connect it to Claude.ai and build their own stock database for long-term investing, swing trading, position tracking, and trading psychology journaling.

The design philosophy: **Claude is the UI**. No web dashboard, no custom frontend — users interact entirely through Claude.ai conversations backed by this MCP server.

---

## MCP Tools Reference

This is the canonical list of tools this server exposes. Each tool maps to a function in `tools/`.

### Screening
| Tool | Params | Description |
|---|---|---|
| `screen_stocks` | `screen_type`, `sector`, `min_market_cap`, `max_pe`, `min_growth`, `min_margin`, `min_rsi`, `max_rsi`, `above_ma`, `limit=20` | Parameterized stock screener. **At least one filter required** (no filter = rejected). `screen_type`: `longterm`/`swing` determines scoring. Filters: sector name, market cap (billions), PE cap, growth % floor, margin % floor, RSI range, MA price floor (20/50/200) |

### Watchlist
| Tool | Params | Description |
|---|---|---|
| `list_watchlist` | — | List all watchlist tickers with latest price, daily change, and technicals |
| `add_to_watchlist` | `ticker`(required) | Add ticker to watchlist |
| `remove_from_watchlist` | `ticker`(required) | Remove ticker from watchlist |
| `analyze_watchlist` | `analysis_type='longterm'`/`'swing'` | Run longterm or swing analysis on all watchlist tickers via skills engine |

### Positions
| Tool | Params | Description |
|---|---|---|
| `list_positions` | — | List all positions with current price, P&L (% and $), and portfolio summary |
| `add_position` | `ticker`(required), `avg_cost`(required), `shares`(required) | Add or update a position |
| `remove_position` | `ticker`(required) | Remove a position |
| `analyze_positions` | — | Comprehensive analysis of all positions: technicals, fundamentals, news, P&L via skills engine |

---

## Architecture

```
tt-trading-mcp/
  server.py              # MCP server entrypoint — registers all tools
  config.py              # DB path, API keys, data source settings
  ai_client.py           # OpenAI-compatible AI client (sentiment + analysis)
  tools/
    screening.py         # screen_stocks (parameterized screener, filter required)
    watchlist.py         # list/add/remove watchlist, analyze_watchlist
    positions.py         # list/add/remove positions, analyze_positions
  data_sources/
    base.py              # Provider abstract classes
    us_market.py         # yfinance + Finnhub implementations
    registry.py          # Provider registry
  db/
    init.py              # DuckDB schema + migrations
    update.py            # Daily incremental update pipeline
    fetch.py             # Initial data fetch
    universe.py          # CSV ticker import
  skills/
    __init__.py          # Analysis engine — DB queries + AI API orchestration
    longterm_screen.md   # Long-term investing skill prompt
    swing_screen.md      # Swing trade skill prompt
    position_review.md   # Position review skill prompt
  web/
    app.py               # FastAPI web server
    templates/
      index.html         # Watchlist page
      screener.html      # Multi-dimensional stock screener
  .skills/               # Claude Code superpowers plugin format (ZIP)
  requirements.txt
```

**Key design rule:** All filtering and aggregation happens inside the tool function (database or Python side). Tools return lightweight summaries to Claude — never raw full tables. This keeps token usage low.

---

## Data Sources

Users configure in `.env`:

```bash
FINNHUB_KEY=xxx                    # Required
FINNHUB_TIER=free                  # free | premium (rate limit)
DB_PATH=./trading.duckdb           # DuckDB database
SENTIMENT_API_BASE=...             # AI: news sentiment scoring
AI_API_BASE=...                    # AI: skills analysis engine
```

**Data providers:**
- yfinance: OHLCV (batch), analyst ratings, valuation fallbacks
- Finnhub: fundamentals, earnings calendar, insider MSPR, news
- AI API (OpenAI-compatible): sentiment scoring, skills analysis

**Core database tables** (DuckDB, defined in `db/init.py`):
- `stocks_meta` — ticker metadata (company, sector, tier)
- `stock_ohlcv_daily` — daily OHLCV + RSI14 + MAs + vol_ratio + atr
- `stock_fundamentals` — 18 fields: valuation, growth, analyst, earnings, MSPR
- `news` — news articles with AI sentiment_label + sentiment_score
- `trades` — trade records
- `user_watchlist` — user's tracked tickers
- `user_positions` — portfolio positions (ticker, avg_cost, shares)

---

## MCP Protocol

Built with the [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk). Tools use `@mcp.tool()` decorators. Transport: `stdio` (default for Claude.ai desktop/CLI).

```python
# server.py pattern
from mcp.server.fastmcp import FastMCP
from tools.screening import screen_stocks
from tools.watchlist import list_watchlist, add_to_watchlist, remove_from_watchlist, analyze_watchlist
from tools.positions import list_positions, add_position, remove_position, analyze_positions

mcp = FastMCP("tt-trading-mcp")
mcp.tool()(screen_stocks)
mcp.tool()(list_watchlist)
# ... register all 9 tools

if __name__ == "__main__":
    mcp.run()
```

---

## Claude.ai Integration

### Remote (Claude.ai web — recommended)

```bash
python main.py
# Prints:
#   Web dashboard:  http://localhost:8766
#   MCP endpoint:   http://localhost:8766/mcp?token=<auto-generated>
```

In Claude.ai → Settings → MCP → Add custom integration, paste the MCP endpoint URL.
Token auto-generates on first run and saves to `.env`.

### Local (Claude Desktop)

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "trading": {
      "command": "python",
      "args": ["/path/to/tt-trading-mcp/server.py"],
      "env": {
        "DB_PATH": "/path/to/your/trading.db"
      }
    }
  }
}
```

---

## Development Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # edit with your API keys
```

```bash
# Unified entry point
python main.py              # Web + MCP remote on one port (default :8766)
python main.py --mcp        # MCP stdio only (for Claude Desktop)
python main.py --update     # Run data update once
python main.py --cron 17:30 # Web + MCP + daily auto-update at 5:30 PM

# MCP inspector (debug tool calls)
npx @modelcontextprotocol/inspector python server.py
```
