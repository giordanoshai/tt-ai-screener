"""
Import a ticker universe from CSV into stocks_meta with tier='universe'.
CSV must have at least a 'ticker' column. Optional: 'sector', 'industry'.

Usage:
  python -m db.universe path/to/tickers.csv
  python -m db.universe path/to/tickers.csv --market US
"""
import argparse
import csv
import sys
from datetime import datetime

from db.init import get_conn


def import_universe(csv_path: str, market: str = "US"):
    con = get_conn()
    count = 0

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if "ticker" not in reader.fieldnames:
            print(f"ERROR: CSV must have a 'ticker' column. Found: {reader.fieldnames}")
            sys.exit(1)

        for row in reader:
            ticker = row["ticker"].strip().upper()
            if not ticker:
                continue
            sector = row.get("sector", "").strip()
            industry = row.get("industry", "").strip()

            con.execute("""
                INSERT INTO stocks_meta (ticker, sector, industry, market, tier)
                VALUES (?, ?, ?, ?, 'universe')
                ON CONFLICT (ticker) DO UPDATE SET
                    market = EXCLUDED.market,
                    tier = CASE
                        WHEN stocks_meta.ticker IN (SELECT ticker FROM user_watchlist)
                        THEN 'core'
                        ELSE 'universe'
                    END,
                    sector = CASE WHEN EXCLUDED.sector != '' THEN EXCLUDED.sector ELSE stocks_meta.sector END,
                    industry = CASE WHEN EXCLUDED.industry != '' THEN EXCLUDED.industry ELSE stocks_meta.industry END
            """, [ticker, sector, industry, market])
            count += 1

    con.close()
    print(f"✓ Imported {count} tickers as '{market}' universe.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import ticker universe from CSV")
    parser.add_argument("csv_path", help="Path to CSV file with 'ticker' column")
    parser.add_argument("--market", default="US", help="Market code (default: US)")
    args = parser.parse_args()
    import_universe(args.csv_path, args.market)
