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
            industry    VARCHAR,
            market      VARCHAR DEFAULT 'US',
            tier        VARCHAR DEFAULT 'core'
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
            sentiment_label VARCHAR,
            sentiment_score DOUBLE
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

    con.execute("""
        CREATE TABLE IF NOT EXISTS user_positions (
            ticker       VARCHAR PRIMARY KEY,
            avg_cost     DOUBLE NOT NULL,
            shares       INTEGER NOT NULL,
            added_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS ai_models (
            id                    VARCHAR PRIMARY KEY,
            display_name          VARCHAR NOT NULL,
            api_base              VARCHAR NOT NULL,
            api_key               VARCHAR,
            model_id              VARCHAR NOT NULL,
            api_format            VARCHAR DEFAULT 'openai',
            role                  VARCHAR DEFAULT 'both',
            supports_thinking     BOOLEAN DEFAULT FALSE,
            is_default_sentiment  BOOLEAN DEFAULT FALSE,
            is_default_analysis   BOOLEAN DEFAULT FALSE,
            enabled               BOOLEAN DEFAULT TRUE
        )
    """)

    con.execute("""
        CREATE SEQUENCE IF NOT EXISTS seq_analysis_id START 1
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS analysis_history (
            id            INTEGER DEFAULT nextval('seq_analysis_id') PRIMARY KEY,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            skill         VARCHAR NOT NULL,
            tickers       VARCHAR,
            ticker_count  INTEGER,
            context_json  TEXT,
            analysis_text TEXT,
            model         VARCHAR
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key   VARCHAR PRIMARY KEY,
            value VARCHAR NOT NULL
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS skill_prompts (
            skill      VARCHAR NOT NULL,
            lang       VARCHAR NOT NULL,
            prompt     TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (skill, lang)
        )
    """)

    con.execute("""
        CREATE SEQUENCE IF NOT EXISTS seq_update_id START 1
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS update_history (
            id          INTEGER DEFAULT nextval('seq_update_id') PRIMARY KEY,
            started_at  TIMESTAMP,
            finished_at TIMESTAMP,
            status      VARCHAR,
            duration_s  INTEGER,
            summary     TEXT,
            log_text    TEXT
        )
    """)

    con.execute("CREATE INDEX IF NOT EXISTS idx_ohlcv_ticker_date ON stock_ohlcv_daily(ticker, date)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_news_ticker ON news(ticker)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_news_published ON news(published_at)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker)")

    # migrations for existing databases
    try:
        con.execute("ALTER TABLE news ADD COLUMN sentiment_score DOUBLE")
    except Exception:
        pass
    try:
        con.execute("ALTER TABLE ai_models ADD COLUMN api_format VARCHAR DEFAULT 'openai'")
    except Exception:
        pass

    # Seed default models from .env (only if table is empty)
    if con.execute("SELECT COUNT(*) FROM ai_models").fetchone()[0] == 0:
        from config import (
            SENTIMENT_API_BASE, SENTIMENT_API_KEY, SENTIMENT_MODEL,
            AI_API_BASE, AI_API_KEY, AI_MODEL,
        )
        defaults = []
        if SENTIMENT_API_BASE and SENTIMENT_MODEL:
            defaults.append((
                "env-sentiment", SENTIMENT_MODEL, SENTIMENT_API_BASE, SENTIMENT_API_KEY,
                SENTIMENT_MODEL, "openai", "sentiment", False, True, False, True,
            ))
        if AI_API_BASE and AI_MODEL:
            thinking = "max" in AI_MODEL.lower() or "think" in AI_MODEL.lower()
            defaults.append((
                "env-analysis", AI_MODEL, AI_API_BASE, AI_API_KEY,
                AI_MODEL, "openai", "analysis", thinking, False, True, True,
            ))
        for d in defaults:
            con.execute("""
                INSERT INTO ai_models (id, display_name, api_base, api_key, model_id, api_format, role, supports_thinking, is_default_sentiment, is_default_analysis, enabled)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (id) DO NOTHING
            """, list(d))

    con.close()
    print("✓ Database initialized.")


if __name__ == "__main__":
    init_db()
