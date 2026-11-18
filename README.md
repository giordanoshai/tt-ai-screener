# TT AI Screener

A self-hosted MCP (Model Context Protocol) server for stock screening, position review, and trading analysis. It is designed to work with Claude.ai as the interface: users ask questions in chat, and the server provides analysis backed by a local PostgreSQL data source.

This project is intentionally read-mostly. Market data, fundamentals, news, and OHLCV are sourced from a separate data pipeline repository, `tt-data-hub`, which writes into a self-hosted Supabase/Postgres instance. This repository only reads that data and writes user-owned watchlists and positions.

## What this project does

- Long-term stock screening
- Swing trading opportunity scanning
- Position review and risk assessment
- Catalyst detection and news relevance analysis
- Watchlist and portfolio management
- Optional dashboard for local browsing

This is not a market-data collector. It does not write price history, fundamentals, or news. It only reads market data and manages local user context such as watchlists, positions, and analysis history.

---

## Architecture and data ownership

The most important rule in this project is simple:

- `tt-data-hub` owns all market data ingestion, schema creation, migrations, and table maintenance.
- `tt-ai-screener` is the analysis layer and reads from the same PostgreSQL database.
- Only three tables are written by this project:
  - `user_watchlist`
  - `user_positions`
  - `analysis_history`

```text
tt-data-hub  ──writes──►  Self-hosted Supabase/Postgres  ◄──reads──  tt-ai-screener  ──►  Claude.ai
   market data / fundamentals / news / options                         local analysis layer      chat UI
                                                                     ▲
                                                             only user-owned writes
```

Three rules matter:

1. Market data is not written here; it is loaded by `tt-data-hub`.
2. No local database file is used. A DuckDB file is legacy and not used by the active code.
3. US and CN markets share the same tables and are separated by a `market` field (`US` / `CN`). A-shares use exchange suffixes such as `600519.SH`, `000001.SZ`, and `430139.BJ`.

## Supported markets and data rules

- US market uses USD
- CN market uses CNY
- Any table that contains market data must be filtered by `market`
- USD and CNY amounts must never be added together
- Read-only database credentials are used for market data access
- Write credentials are limited to the three user-owned tables only

---

## Requirements

- Python 3.10+
- A reachable PostgreSQL database (for example, self-hosted Supabase)
- A `tt-data-hub` instance populating the schema
- Claude.ai account for MCP usage
- Optional: OpenAI-compatible AI API for analysis AI features

## Quick start

```bash
git clone https://github.com/giordanoshai/tt-ai-screener.git
cd tt-ai-screener

python -m venv .venv
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate         # Windows
pip install -r requirements.txt

cp .env.example .env            # fill in PG_READ_URL and PG_WRITE_URL
python main.py
```

After startup, the app prints:

```text
Web dashboard:  http://localhost:8766
MCP endpoint:   http://localhost:8766/mcp?token=<generated-on-first-run>
```

### Command-line modes

```bash
python main.py              # web dashboard + remote MCP on the same port
python main.py --mcp        # MCP stdio mode for Claude Desktop
python main.py --port 9000  # custom port
```

### Testing

```bash
.venv/Scripts/python.exe -m pytest tests/ -q
```

---

## Connecting to Claude.ai

### Remote MCP (recommended)

Start the service with:

```bash
python main.py
```

Then in Claude.ai:

- Settings
- Connectors
- Add custom connector
- Paste the MCP endpoint URL

The token is generated automatically on first run and stored in `.env`.

### Local Claude Desktop

Example configuration:

```json
{
  "mcpServers": {
    "trading": {
      "command": "/path/to/.venv/bin/python",
      "args": ["/path/to/tt-ai-screener/main.py", "--mcp"],
      "env": {
        "PG_READ_URL": "postgresql://readonly_user:...@host:5432/postgres",
        "PG_WRITE_URL": "postgresql://write_user:...@host:5432/postgres"
      }
    }
  }
}
```

---

## MCP tools

The server exposes 40 tools.

### Scenario tools

- `review_positions` — review current positions, P&L, stop-loss, technical setup, news relevance, and urgency ranking
- `scan_swing` — identify swing trading setups with risk/reward and target levels
- `analyze_ticker` — deep-dive technical, fundamental, and catalyst analysis for one ticker
- `detect_catalysts` — find recent company or market catalysts and whether the move already happened
- `watch_positions` — monitor holdings for stop-loss, earnings, option flow, and breaking signals
- `review_tonight_trades` — review US Eastern date (`America/New_York`) vertical spread and single-leg execution records (`spreads`/`singles` counts, compact `contract` labels) with P&L, leg fill summary, and that night's trading journal notes

### Screening and watchlist tools

- `screen_stocks` — parameterized stock screening with at least one filter
- `list_watchlist` — view current watchlist with pricing and technicals
- `add_to_watchlist` — add a ticker to the watchlist
- `remove_from_watchlist` — remove a ticker from the watchlist

### Position management tools

- `list_positions` — view positions and P&L summary grouped by currency
- `add_position` — add or update a position
- `remove_position` — delete a position

### Trade journal, psychology & market context tools (27 tools)

- **Trade review & options**: `get_recent_trades` (auto-merges vertical/calendar spread legs with compact output), `get_recent_trades_light` (schema+rows with `kind`/`contract`), `get_trade_details` (auto-pairs spread legs with compact executions), `get_strategy_templates`, `get_strategy_signals`, `get_option_ohlcv`, `get_option_market_snapshots`
- **Notes, daily logs & rules**: `get_recent_notes`, `get_daily_trade_notes`, `create_note`, `update_note`, `get_trader_daily_logs`, `create_or_update_trader_daily_log`, `get_trade_emotions`, `get_trading_rules`, `get_energy_schedules`, `get_activity_sessions`
- **Market breadth, news & diagnostics**: `get_latest_market_breadth`, `get_market_regime_history`, `get_daily_news_summary`, `search_stock_news`, `get_news_sentiment_summary`, `search_stocks_meta`, `get_stock_ohlcv`, `get_stock_fundamentals`, `get_system_logs`, `analyze_server_status`

---

## Security and privacy notes

This project is designed to keep sensitive data on your own infrastructure.

- The database is self-hosted and under your control
- Market data and news are read-only from your Postgres instance
- Only user-owned tables (`user_watchlist`, `user_positions`, `analysis_history`, `notes`, `trader_daily_log`) are written by this project
- AI provider keys are not stored in git-tracked config files
- Secrets are expected to live in `.env` and remain excluded by `.gitignore`

Important: do not expose the dashboard or MCP endpoint publicly without proper protection. The project documents a safe pattern for using a tunnel or reverse proxy and explicitly restricts the public route to the MCP path only.

## Project structure

```text
tt-ai-screener/
  main.py                  # entry point for web UI + MCP or stdio-only MCP
  server.py                # MCP server, tool registration, token auth
  config.py                # DB settings, AI defaults, port settings
  ai_client.py             # OpenAI / Anthropic-compatible clients
  config/
    models.json            # model registry only; no secrets
    skill_prompts.json      # custom skill prompts
  sql/
    001_analysis_history.sql
  db/
    pg.py                  # database access layer
  core/
    snapshot.py / scoring.py / catalyst.py / fundamental.py / regime.py /
    cn_signals.py / uw.py   # shared analysis logic
  tools/
    screening.py
    watchlist.py
    positions.py
    hourly.py
    review_positions.py / scan_swing.py / analyze_ticker.py /
    detect_catalysts.py / watch_positions.py / review_tonight_trades.py
    trade_journal.py / journal_notes.py / market_context.py
  skills/
    en/ / cn/
  web/
    app.py
    templates/
  tests/
  requirements.txt
```

---

## Privacy and leakage checklist

The repository should avoid exposing:

- real database connection strings
- API keys
- personal email addresses
- internal LAN IPs or private infrastructure hostnames
- local tunnel domains
- local workstation paths
- user account data

This public README intentionally uses example placeholders rather than personal infrastructure details.

---

## License

MIT
