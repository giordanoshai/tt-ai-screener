"""
tt-trading-mcp setup wizard
Run this once to initialize the database and fetch all data.
Usage: python setup.py
"""

import sys
import os
from pathlib import Path


def check_env():
    from config import FINNHUB_KEY, DB_PATH, load_tickers

    print("=" * 55)
    print("  tt-trading-mcp  —  First-time Setup")
    print("=" * 55)

    errors = []

    if not FINNHUB_KEY:
        errors.append("  ✗ FINNHUB_KEY not set in .env")
    else:
        print(f"  ✓ Finnhub API key found")

    tickers = load_tickers()
    if not tickers:
        errors.append("  ✗ No tickers found in tickers.txt")
    else:
        print(f"  ✓ {len(tickers)} tickers loaded: {', '.join(tickers[:5])}{'...' if len(tickers)>5 else ''}")

    print(f"  ✓ Database path: {DB_PATH}")

    if errors:
        print("\nSetup cannot continue. Please fix:")
        for e in errors:
            print(e)
        print("\n  1. Copy .env.example to .env and fill in your FINNHUB_KEY")
        print("  2. Edit tickers.txt and add your stocks")
        sys.exit(1)

    return tickers


def estimate_time(n: int) -> str:
    secs = n * 3.5  # ~3.5s per ticker (OHLCV + fundamentals + news Finnhub calls)
    mins = int(secs / 60)
    return f"~{mins} minutes" if mins > 1 else "~1 minute"


def main():
    tickers = check_env()

    print(f"\n  Estimated time: {estimate_time(len(tickers))}")
    print("  This fetches 2 years of price data + fundamentals + 30 days news.\n")

    confirm = input("  Start setup? [y/N] ").strip().lower()
    if confirm != "y":
        print("  Cancelled.")
        sys.exit(0)

    print()

    # Step 1: Initialize DB
    print("[1/3] Initializing database...")
    from db.init import init_db
    init_db()

    # Step 2: Fetch all data
    print("[2/3] Fetching data...")
    from db.fetch import fetch_all
    from config import load_tickers
    fetch_all(load_tickers())

    # Step 3: Print Claude.ai config
    print("\n[3/3] Setup complete!\n")
    server_path = Path(__file__).parent / "server.py"
    print("─" * 55)
    print("Add this to your claude_desktop_config.json:")
    print("─" * 55)
    print(f"""
{{
  "mcpServers": {{
    "trading": {{
      "command": "python",
      "args": ["{server_path}"]
    }}
  }}
}}
""")
    print("─" * 55)
    print("Then restart Claude.ai and start talking to your data!")


if __name__ == "__main__":
    main()
