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
| `screen_longterm_candidates` | `limit=20` | Fundamental screening: high growth, high margins, reasonable valuation + bullish yearly MA |
| `screen_swing_candidates` | `limit=20`, `setup_type='all'/'breakout'/'pullback'` | Technical screening: breakout / pullback setups, all filtering done server-side |

### Trades
| Tool | Params | Description |
|---|---|---|
| `get_recent_trades` | `limit`, `status`, `start_date`, `end_date`, `trade_type`, `ticker`, `direction` | Query trade history. `trade_type`: `PAPER`=模拟, `STRAND`=实盘. `direction`: `LONG`/`SHORT` |
| `get_recent_trades_light` | same as above | Lightweight version, less fields, saves tokens |
| `get_trade_details` | `trade_id` (uuid or display_id) | Full trade detail including all execution legs |
| `get_trade_emotions` | — | Emotional state logs linked to trades |

### Market Data
| Tool | Params | Description |
|---|---|---|
| `get_stock_ohlcv` | `ticker`(required), `start_date`, `end_date`, `limit=100` | Daily OHLCV + RSI14 + MAs for a ticker |
| `get_stock_fundamentals` | `ticker`(required) | Latest fundamental snapshot for a ticker |
| `get_latest_market_breadth` | `limit=5` | Recent market breadth & trend data |
| `get_market_regime_history` | `limit=10`, `include_config=False` | Macro market regime: trend, volatility, trading recommendations. Set `include_config=True` for full scoring rules (token-heavy) |
| `get_daily_news_summary` | `limit=5` | Daily news digest (morning + evening) |
| `get_news_sentiment_summary` | `ticker`(required), `days=7` | Aggregated sentiment stats for a ticker, no full text, token-efficient |
| `get_option_market_snapshots` | `trade_id` or `option_code`, `limit=100` | Options Greeks snapshot (delta, theta, IV) for a position |
| `get_option_ohlcv` | — | Options OHLCV data |

### Watchlist
| Tool | Params | Description |
|---|---|---|
| `get_user_watchlist` | `limit=100` | Current watchlist |
| `add_to_watchlist` | `ticker`(required) | Add ticker to watchlist |
| `remove_from_watchlist` | `ticker`(required) | Remove ticker from watchlist |
| `search_stocks_meta` | — | Search stock metadata |

### Strategy & Rules
| Tool | Params | Description |
|---|---|---|
| `get_strategy_templates` | `only_active=True` | User-defined strategy templates with buy/sell criteria and risk settings |
| `get_trading_rules` | — | Risk parameters: max daily loss, max position size, etc. |
| `get_strategy_signals` | — | Active signals from strategy templates |

### Journal & Notes
| Tool | Params | Description |
|---|---|---|
| `get_trader_daily_logs` | `limit=10`, `start_date` | Daily psychology log: sleep, focus, mood score, review notes |
| `create_or_update_trader_daily_log` | — | Write/update a daily log entry |
| `get_daily_trade_notes` | — | Notes attached to specific trades |
| `get_recent_notes` | — | Recently created notes |
| `create_note` | — | Create a new note |
| `update_note` | — | Update existing note |

### System
| Tool | Params | Description |
|---|---|---|
| `get_activity_sessions` | — | User activity session history |
| `get_energy_schedules` | — | Energy/focus schedule configuration |
| `get_reward_config` | — | Gamification reward configuration |
| `get_system_logs` | — | Server-side system logs |
| `analyze_server_status` | — | Server health check |
| `search_stock_news` | — | Search news by keyword/ticker |

---

## Architecture

```
tt-trading-mcp/
  server.py              # MCP server entrypoint — registers all tools
  tools/
    screening.py         # screen_longterm_candidates, screen_swing_candidates
    trades.py            # get_recent_trades, get_trade_details, get_trade_emotions
    market.py            # ohlcv, fundamentals, breadth, regime, news, options
    watchlist.py         # get/add/remove watchlist, search_stocks_meta
    strategy.py          # strategy_templates, trading_rules, signals
    journal.py           # daily_logs, notes, create_or_update
    system.py            # activity_sessions, server_status, system_logs
  db/
    schema.sql           # Full SQLite schema
    seed.py              # Sample data / import scripts
  config.py              # DB path, API keys, data source settings
  requirements.txt
```

**Key design rule:** All filtering and aggregation happens inside the tool function (database or Python side). Tools return lightweight summaries to Claude — never raw full tables. This keeps token usage low.

---

## Data Sources

Users configure their data source in `config.py`:

```python
DB_PATH = "~/trading.db"          # SQLite (default, self-hosted)
MARKET_DATA_PROVIDER = "yfinance" # yfinance | polygon | alpaca | csv
```

**Supported ingestion paths:**
- SQLite database (default) — user maintains their own DB
- CSV import from brokerage exports (IBKR, Schwab, Tastytrade)
- Market data APIs: yfinance (free), Polygon.io, Alpaca

**Core database tables** (defined in `db/schema.sql`):
- `trades` — trade records with status, direction, trade_type, executions
- `stock_ohlcv_daily` — daily price data + RSI14 + MAs
- `stock_fundamentals` — fundamental snapshots per ticker
- `market_breadth` — daily breadth indicators
- `market_regime_history` — macro regime classification
- `user_watchlist` — user's tracked tickers
- `strategy_templates` — user-defined buy/sell strategies
- `trading_rules` — risk parameters
- `trader_daily_log` — psychology journal
- `notes` — freeform notes
- `news_articles` — ingested news with sentiment scores

---

## MCP Protocol

Built with the [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk). Tools use `@mcp.tool()` decorators. Transport: `stdio` (default for Claude.ai desktop/CLI).

```python
# server.py pattern
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("tt-trading-mcp")

from tools.screening import screen_longterm_candidates, screen_swing_candidates
mcp.tool()(screen_longterm_candidates)
mcp.tool()(screen_swing_candidates)

if __name__ == "__main__":
    mcp.run()
```

---

## Claude.ai Integration

Add to `claude_desktop_config.json` (Mac: `~/Library/Application Support/Claude/`):

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

# Test server locally
python server.py
```

```bash
# Run with MCP inspector (debug tool calls)
npx @modelcontextprotocol/inspector python server.py
```
