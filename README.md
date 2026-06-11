# tt-trading-mcp

A self-hostable MCP server for personal stock analysis. Connect it to Claude.ai and talk to your own stock database — no web dashboard, no frontend. **Claude is the UI.**

Three built-in skills:
- **Long-term screening** — find high-growth, high-margin stocks above the 200-day MA
- **Swing trade screening** — find breakout / pullback setups with volume confirmation
- **Position analysis** — get technicals + fundamentals + recent news for any ticker

Data sources: [yfinance](https://github.com/ranaroussi/yfinance) (free, no key) + [Finnhub](https://finnhub.io) (free key required). Local database: [DuckDB](https://duckdb.org) (no server needed).

---

## Requirements

- Python 3.10+
- A free [Finnhub API key](https://finnhub.io/register) (takes 30 seconds to register)
- Claude.ai account (Pro or above for MCP support)

---

## Installation

**Step 1 — Clone the repo**

```bash
git clone https://github.com/your-username/tt-trading-mcp.git
cd tt-trading-mcp
```

**Step 2 — Create a virtual environment**

```bash
python3 -m venv .venv
source .venv/bin/activate        # Mac / Linux
# .venv\Scripts\activate         # Windows
pip install -r requirements.txt
```

**Step 3 — Set up your config**

```bash
cp .env.example .env
```

Open `.env` and fill in your Finnhub key:

```
FINNHUB_KEY=your_key_here
```

**Step 4 — Add your tickers**

Open `tickers.txt` and add the stocks you want to track, one per line:

```
AAPL
NVDA
MSFT
TSLA
```

**Step 5 — Run setup** (fetches all data, takes 1–5 min depending on ticker count)

```bash
python setup.py
```

This will:
- Create a local DuckDB database (`trading.duckdb`)
- Download 2 years of daily price data via yfinance
- Fetch fundamentals and 30 days of news via Finnhub
- Print the config snippet you need to connect Claude.ai

---

## Web Management Panel

Start the web UI to manage your watchlist and trigger data updates:

```bash
python -m uvicorn web.app:app --host 0.0.0.0 --port 8765
```

Then open `http://localhost:8765` in your browser.

Features:
- Add / remove tickers (sector info auto-fetched from Yahoo Finance)
- Filter watchlist by sector
- See latest price, RSI, 1-day change for each ticker
- Trigger data update with one click

---

## Connect to Claude.ai

After setup, add this to your `claude_desktop_config.json`:

**Mac:** `~/Library/Application Support/Claude/claude_desktop_config.json`  
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "trading": {
      "command": "/absolute/path/to/tt-trading-mcp/.venv/bin/python",
      "args": ["/absolute/path/to/tt-trading-mcp/server.py"]
    }
  }
}
```

Restart Claude.ai. You should see a hammer icon — your trading tools are live.

---

## Daily Updates

Run this every morning before the market opens to get fresh data:

```bash
python db/update.py
```

Or set up a cron job (Mac / Linux):

```bash
# Run every weekday at 9:00 AM
0 9 * * 1-5 cd /path/to/tt-trading-mcp && .venv/bin/python db/update.py
```

---

## Usage Examples

Once connected to Claude.ai, just ask naturally:

> "Screen for long-term candidates today"

> "Any swing trade setups? Show me breakouts only"

> "Analyze my NVDA position — what does the data say?"

> "Compare AAPL and MSFT fundamentals"

> "What's the recent news sentiment on TSLA?"

---

## Add More Tickers

1. Add tickers to `tickers.txt`
2. Run `python db/update.py` — it will automatically fetch historical data for new tickers

---

## Project Structure

```
tt-trading-mcp/
  setup.py          # First-time setup wizard
  server.py         # MCP server entry point
  config.py         # Configuration
  tickers.txt       # Your stock watchlist
  .env              # API keys (never commit this)
  .env.example      # Template
  requirements.txt
  db/
    init.py         # DuckDB schema
    fetch.py        # Full historical fetch (used by setup)
    update.py       # Daily incremental update
  tools/
    screening.py    # Long-term + swing screening
    trades.py       # Position analysis + trade history
```

---

## Data Sources

| Data | Source | Cost |
|---|---|---|
| OHLCV (2 years daily) | yfinance | Free |
| Technical indicators | pandas-ta | Free |
| Fundamentals (PE, margins, growth) | Finnhub | Free tier |
| News (30 days) | Finnhub | Free tier |

Finnhub free tier limit: 60 API calls/minute. Setup for 100 tickers takes ~6 minutes.

---

## Privacy

All data is stored locally in `trading.duckdb`. Nothing is sent to any external server except the data fetch requests to yfinance and Finnhub. Your trade records never leave your machine.

---

## License

MIT
