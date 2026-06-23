import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent

DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "trading.duckdb"))
FINNHUB_KEY = os.getenv("FINNHUB_KEY", "")
FINNHUB_TIER = os.getenv("FINNHUB_TIER", "free")  # free | premium
NEWS_DAYS = int(os.getenv("NEWS_DAYS", "30"))
FUNDAMENTALS_STALE_DAYS = int(os.getenv("FUNDAMENTALS_STALE_DAYS", "7"))
NEWS_MOVER_PCT_THRESHOLD = float(os.getenv("NEWS_MOVER_PCT_THRESHOLD", "5.0"))
NEWS_MOVER_VOLRATIO_THRESHOLD = float(os.getenv("NEWS_MOVER_VOLRATIO_THRESHOLD", "2.0"))
OHLCV_BATCH_SIZE = int(os.getenv("OHLCV_BATCH_SIZE", "200"))
TICKERS_FILE = BASE_DIR / "tickers.txt"

# AI: news sentiment analysis (small/cheap model, runs during data update)
SENTIMENT_API_BASE = os.getenv("SENTIMENT_API_BASE", "")
SENTIMENT_API_KEY = os.getenv("SENTIMENT_API_KEY", "")
SENTIMENT_MODEL = os.getenv("SENTIMENT_MODEL", "")

# AI: local analysis engine (skills analysis via API, more capable model)
AI_API_BASE = os.getenv("AI_API_BASE", "")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "")

# MCP remote server
MCP_PORT = int(os.getenv("MCP_PORT", "8766"))
MCP_TOKEN = os.getenv("MCP_TOKEN", "")


def load_tickers() -> list[str]:
    """Load tickers from DB watchlist. Falls back to tickers.txt if DB not ready."""
    try:
        import duckdb
        con = duckdb.connect(DB_PATH)
        rows = con.execute("SELECT ticker FROM user_watchlist ORDER BY ticker").fetchall()
        con.close()
        if rows:
            return [r[0] for r in rows]
    except Exception:
        pass

    # Fallback: read from tickers.txt (used during first-time setup before DB exists)
    if TICKERS_FILE.exists():
        tickers = []
        for line in TICKERS_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                tickers.append(line.upper())
        return tickers

    return []
