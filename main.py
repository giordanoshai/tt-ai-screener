"""
TT AI Screener — unified entry point.

Usage:
  python main.py                  # Web + MCP remote (default)
  python main.py --mcp            # MCP stdio (for Claude Desktop)
  python main.py --update         # Run data update once and exit

Options:
  --port PORT       Server port (default: 8766)
  --cron HH:MM      Daily auto-update at specified time (e.g. --cron 17:30)
"""
import argparse
import logging
import sys
import threading
import time
from datetime import datetime, timedelta

from db.init import init_db

logger = logging.getLogger("tt-trading")

_cron_thread = None
_cron_stop = threading.Event()


def start_mcp_stdio():
    from server import mcp
    mcp.run()


def run_update():
    from db.update import run
    run()


def _cron_loop(hour: int, minute: int):
    while not _cron_stop.is_set():
        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        wait = (target - now).total_seconds()
        weekday = target.strftime("%A")
        logger.info(f"[Cron] Next update: {target.strftime('%Y-%m-%d %H:%M')} ({weekday}), in {wait/3600:.1f}h")

        if _cron_stop.wait(timeout=wait):
            break

        if target.weekday() >= 5:
            logger.info("[Cron] Weekend — skipping update")
            continue

        logger.info(f"[Cron] Starting daily update at {datetime.now().strftime('%H:%M')}")
        try:
            run_update()
            logger.info("[Cron] Update complete")
        except Exception as e:
            logger.error(f"[Cron] Update failed: {e}")


def start_cron(cron_time: str):
    global _cron_thread
    hour, minute = map(int, cron_time.split(":"))
    _cron_stop.clear()
    _cron_thread = threading.Thread(target=_cron_loop, args=(hour, minute), daemon=True, name="cron-update")
    _cron_thread.start()
    logger.info(f"[Cron] Scheduled daily update at {cron_time}")


def stop_cron():
    global _cron_thread
    if _cron_thread and _cron_thread.is_alive():
        _cron_stop.set()
        _cron_thread.join(timeout=2)
        _cron_thread = None
        logger.info("[Cron] Stopped")


def apply_schedule(schedule: dict):
    stop_cron()
    if schedule.get("enabled") and schedule.get("time"):
        start_cron(schedule["time"])


def load_db_schedule():
    try:
        from db.init import get_conn
        import json
        con = get_conn()
        row = con.execute("SELECT value FROM app_settings WHERE key = 'update_schedule'").fetchone()
        con.close()
        if row:
            return json.loads(row[0])
    except Exception:
        pass
    return None


def main():
    parser = argparse.ArgumentParser(description="TT AI Screener")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--mcp", action="store_true", help="MCP stdio (for Claude Desktop)")
    mode.add_argument("--update", action="store_true", help="Run data update once")
    parser.add_argument("--port", type=int, default=None, help="Server port (default: 8766)")
    parser.add_argument("--cron", type=str, default=None, metavar="HH:MM",
                        help="Daily auto-update time (e.g. 17:30)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    init_db()

    if args.update:
        run_update()
        return

    if args.cron:
        start_cron(args.cron)
    else:
        db_schedule = load_db_schedule()
        if db_schedule and db_schedule.get("enabled") and db_schedule.get("time"):
            start_cron(db_schedule["time"])

    if args.mcp:
        logger.info("Starting MCP server (stdio)")
        start_mcp_stdio()
    else:
        from server import run_server
        run_server(args.port)


if __name__ == "__main__":
    main()
