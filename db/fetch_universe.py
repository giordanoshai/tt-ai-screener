"""
Fetch US stock universe from NASDAQ screener API and import into DB.
Filters by market cap and country. Outputs CSV then imports via universe.py.

Usage:
  python -m db.fetch_universe                    # default: market cap >= $20B
  python -m db.fetch_universe --min-cap 10       # market cap >= $10B
  python -m db.fetch_universe --min-cap 2        # market cap >= $2B
  python -m db.fetch_universe --csv-only         # only generate CSV, don't import
"""
import argparse
import csv
import json
import sys
from pathlib import Path

import requests

from db.universe import import_universe

NASDAQ_API = "https://api.nasdaq.com/api/screener/stocks"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}
CSV_PATH = Path(__file__).parent.parent / "universe_us.csv"


def fetch_nasdaq_screener(min_cap_billions: float = 20.0) -> list[dict]:
    print(f"[Universe] Fetching US stocks from NASDAQ (market cap >= ${min_cap_billions}B)...")

    params = {
        "tableonly": "true",
        "country": "united states",
        "limit": 5000,
        "offset": 0,
    }

    try:
        resp = requests.get(NASDAQ_API, params=params, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"ERROR: Failed to fetch from NASDAQ API: {e}")
        sys.exit(1)

    rows = data.get("data", {}).get("table", {}).get("rows", [])
    if not rows:
        rows = data.get("data", {}).get("rows", [])
    if not rows:
        print("ERROR: No data returned from NASDAQ API")
        print(f"  Response keys: {list(data.get('data', {}).keys())}")
        sys.exit(1)

    print(f"  Raw: {len(rows)} stocks returned")

    min_cap = min_cap_billions * 1_000_000_000
    filtered = []

    for row in rows:
        symbol = row.get("symbol", "").strip()
        if not symbol or "/" in symbol or "^" in symbol:
            continue

        cap_str = row.get("marketCap", "")
        if not cap_str or cap_str == "N/A" or cap_str == "":
            continue

        try:
            market_cap = float(str(cap_str).replace(",", ""))
        except (ValueError, TypeError):
            continue

        if market_cap < min_cap:
            continue

        sector = row.get("sector", "").strip()
        industry = row.get("industry", "").strip()
        name = row.get("name", "").strip()

        if sector in ("", "N/A"):
            sector = ""
        if industry in ("", "N/A"):
            industry = ""

        filtered.append({
            "ticker": symbol,
            "company_name": name,
            "sector": sector,
            "industry": industry,
            "market_cap": market_cap,
        })

    filtered.sort(key=lambda x: x["market_cap"], reverse=True)
    print(f"  Filtered: {len(filtered)} stocks with market cap >= ${min_cap_billions}B")

    return filtered


def write_csv(stocks: list[dict], path: Path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["ticker", "company_name", "sector", "industry", "market_cap"])
        writer.writeheader()
        writer.writerows(stocks)
    print(f"  CSV written: {path} ({len(stocks)} rows)")


def main():
    parser = argparse.ArgumentParser(description="Fetch US stock universe from NASDAQ")
    parser.add_argument("--min-cap", type=float, default=20.0,
                        help="Minimum market cap in billions (default: 20)")
    parser.add_argument("--csv-only", action="store_true",
                        help="Only generate CSV, don't import into DB")
    parser.add_argument("--output", type=str, default=str(CSV_PATH),
                        help=f"Output CSV path (default: {CSV_PATH})")
    args = parser.parse_args()

    stocks = fetch_nasdaq_screener(min_cap_billions=args.min_cap)

    if not stocks:
        print("No stocks found matching criteria.")
        sys.exit(1)

    output_path = Path(args.output)
    write_csv(stocks, output_path)

    sectors = {}
    for s in stocks:
        sec = s["sector"] or "(unknown)"
        sectors[sec] = sectors.get(sec, 0) + 1
    print(f"\n  Sector breakdown:")
    for sec, cnt in sorted(sectors.items(), key=lambda x: -x[1]):
        print(f"    {sec}: {cnt}")

    if not args.csv_only:
        print(f"\n[Universe] Importing into database...")
        import_universe(str(output_path), market="US")

    print(f"\n✓ Done. {len(stocks)} US stocks (>= ${args.min_cap}B market cap).")


if __name__ == "__main__":
    main()
