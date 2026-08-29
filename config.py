import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent

# 数据库：读写分账号，物理隔开，避免误写。
# 采集（往库里写行情、基本面、新闻）已经全部搬到 tt-data-hub，
# 这个项目退化成只读消费端，写连接只给自选和持仓的增删改用。
#
# 兼容旧键名 READ / WRITE，等 .env 换成新键名之后可以把 or 这一段清掉。
PG_READ_URL = os.getenv("PG_READ_URL") or os.getenv("READ", "")
PG_WRITE_URL = os.getenv("PG_WRITE_URL") or os.getenv("WRITE", "")

# 线上 user_watchlist 表有非空的 user_id 列（uuid），插入时必须带上。
# 库里目前只有这一个用户，所以给了默认值，.env 里配了就以 .env 为准。
WATCHLIST_USER_ID = os.getenv(
    "WATCHLIST_USER_ID", "29795d82-c53b-43e5-985b-cb67b565d7bd")

UW_API_TOKEN = os.getenv("UW_API_TOKEN", "")

# AI 模型的正式配置在 config/models.json（原来是 DuckDB 的 ai_models 表，
# 迁库时漏了，线上没有）。**密钥不在那个 JSON 里**：每个模型只写一个
# api_key_env 字段指明环境变量名（ANTHROPIC_API_KEY / DASHSCOPE_API_KEY /
# OPENAI_API_KEY / AI_API_KEY 之类），值放 .env，由 ai_client 直接 os.getenv 取。
# 所以这里不需要、也不该把那些密钥列成常量。
#
# 下面这两组只是**兜底**：config/models.json 不存在、读不动、或者一个模型都
# 没有的时候，ai_client 才回到它们身上。

# AI: news sentiment analysis (small/cheap model)
SENTIMENT_API_BASE = os.getenv("SENTIMENT_API_BASE", "")
SENTIMENT_API_KEY = os.getenv("SENTIMENT_API_KEY", "")
SENTIMENT_MODEL = os.getenv("SENTIMENT_MODEL", "")

# AI: local analysis engine (skills analysis via API, more capable model)
AI_API_BASE = os.getenv("AI_API_BASE", "")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "")

# 输出语言。原来还能在设置页上切换（存 app_settings 表），那张表线上没有，
# 2026-08-29 这个功能整个删掉了 —— 只剩这一个环境变量，en / cn，默认 en。
# skills.get_lang() 自己读它，这里列出来只是为了让 .env 里的键名有个出处。
LANG_UI = os.getenv("LANG_UI", "en")

# MCP remote server
MCP_PORT = int(os.getenv("MCP_PORT", "8766"))
MCP_TOKEN = os.getenv("MCP_TOKEN", "")
