# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**tt-trading-mcp** is a self-hostable MCP (Model Context Protocol) server for personal stock trading analysis. It connects to Claude.ai for long-term investing, swing trading, position tracking, and trading psychology journaling.

The design philosophy: **Claude is the UI**. Users interact primarily through Claude.ai conversations backed by this MCP server (a small local web dashboard exists for manual browsing).

**This project is a read-only consumer.** All data collection — daily/hourly OHLCV, fundamentals, news, sentiment scoring, table creation and migrations — lives in a separate repo, **`tt-data-hub`**, which runs on the server against the same self-hosted Supabase/Postgres instance. Nothing here writes market data; the only writes are the user's own watchlist and positions. See "Data Sources" below.

Two markets are in the same tables: **`US`** (US stocks, USD) and **`CN`** (A-shares, CNY, tickers carry an exchange suffix like `600519.SH` / `000001.SZ` / `430139.BJ`). Every query against a market-data table must carry a `market` condition, and USD/CNY amounts must never be summed together.

---

## MCP Tools Reference

This is the canonical list of tools this server exposes (10 tools). Each tool maps to a function in `tools/`.

### Screening
| Tool | Params | Description |
|---|---|---|
| `screen_stocks` | `screen_type='longterm'`, `market='US'`, `sector`, `min_market_cap`, `max_pe`, `min_growth`, `min_margin`, `min_rsi`, `max_rsi`, `above_ma`, `limit=20` | Parameterized stock screener. **At least one filter required** (no filter = rejected). `screen_type`: `longterm`/`swing` determines scoring. `market`: `US`/`CN`. Filters: sector name, market cap (billions), PE cap, growth % floor, margin % floor, RSI range, MA price floor (20/50/200). Result count capped at 30 |

### Watchlist
| Tool | Params | Description |
|---|---|---|
| `list_watchlist` | — | List all watchlist tickers with latest price, daily change, and technicals |
| `add_to_watchlist` | `ticker`(required) | Add ticker to watchlist (rejected if the ticker is not yet in `stocks_meta`) |
| `remove_from_watchlist` | `ticker`(required) | Remove ticker from watchlist |
| `analyze_watchlist` | `analysis_type='longterm'`/`'swing'` | Run longterm or swing analysis on all watchlist tickers via skills engine |

### Positions
| Tool | Params | Description |
|---|---|---|
| `list_positions` | — | List all positions with current price, P&L (% and $). Summary is **per currency** (`{"USD": {...}, "CNY": {...}}`) — no cross-currency total |
| `add_position` | `ticker`(required), `avg_cost`(required), `shares`(required), `market='US'` | Add or update a position. `market` (`US`/`CN`) decides the currency. Primary key is `ticker` alone, so the same ticker cannot be held in two markets |
| `remove_position` | `ticker`(required) | Remove a position |
| `analyze_positions` | — | Comprehensive analysis of all positions: technicals, fundamentals, news, P&L via skills engine |

### Intraday
| Tool | Params | Description |
|---|---|---|
| `get_hourly_setup` | `ticker`(required), `bars=60`, `market` | Hourly-bar entry/exit read for one ticker: range position, 20h/50h MAs and slope, RSI, volume ratio, ATR (for stop placement), nearest swing support/resistance, plus a plain-language summary. Daily bars set direction, hourly bars set the price levels. `market` is normally inferred from the ticker suffix. Hourly bars only exist for the active pool (positions + watchlist + screener hits) |

---

## Architecture

```
tt-trading-mcp/
  main.py                # Unified entrypoint (web + MCP, or MCP stdio)
  server.py              # MCP server — registers all 10 tools, token auth
  config.py              # Postgres URLs, fallback AI env vars, MCP port/token
  ai_client.py           # OpenAI/Anthropic-compatible AI client; reads config/models.json
  config/
    models.json          # AI model list (NO keys — each entry names an env var)
    skill_prompts.json   # Custom skill prompts; absent until the UI saves one
  sql/
    001_analysis_history.sql   # The one table this repo owns
  db/
    pg.py                # THE ONLY database gateway (read + write)
  tools/
    screening.py         # screen_stocks (parameterized screener, filter required)
    watchlist.py         # list/add/remove watchlist, analyze_watchlist
    positions.py         # list/add/remove positions, analyze_positions
    hourly.py            # get_hourly_setup (hourly-bar entry/exit levels)
  skills/
    __init__.py          # Analysis data layer — builds context for the AI models
    en/, cn/             # Skill prompts per language:
                         #   longterm_screen.md / swing_screen.md / position_review.md
  web/
    app.py               # FastAPI web dashboard
    templates/           # home / index / screener / analysis / history / settings
  tests/
  requirements.txt
```

**Key design rule:** All filtering and aggregation happens inside the tool function (database or Python side). Tools return lightweight summaries to Claude — never raw full tables. This keeps token usage low.

### `db/pg.py` — the only DB gateway

Reads and writes use **separate database accounts**, physically isolated:

- Reads go through `readonly_user`, and the session is additionally set to a read-only transaction, so even a buggy write statement is rejected by the database itself.
- Writes go through `write_user` and are only for `user_watchlist` / `user_positions`.
- Postgres `numeric` is converted to `float` at this layer (a `Decimal` would blow up `json.dumps`).

API: `pg.query(sql, params) -> list[dict]`, `pg.query_one(...) -> dict | None`, `pg.execute_write(...) -> rowcount`, `pg.fetch_write(...)` for `RETURNING`.

**Four rules every query must follow** (each one has already broken this codebase once):

1. **Placeholders are `%s`, not `?`.** psycopg2, not DuckDB. A leftover `?` raises `syntax error at or near "?"`.
2. **Always filter on `market`.** `stock_ohlcv_daily` holds ~2.75M US rows and ~1.05M CN rows in one table; a missing `market` condition silently mixes A-shares into US results.
3. **Never take the latest date with a correlated subquery.** `o.date = (select max(date) from stock_ohlcv_daily where ticker = o.ticker)` measured as a 2.75M-row sequential scan (~6.7s). Use one of two shapes: for a handful of tickers, a per-ticker `order by date desc limit 1` index scan (also keeps halted/delisted names); for a full-pool scan, fetch the market's latest trading day in a separate small query first, then compare for equality (~109ms).
4. **`stock_fundamentals` has many rows per ticker** (PK is `(ticker, updated_at)`; ~2,135 tickers spread over ~29,943 rows). Always dedupe with `distinct on (ticker) ... order by ticker, updated_at desc`.

---

## Data Sources

Market data comes from Postgres; configuration comes from files in the repo and from
`.env`. Nothing comes from a local database file any more — **there is no DuckDB**.
`trading.duckdb` is a dead leftover; no code reads it, and no code should.

| What | Where it lives | Who writes it |
|---|---|---|
| Prices/OHLCV (daily + intraday), fundamentals, news + sentiment, Unusual Whales options data | Self-hosted Supabase/Postgres | **`tt-data-hub` only.** This repo reads it through `readonly_user` |
| Watchlist, positions | Same Postgres (`user_watchlist`, `user_positions`) | **This repo**, through `write_user` |
| Analysis history | Same Postgres (`analysis_history`) | **This repo**, through `write_user` |
| AI model configuration | `config/models.json` (a file, not a table) | Edited by hand |
| API keys for those models | `.env` | Edited by hand |
| Custom skill prompts | `config/skill_prompts.json` (a file, not a table) | Written by the Settings page |
| Default skill prompts | `skills/<lang>/<skill>.md` | Edited by hand |
| Output language | `LANG_UI` env var — fixed, not switchable at runtime | Edited by hand |

### Market data: read-only, collected elsewhere

**Collection lives in `tt-data-hub`, not here.** That repo owns the ingest jobs, the
schema, the migrations, and the system cron on the server. Do not add fetching, table
creation, or migrations to this repo. There is no `/update` endpoint and no update
button — those were removed, not stubbed, because a button that only apologises is
worse than no button. To see how the last ingest run went, read `ingest_runlog`
(surfaced on the home page and at `GET /api/update/last`).

### Writes from this repo: exactly three tables

`user_watchlist`, `user_positions`, and `analysis_history`. Everything else goes
through the read-only account, whose session is additionally set to a read-only
transaction. `analysis_history` is the newest of the three: every AI analysis run
from the web UI (both `POST /analyze` and the streaming `POST /analyze/stream`) is
saved there, and the History page reads it back. **A failed history write must never
fail the analysis** — the answer is already computed and on its way to the user, so a
failure there is logged and swallowed.

### AI models: `config/models.json`

The model list used to be an `ai_models` table in the old DuckDB. It was missed
during the migration and does not exist in Postgres, which is why AI analysis
silently reported "no model configured" for a while. It now lives in
`config/models.json`:

```jsonc
{
  "models": [
    {
      "id": "qwen-max",                  // stable key; what /analyze's `model` param takes
      "display_name": "Qwen3.7 Max",
      "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
      "model_id": "qwen3.7-max",         // what goes in the API request body
      "api_format": "openai",            // openai | anthropic
      "role": "analysis",                // analysis | sentiment | both
      "supports_thinking": true,
      "is_default_analysis": true,       // picked when no id is given
      "is_default_sentiment": false,
      "enabled": true,                   // false = hidden everywhere
      "api_key_env": "DASHSCOPE_API_KEY" // NAME of an env var, never the key itself
    }
  ]
}
```

**No secret ever goes in this file.** `api_key_env` names an environment variable
(`ANTHROPIC_API_KEY`, `DASHSCOPE_API_KEY`, `OPENAI_API_KEY`, `AI_API_KEY`, …) and
`ai_client.get_model()` resolves it with `os.getenv` at call time. The file is read
fresh on every call, so an edit takes effect without a restart.

The Settings page shows this list **read-only** — adding, removing, or changing the
default model means editing the file. The old `POST /models` and `DELETE /models/{id}`
endpoints are gone.

If `config/models.json` is missing, unreadable, or has no models, `ai_client` falls
back to the single-model-per-role env vars (`AI_API_BASE`/`AI_API_KEY`/`AI_MODEL` and
`SENTIMENT_API_BASE`/`SENTIMENT_API_KEY`/`SENTIMENT_MODEL`).

### Skill prompts: `skills/<lang>/*.md`, overridable in `config/skill_prompts.json`

`load_skill_prompt(skill, lang)` returns the custom prompt if
`config/skill_prompts.json` has one, otherwise the bundled markdown file. The JSON is
`{"<skill>": {"<lang>": "<prompt>"}}`; a missing file, a broken file, or a blank entry
all fall back to the markdown without erroring. The Settings page writes it (Save) and
removes entries from it (Reset to Default). The `skill_prompts` table it replaced does
not exist in Postgres.

### Language: fixed

`LANG_UI` (`en` / `cn`, default `en`) decides the analysis output language and which
`skills/<lang>/` directory is used. **Runtime language switching was removed** — the
`app_settings` table it was stored in does not exist in Postgres, and the feature is
not wanted. There is no `/settings/lang` endpoint and no switch in the UI.

### `.env`

```bash
PG_READ_URL=...            # Postgres, readonly_user  (all market data)
PG_WRITE_URL=...           # Postgres, write_user     (watchlist + positions + history)
WATCHLIST_USER_ID=...      # uuid; user_watchlist.user_id is NOT NULL
ANTHROPIC_API_KEY=...      # AI keys, referenced by name from config/models.json
DASHSCOPE_API_KEY=...
OPENAI_API_KEY=...
AI_API_BASE=...            # fallback single-model config, only used if models.json is empty
SENTIMENT_API_BASE=...     # same, for sentiment scoring
LANG_UI=en                 # en | cn, output language (fixed, not switchable in the UI)
UW_API_TOKEN=...           # Unusual Whales (options flow), optional
MCP_PORT=8766
MCP_TOKEN=...              # auto-generated on first run
```

**Core database tables** (self-hosted Postgres; market-data schema owned by `tt-data-hub`):

| Table | Notes |
|---|---|
| `stocks_meta` | Ticker metadata: `company_name`, `sector`, `industry`, `exchange`, **`market`**, `currency`. `sector` is populated for roughly half the universe |
| `stock_ohlcv_daily` | Daily OHLCV + RSI14 + `ma_20/50/200` + `dist_ma20_pct`/`dist_ma50_pct` + `high_20` + `vol_ratio` + `atr_pct`. Keyed `(ticker, date)`, carries **`market`** |
| `stock_ohlcv_min` | Intraday bars, **hourly and minute bars share this table** — split by the `timeframe` column (`'1h'` / `'1m'`). Minute data is ~20M rows / 9.3GB, so a query missing `timeframe = '1h'` is a disaster. `ts` is the bar's **start** time, stored as `timestamptz` (convert to `America/New_York` or `Asia/Shanghai` for display). US bars carry a `session` of `RTH`/`ETH` |
| `v_ohlcv_hourly` | Convenience view over the `'1h'` rows. `tools/hourly.py` deliberately queries the base table instead — the view hides `session` and `timeframe`, and Postgres inlines it anyway (identical plan, cost 6.33) |
| `stock_fundamentals` | Valuation, growth, margins, analyst ratings, earnings dates, insider MSPR, short interest. **Many rows per ticker** (see rule 4 above). Currently **US only** — CN tickers legitimately come back with empty fundamentals |
| `news` | Articles with AI `sentiment_label` + `sentiment_score`. **No `ticker` column** — tickers live in `related_tickers` (`text[]`, GIN-indexed, so match with `&&`). **No `headline` column** — the title is in `title` |
| `uw_gex_daily` | Options gamma walls (call/put wall, gamma flip/magnet). Covers only ~40 large caps |
| `user_watchlist` | User's tracked tickers. Time column is **`created_at`** (not `added_at`); `user_id` is a NOT NULL uuid; unique on `(ticker)`. **Written by this repo** |
| `user_positions` | `ticker`, `market`, `avg_cost`, `shares`, `currency`, `updated_at`. PK is `ticker` alone. **Written by this repo** |
| `analysis_history` | `id`, `created_at`, `skill`, `tickers` (comma-joined), `ticker_count`, `context_json`, `analysis_text`, `model`. Owned and **written by this repo** — see `sql/001_analysis_history.sql` |

Three tables that the old DuckDB had and Postgres deliberately does **not**:
`ai_models`, `skill_prompts`, `app_settings`. They are now `config/models.json`,
`config/skill_prompts.json`, and (for language) a plain env var. Do not re-create them.

---

## MCP Protocol

Built with the [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk). Tools use `@mcp.tool()` decorators. Transport: `stdio` (default for Claude.ai desktop/CLI).

```python
# server.py pattern
from mcp.server.fastmcp import FastMCP
from tools.screening import screen_stocks
from tools.watchlist import list_watchlist, add_to_watchlist, remove_from_watchlist, analyze_watchlist
from tools.positions import list_positions, add_position, remove_position, analyze_positions
from tools.hourly import get_hourly_setup

mcp = FastMCP("tt-ai-screener")
mcp.tool()(screen_stocks)
mcp.tool()(list_watchlist)
# ... register all 10 tools

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
      "args": ["/path/to/tt-trading-mcp/main.py", "--mcp"],
      "env": {
        "PG_READ_URL": "postgresql://readonly_user:...@host:5432/postgres",
        "PG_WRITE_URL": "postgresql://write_user:...@host:5432/postgres"
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
python main.py --port 9000  # Override the port

# Tests (reads hit the real Postgres through the read-only account)
.venv/Scripts/python.exe -m pytest tests/ -q

# MCP inspector (debug tool calls)
npx @modelcontextprotocol/inspector python server.py
```

There is no `--update` and no `--cron`: data collection and its schedule belong to
`tt-data-hub` on the server. Anything that needs new data is fixed there, not here.
