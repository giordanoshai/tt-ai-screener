"""
Run once to add market/tier columns to an existing stocks_meta table.
Safe to run multiple times — uses IF NOT EXISTS / try-except.
Usage: python -m db.migrate
"""
from db.init import get_conn


def migrate():
    con = get_conn()
    for col, typedef in [("market", "VARCHAR DEFAULT 'US'"), ("tier", "VARCHAR DEFAULT 'core'")]:
        try:
            con.execute(f"ALTER TABLE stocks_meta ADD COLUMN {col} {typedef}")
            print(f"  + Added column stocks_meta.{col}")
        except Exception:
            print(f"  . Column stocks_meta.{col} already exists")
    con.close()
    print("✓ Migration complete.")


if __name__ == "__main__":
    migrate()
