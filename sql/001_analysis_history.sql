-- =============================================================================
-- 分析历史表。这是 tt-trading-mcp 自己的数据，不是行情数据。
--
-- 背景：这张表原来在本地 DuckDB 里（trading.duckdb），迁到 Postgres 时漏掉了，
-- 所以「分析历史」页面一直是空的。2026-08-29 用户确认这个功能要保留。
--
-- write_user 有 public schema 的 CREATE 权限，可以自己建这张新表
-- （实测过：新建表可以，ALTER 已有表不行——那些表的属主是 postgres）。
-- 所以这个文件由采集/应用侧执行即可，不需要管理员。
-- =============================================================================

create table if not exists analysis_history (
    id            bigserial primary key,
    created_at    timestamptz not null default now(),
    skill         text        not null,
    tickers       text,
    ticker_count  integer,
    context_json  text,
    analysis_text text,
    model         text
);

-- 历史页按时间倒序翻页，这个索引够用
create index if not exists idx_analysis_history_created
    on analysis_history (created_at desc);

-- 按技能筛（长线 / 波段 / 持仓复盘）
create index if not exists idx_analysis_history_skill
    on analysis_history (skill, created_at desc);

grant select, insert, update, delete on analysis_history to write_user;
grant select on analysis_history to readonly_user;
grant usage, select on sequence analysis_history_id_seq to write_user;
