import duckdb
from config import DB_PATH


def get_conn() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(DB_PATH)


def init_db():
    con = get_conn()
    con.executemany("", [])  # warm up

    con.execute("""
        CREATE TABLE IF NOT EXISTS stocks_meta (
            ticker      VARCHAR PRIMARY KEY,
            company_name VARCHAR,
            exchange    VARCHAR,
            sector      VARCHAR,
            industry    VARCHAR
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS stock_ohlcv_daily (
            ticker       VARCHAR  NOT NULL,
            date         DATE     NOT NULL,
            open         DOUBLE,
            high         DOUBLE,
            low          DOUBLE,
            close        DOUBLE,
            volume       BIGINT,
            ma_20        DOUBLE,
            ma_50        DOUBLE,
            ma_200       DOUBLE,
            vol_ma_20    DOUBLE,
            rsi_14       DOUBLE,
            atr_14       DOUBLE,
            dist_ma20_pct DOUBLE,
            dist_ma50_pct DOUBLE,
            high_20      DOUBLE,
            high_55      DOUBLE,
            vol_ratio    DOUBLE,
            atr_pct      DOUBLE,
            pct_chg      DOUBLE,
            PRIMARY KEY (ticker, date)
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS stock_fundamentals (
            ticker               VARCHAR PRIMARY KEY,
            pe_ratio             DOUBLE,
            ps_ratio             DOUBLE,
            pb_ratio             DOUBLE,
            peg_ratio            DOUBLE,
            market_cap           DOUBLE,
            revenue_growth_yoy   DOUBLE,
            earnings_growth_yoy  DOUBLE,
            gross_margin         DOUBLE,
            roe                  DOUBLE,
            fcf_yield            DOUBLE,
            analyst_rating       VARCHAR,
            analyst_target_price DOUBLE,
            analyst_count        INTEGER,
            next_earnings_date   DATE,
            last_earnings_date   DATE,
            mspr                 DOUBLE,
            updated_at           DATE
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS news (
            id              BIGINT PRIMARY KEY,
            ticker          VARCHAR NOT NULL,
            headline        TEXT,
            summary         TEXT,
            source          VARCHAR,
            url             TEXT,
            published_at    TIMESTAMP,
            sentiment_label VARCHAR
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id              VARCHAR PRIMARY KEY,
            ticker          VARCHAR NOT NULL,
            direction       VARCHAR,
            status          VARCHAR,
            trade_type      VARCHAR,
            entry_price     DOUBLE,
            exit_price      DOUBLE,
            qty             INTEGER,
            pnl             DOUBLE,
            r_multiple      DOUBLE,
            entry_time      TIMESTAMP,
            exit_time       TIMESTAMP,
            hold_minutes    INTEGER,
            note            TEXT,
            tags            VARCHAR,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS user_watchlist (
            ticker      VARCHAR PRIMARY KEY,
            added_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("CREATE INDEX IF NOT EXISTS idx_ohlcv_ticker_date ON stock_ohlcv_daily(ticker, date)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_news_ticker ON news(ticker)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_news_published ON news(published_at)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker)")

    con.close()
    print("✓ Database initialized.")


if __name__ == "__main__":
    init_db()
