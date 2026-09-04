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

# Unusual Whales 实时接口。走 ai-tool.indevs.in 的代理，不直连官方域名。
#
# .env 里这两个键的名字是历史遗留的 UW_T_ENDPOINT / UW_T_KEY，
# 而 CLAUDE.md 写的是 UW_API_TOKEN——两边对不上，导致 config 一直读到空字符串。
# 这里两个名字都认，新名字优先，谁都不用改 .env。
UW_API_BASE = (os.getenv("UW_API_BASE") or os.getenv("UW_T_ENDPOINT")
               or "https://ai-tool.indevs.in/unusualwhales").rstrip("/")
UW_API_TOKEN = os.getenv("UW_API_TOKEN") or os.getenv("UW_T_KEY", "")

# 单次请求的超时（秒）。实测单票期权流 3.6 秒、实时警报 12~14 秒，
# 给到 20 秒是留给网络抖动的余量；超了就当这一路没有，走降级。
UW_TIMEOUT = float(os.getenv("UW_TIMEOUT", "20"))

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

# 自建 AKTools 服务（AKShare 的 HTTP 封装），跑在 tt-data-hub 那台机器上。
# 这里只用它做一件事：A股小时线的【实时】取数，见 core/akshare.py。
#
# 为什么需要：库里的 A股小时线只覆盖活跃池约 150 只，而活跃池是按
# 「站上 50 日均线 + 放量」挑的，跟「用户想看哪只票」是两回事——
# 2026-09-01 实测茅台、五粮液、宁德时代、比亚迪库里一根都没有。
# 没配这个地址也不会坏，只是 A股小时线退回库里那 150 只。
AKSHARE_BASE_URL = os.getenv("AKSHARE_BASE_URL", "")

# 盯盘守护进程（watchdog.py）。跟 MCP 服务分开跑，互不拖累。
#
# Telegram 机器人是复用用户已有的那个，token 和 chat id 得自己填进 .env——
# 代码不猜、也不去别的项目里找。没配就直接报错退出，不静默不推送：
# 一个「以为在盯盘、其实什么都没发」的进程比没有更糟。
# 键名两套都认，跟上面 UW 那段同一个道理：.env 里现成的是
# TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / TELEGRAM_PROXY（别处也在用），
# 而这套代码里写的是 TG_*。2026-09-04 首次冒烟就栽在这上面——config
# 读到空字符串，`notify.available()` 返回 False，进程起来直接报错退出。
# 不逼用户改 .env（那三个键可能别的脚本也在读），这里认两套，TG_* 优先。
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID", "")

# 服务器在国内，Telegram 要走代理。形如 http://127.0.0.1:7890。
# 留空表示直连。
TG_PROXY = os.getenv("TG_PROXY") or os.getenv("TELEGRAM_PROXY", "")

# 每轮间隔（秒）。一轮实测：A股快照 15 秒 + 美股 6 只并发 14 秒，
# 60 秒绰绰有余。
WATCHDOG_INTERVAL = int(os.getenv("WATCHDOG_INTERVAL", "60"))

# 预警线距关键位多远（小数，0.01 = 1%）。这个值是比出来的：
# 601138 在 2026-09-04 昨收 63.20、开盘 63.84，退 2% 的预警线落在 63.54，
# 开盘就已越过，等于一开盘就叫；退 1% 落在 64.19，盘中真涨上去才触发。
WATCHDOG_WARN_DIST = float(os.getenv("WATCHDOG_WARN_DIST", "0.01"))

# 盘前要不要盯。盘前成交量只占全天 1%~2%，信号成色差，
# 但财报和突发消息常在那时候发酵，默认还是盯（门槛自动提高）。
WATCHDOG_PREMARKET = os.getenv("WATCHDOG_PREMARKET", "1") not in ("0", "false", "")

# 去重状态存哪。进程重启不该重复轰炸用户，所以要落地。
WATCHDOG_STATE_FILE = os.getenv(
    "WATCHDOG_STATE_FILE", str(BASE_DIR / ".watchdog_state.json"))

# MCP remote server
MCP_PORT = int(os.getenv("MCP_PORT", "8766"))
MCP_TOKEN = os.getenv("MCP_TOKEN", "")

# MCP SDK 自带一层防 DNS 重绑定的 Host 白名单，默认只放行
# 127.0.0.1 / localhost / [::1]，别的 Host 一律 421 Misdirected Request。
# 也就是说下面两种访问【默认都是不通的】，必须把主机名显式加进来：
#   - 局域网直连          Host: 192.168.1.38:8766
#   - 走 Cloudflare Tunnel  Host: mcp.你的域名
#
# 逗号分隔，端口可以用 * 通配。例：
#   MCP_ALLOWED_HOSTS=192.168.1.38:*,mcp.example.com
MCP_ALLOWED_HOSTS = [h.strip() for h in
                     os.getenv("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
