# -*- coding: utf-8 -*-
"""交易笔记、心理日志、情绪与风控规则工具（由 TTAiTradingSystem 迁入）。

只读查询走 `pg.query`（只读账号），笔记与每日日志的写入走 `pg.fetch_write`（写账号）。
"""
from datetime import datetime, timezone

from db import pg
from tools._common import default_user_id, dump_json

_NOTE_TYPE_COLUMNS = {
    "reminder": "is_reminder",
    "marketing": "is_on_marketing",
    "todo": "daily_todo",
    "rule": "is_trade_rule",
    "daily_trade": "daily_trade_note",
}
_VALID_SOURCES = ("web", "joi")


def _note_flags(note_type: str) -> dict[str, bool]:
    return {col: (key == note_type) for key, col in _NOTE_TYPE_COLUMNS.items()}


def get_recent_notes(
    limit: int = 15,
    note_type: str | None = None,
    source: str | None = None,
    keyword: str | None = None,
) -> str:
    """获取最近的笔记和提醒 (notes)。

    参数:
    - limit: 返回数量限制，默认 15
    - note_type: 'reminder' / 'marketing' / 'todo' / 'rule' / 'daily_trade'
    - source: 'web' 或 'joi'
    - keyword: 关键字模糊搜索
    """
    try:
        clauses: list[str] = []
        params: list = []
        if source:
            clauses.append("source = %s")
            params.append(source.strip())
        if note_type:
            col = _NOTE_TYPE_COLUMNS.get(note_type)
            if not col:
                return f"错误: note_type 必须是以下之一: {list(_NOTE_TYPE_COLUMNS)}"
            clauses.append(f"{col} = true")
        if keyword:
            clauses.append("content ilike %s")
            params.append(f"%{keyword}%")

        where_sql = f"where {' and '.join(clauses)}" if clauses else ""
        sql = f"""
            select *
            from notes
            {where_sql}
            order by created_at desc
            limit %s
        """
        return dump_json(pg.query(sql, tuple(params) + (int(limit),)))
    except Exception as exc:
        return f"获取笔记失败: {exc}"


def get_daily_trade_notes(
    trade_date: str | None = None,
    limit: int = 10,
) -> str:
    """获取交易日志 / 每日交易笔记 (daily_trade_note=true)。

    若 `trade_date` 为空则回退取 `created_at` 的日期部分作为 `effective_trade_date`。
    """
    try:
        if trade_date:
            sql = """
                select *
                from notes
                where daily_trade_note = true
                  and coalesce(trade_date, (created_at at time zone 'UTC')::date) = %s::date
                order by created_at desc
            """
            notes = pg.query(sql, (trade_date.strip(),))
        else:
            sql = """
                select *
                from notes
                where daily_trade_note = true
                order by created_at desc
                limit %s
            """
            notes = pg.query(sql, (int(limit),))

        for note in notes:
            eff = note.get("trade_date")
            if eff is not None:
                note["effective_trade_date"] = str(eff)[:10]
            elif note.get("created_at") is not None:
                note["effective_trade_date"] = str(note["created_at"])[:10]
            else:
                note["effective_trade_date"] = None
        return dump_json(notes)
    except Exception as exc:
        return f"获取交易日志失败: {exc}"


def create_note(
    content: str,
    note_type: str,
    trade_date: str | None = None,
    source: str = "web",
) -> str:
    """创建一条新笔记或提醒 (notes)。"""
    try:
        if not content or not content.strip():
            return "错误: 笔记内容不能为空"
        if source not in _VALID_SOURCES:
            return "错误: source 必须是 'web' 或 'joi'"
        if note_type not in _NOTE_TYPE_COLUMNS:
            return f"错误: note_type 必须是以下之一: {list(_NOTE_TYPE_COLUMNS)}"

        flags = _note_flags(note_type)
        sql = """
            insert into notes (
                user_id, content, source,
                is_reminder, is_on_marketing, daily_todo, is_trade_rule, daily_trade_note,
                trade_date
            )
            values (%s::uuid, %s, %s, %s, %s, %s, %s, %s, %s::date)
            returning id
        """
        rows = pg.fetch_write(
            sql,
            (
                default_user_id(),
                content,
                source,
                flags["is_reminder"],
                flags["is_on_marketing"],
                flags["daily_todo"],
                flags["is_trade_rule"],
                flags["daily_trade_note"],
                trade_date.strip() if trade_date else None,
            ),
        )
        if rows:
            return f"成功创建笔记，ID: {rows[0]['id']}"
        return "创建笔记失败: 未返回数据"
    except Exception as exc:
        return f"创建笔记失败: {exc}"


def update_note(
    note_id: str,
    content: str | None = None,
    note_type: str | None = None,
    trade_date: str | None = None,
    source: str | None = None,
) -> str:
    """修改一条已有笔记或提醒 (notes)。传 `trade_date="null"` 可清空日期。"""
    try:
        sets: list[str] = []
        params: list = []

        if content is not None:
            sets.append("content = %s")
            params.append(content)
        if source is not None:
            if source not in _VALID_SOURCES:
                return "错误: source 必须是 'web' 或 'joi'"
            sets.append("source = %s")
            params.append(source)
        if note_type is not None:
            if note_type not in _NOTE_TYPE_COLUMNS:
                return f"错误: note_type 必须是以下之一: {list(_NOTE_TYPE_COLUMNS)}"
            for col, val in _note_flags(note_type).items():
                sets.append(f"{col} = %s")
                params.append(val)
        if trade_date is not None:
            if trade_date == "null":
                sets.append("trade_date = null")
            else:
                sets.append("trade_date = %s::date")
                params.append(trade_date.strip())

        if not sets:
            return "错误: 没有指定任何要修改的字段"

        sets.append("updated_at = %s")
        params.append(datetime.now(timezone.utc))
        params.append(note_id.strip())

        sql = f"""
            update notes
            set {', '.join(sets)}
            where id = %s::uuid
            returning id
        """
        rows = pg.fetch_write(sql, tuple(params))
        if rows:
            return f"成功更新笔记: {note_id}"
        return f"更新失败: 未找到 ID 为 {note_id} 的笔记"
    except Exception as exc:
        return f"更新笔记失败: {exc}"


def get_trader_daily_logs(limit: int = 10, start_date: str | None = None) -> str:
    """获取交易员每日心理与身体状态日志 (trader_daily_log)。"""
    try:
        clauses = ["log_date >= %s::date"] if start_date else []
        params: list = [start_date.strip()] if start_date else []
        where_sql = f"where {' and '.join(clauses)}" if clauses else ""
        sql = f"""
            select *
            from trader_daily_log
            {where_sql}
            order by log_date desc
            limit %s
        """
        return dump_json(pg.query(sql, tuple(params) + (int(limit),)))
    except Exception as exc:
        return f"获取每日日志失败: {exc}"


def create_or_update_trader_daily_log(
    log_date: str,
    mood_score: int | None = None,
    energy_level: int | None = None,
    focus_score: int | None = None,
    sleep_hours: float | None = None,
    daily_review: str | None = None,
) -> str:
    """创建或更新交易员每日日志 (trader_daily_log)。"""
    try:
        if not log_date or not log_date.strip():
            return "错误: log_date 不能为空"
        date_str = log_date.strip()
        uid = default_user_id()

        existing = pg.query_one(
            "select id from trader_daily_log where user_id = %s::uuid and log_date = %s::date",
            (uid, date_str),
        )

        fields: dict = {}
        if mood_score is not None:
            fields["mood_score"] = int(mood_score)
        if energy_level is not None:
            fields["energy_level"] = int(energy_level)
        if focus_score is not None:
            fields["focus_score"] = int(focus_score)
        if sleep_hours is not None:
            fields["sleep_hours"] = float(sleep_hours)
        if daily_review is not None:
            fields["daily_review"] = daily_review

        if existing:
            if not fields:
                return f"未修改任何字段，已有日志 ID: {existing['id']}"
            set_sql = ", ".join(f"{k} = %s" for k in fields)
            sql = f"update trader_daily_log set {set_sql} where id = %s returning id"
            rows = pg.fetch_write(sql, tuple(fields.values()) + (existing["id"],))
            return f"成功更新 {date_str} 的交易员日志，ID: {rows[0]['id']}"

        fields["user_id"] = uid
        fields["log_date"] = date_str
        cols = ", ".join(fields.keys())
        placeholders = ", ".join(
            "%s::uuid" if k == "user_id" else ("%s::date" if k == "log_date" else "%s")
            for k in fields
        )
        sql = f"insert into trader_daily_log ({cols}) values ({placeholders}) returning id"
        rows = pg.fetch_write(sql, tuple(fields.values()))
        if rows:
            return f"成功新建 {date_str} 的交易员日志，ID: {rows[0]['id']}"
        return "新建日志失败"
    except Exception as exc:
        return f"保存交易员日志失败: {exc}"


def get_trade_emotions(start_date: str | None = None, limit: int = 10) -> str:
    """查询交易情绪分析与焦虑分数记录 (trade_emotions)。

    注：来自交易时面部表情快照，仅供辅助参考，需结合真实成交与持仓表现综合判断。
    """
    try:
        clauses: list[str] = []
        params: list = []
        if start_date:
            try:
                dt = datetime.strptime(start_date.strip(), "%Y-%m-%d")
                clauses.append("trade_time_ms >= %s")
                params.append(int(dt.timestamp() * 1000))
            except ValueError:
                pass
        where_sql = f"where {' and '.join(clauses)}" if clauses else ""
        sql = f"""
            select *
            from trade_emotions
            {where_sql}
            order by trade_time_ms desc
            limit %s
        """
        return dump_json(pg.query(sql, tuple(params) + (int(limit),)))
    except Exception as exc:
        return f"查询情绪记录失败: {exc}"


def get_trading_rules() -> str:
    """获取交易风控与守则参数 (trading_rules)。"""
    try:
        return dump_json(pg.query("select * from trading_rules order by rule_key asc"))
    except Exception as exc:
        return f"获取交易风控参数失败: {exc}"


def get_energy_schedules() -> str:
    """查询作息与交易日程安排 (energy_schedules)。"""
    try:
        return dump_json(pg.query("select * from energy_schedules order by sort_order asc"))
    except Exception as exc:
        return f"查询日程失败: {exc}"


def get_activity_sessions(start_date: str | None = None, limit: int = 20) -> str:
    """查询实际活动记录 (activity_sessions)，用于对照日程安排。"""
    try:
        clauses = ["start_time >= %s"] if start_date else []
        params: list = [start_date.strip()] if start_date else []
        where_sql = f"where {' and '.join(clauses)}" if clauses else ""
        sql = f"""
            select *
            from activity_sessions
            {where_sql}
            order by start_time desc
            limit %s
        """
        return dump_json(pg.query(sql, tuple(params) + (int(limit),)))
    except Exception as exc:
        return f"查询活动记录失败: {exc}"
