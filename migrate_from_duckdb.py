"""把只存在于本地 DuckDB 里的三样东西迁到新家。一次性脚本，跑完就可以删旧库。

  user_positions   6 笔真实持仓 -> Postgres（线上这张表是空的）
  analysis_history 3 条分析记录 -> Postgres（表刚建好）
  ai_models        9 个模型配置 -> 本地 JSON（不含密钥，密钥走环境变量）
"""
import json
import duckdb
from db import pg

d = duckdb.connect("trading.duckdb", read_only=True)

print("=== 迁移前的线上状态 ===")
before_pos = pg.query_one("select count(*) as n from user_positions")["n"]
before_hist = pg.query_one("select count(*) as n from analysis_history")["n"]
print("   user_positions   %d 行" % before_pos)
print("   analysis_history %d 行" % before_hist)
print()

# ── 1. 持仓 ────────────────────────────────────────────────────────────
rows = d.execute("select ticker, avg_cost, shares from user_positions order by ticker").fetchall()
print("=== 迁持仓（%d 笔）===" % len(rows))
moved = 0
for ticker, avg_cost, shares in rows:
    # 全是美股（代码里没有 .SH/.SZ/.BJ 后缀），币种 USD
    n = pg.execute_write(
        "insert into user_positions (ticker, market, avg_cost, shares, currency, note) "
        "values (%s, %s, %s, %s, %s, %s) "
        "on conflict (ticker) do nothing",
        (ticker, "US", float(avg_cost), float(shares), "USD",
         "从本地 DuckDB 迁入 2026-08-29"))
    moved += n
    print("   %-6s 成本 %-8s 股数 %-5s -> %s" % (ticker, avg_cost, shares, "已插入" if n else "已存在，跳过"))
print()

# ── 2. 分析历史 ────────────────────────────────────────────────────────
rows = d.execute("""select created_at, skill, tickers, ticker_count,
                           context_json, analysis_text, model
                    from analysis_history order by id""").fetchall()
print("=== 迁分析历史（%d 条）===" % len(rows))
for r in rows:
    pg.execute_write(
        "insert into analysis_history "
        "(created_at, skill, tickers, ticker_count, context_json, analysis_text, model) "
        "values (%s, %s, %s, %s, %s, %s, %s)", tuple(r))
    print("   %s %-16s %d 只票 %s" % (r[0], r[1], r[3] or 0, r[6]))
print()

# ── 3. 模型配置 -> JSON ────────────────────────────────────────────────
cur = d.execute("""select id, display_name, api_base, model_id, api_format, role,
                          supports_thinking, is_default_sentiment, is_default_analysis, enabled
                   from ai_models order by display_name""")
names = [c[0] for c in cur.description]
models = [dict(zip(names, row)) for row in cur.fetchall()]
for m in models:
    for k in ("supports_thinking", "is_default_sentiment", "is_default_analysis", "enabled"):
        m[k] = bool(m[k])
    # 密钥不进 JSON，按接口地址指到对应的环境变量
    base = (m["api_base"] or "").lower()
    m["api_key_env"] = ("ANTHROPIC_API_KEY" if "anthropic" in base
                        else "DASHSCOPE_API_KEY" if "dashscope" in base
                        else "OPENAI_API_KEY" if "openai" in base
                        else "AI_API_KEY")
payload = {
    "_说明": ("AI 模型配置。原来存在本地 DuckDB 的 ai_models 表里，2026-08-29 "
              "迁到这个文件。密钥【不在这里】——每个模型用 api_key_env 指向一个"
              "环境变量名，实际的值放 .env。改模型直接改这个文件，不用重启以外的操作。"),
    "models": models,
}
with open("config/models.json", "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)
print("=== 模型配置已导出到 config/models.json（%d 个，不含密钥）===" % len(models))
for m in models:
    print("   %-20s %-24s role=%-10s %s" %
          (m["display_name"], m["model_id"], m["role"],
           "默认分析" if m["is_default_analysis"] else
           ("默认情绪" if m["is_default_sentiment"] else "")))
print()

d.close()
print("=== 迁移后的线上状态 ===")
after_pos = pg.query_one("select count(*) as n from user_positions")["n"]
after_hist = pg.query_one("select count(*) as n from analysis_history")["n"]
print("   user_positions   %d -> %d 行" % (before_pos, after_pos))
print("   analysis_history %d -> %d 行" % (before_hist, after_hist))
