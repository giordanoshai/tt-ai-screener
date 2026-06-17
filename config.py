import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent

DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "trading.duckdb"))
FINNHUB_KEY = os.getenv("FINNHUB_KEY", "")
NEWS_DAYS = int(os.getenv("NEWS_DAYS", "30"))
FUNDAMENTALS_STALE_DAYS = int(os.getenv("FUNDAMENTALS_STALE_DAYS", "7"))
NEWS_MOVER_PCT_THRESHOLD = float(os.getenv("NEWS_MOVER_PCT_THRESHOLD", "5.0"))
NEWS_MOVER_VOLRATIO_THRESHOLD = float(os.getenv("NEWS_MOVER_VOLRATIO_THRESHOLD", "2.0"))
OHLCV_BATCH_SIZE = int(os.getenv("OHLCV_BATCH_SIZE", "200"))
TICKERS_FILE = BASE_DIR / "tickers.txt"


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
