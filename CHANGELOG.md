# Changelog

All notable changes to this project will be documented in this file.

### [2026-11-18] 交易复盘工具支持识别/合并差价与单腿并全面精简返回体
* **新增 (Added)**:
  - 在 [tools/_common.py](file:///d:/Dev_project/Python_Project/tt-trading-mcp/tools/_common.py) 新增 `parse_occ_code`、`short_option_contract`、`short_spread_contract`，将 OCC 长合约代码压缩为易读短串（如 `261117 652P/650P`、`261218/261113 15C`、`261106 738P`）。
  - 在 [tools/trade_journal.py](file:///d:/Dev_project/Python_Project/tt-trading-mcp/tools/trade_journal.py) 新增组合单自动识别与双腿合并逻辑：通过 `metrics.combo_info.parent_order_id` 合并 `VERTICAL` / `CALENDAR` 双腿为单一逻辑交易（按 `LONG_ASSET` / `SHORT_ASSET` 计算净入/出场价、净盈亏、净收益率与 R 倍数），同时兼容历史 `option_code` 斜杠差价记录与 `single` 单腿，并在查询条件修正按整体 `strategy_direction_view` 过滤方向。
  - `get_trade_details` 新增支持传入任意一腿 `display_id`、UUID 或 `parent_order_id`，自动拉齐配对腿与全部 `executions`。
  - 在 [tools/review_tonight_trades.py](file:///d:/Dev_project/Python_Project/tt-trading-mcp/tools/review_tonight_trades.py) 的 `summary` 增加 `spreads` / `singles` 笔数统计，并在逐笔明细补充 `contract` 简称字段。
* **修改 (Changed)**:
  - 全面精简 `get_recent_trades`、`get_recent_trades_light` 与 `get_trade_details` 返回结构：剔除 `user_id`、`strategy_template_id`、冗长 `ideal_plan` 规则、11 板块 ETF 明细、`NO_DEVIATION` 占位标签、`indicator_history` 与原始面部特征点等低价值字段；`get_recent_trades_light` 的 `schema` 新增 `kind`（`vertical`/`calendar`/`single`）与 `contract`。
  - 更新 [tests/test_merged_tools.py](file:///d:/Dev_project/Python_Project/tt-trading-mcp/tests/test_merged_tools.py)、[tests/test_review_tonight_trades.py](file:///d:/Dev_project/Python_Project/tt-trading-mcp/tests/test_review_tonight_trades.py)、[PLAN.md](file:///d:/Dev_project/Python_Project/tt-trading-mcp/PLAN.md)、[AGENTS.md](file:///d:/Dev_project/Python_Project/tt-trading-mcp/AGENTS.md) 与 [README.md](file:///d:/Dev_project/Python_Project/tt-trading-mcp/README.md)。

### [2026-11-18] `review_tonight_trades` 联合返回当晚交易日志
* **新增 (Added)**:
  - [tools/review_tonight_trades.py](file:///d:/Dev_project/Python_Project/tt-trading-mcp/tools/review_tonight_trades.py) 同步查询美东当日的 `notes`（`daily_trade_note=true`，兼容 `trade_date` 或美东时区 `created_at`）与 `trader_daily_log`（按 `log_date`），清洗 HTML 标签后通过 `trade_notes` 与可选 `daily_log` 返回，并在 `summary` 增加 `notes_count`。
  - 在 [tests/test_review_tonight_trades.py](file:///d:/Dev_project/Python_Project/tt-trading-mcp/tests/test_review_tonight_trades.py) 新增当晚交易日志、`trader_daily_log` 及 HTML 清洗测试。
* **修改 (Changed)**:
  - 更新 [AGENTS.md](file:///d:/Dev_project/Python_Project/tt-trading-mcp/AGENTS.md) 与 [README.md](file:///d:/Dev_project/Python_Project/tt-trading-mcp/README.md) 中 `review_tonight_trades` 的说明。

### [2026-11-18] 合并 `TTAiTradingSystem` 的 27 个 MCP 工具至 `tt-trading-mcp`
* **新增 (Added)**:
  - 新增 [tools/_common.py](file:///d:/Dev_project/Python_Project/tt-trading-mcp/tools/_common.py)，抽取 `dump_json`、`normalize_direction`、`calc_mfe_capture_rate`、`resolve_market` 等公共辅助函数。
  - 新增 [tools/trade_journal.py](file:///d:/Dev_project/Python_Project/tt-trading-mcp/tools/trade_journal.py)（7 个工具：`get_recent_trades`、`get_recent_trades_light`、`get_trade_details`、`get_strategy_templates`、`get_strategy_signals`、`get_option_ohlcv`、`get_option_market_snapshots`）。
  - 新增 [tools/journal_notes.py](file:///d:/Dev_project/Python_Project/tt-trading-mcp/tools/journal_notes.py)（10 个工具：`get_recent_notes`、`get_daily_trade_notes`、`create_note`、`update_note`、`get_trader_daily_logs`、`create_or_update_trader_daily_log`、`get_trade_emotions`、`get_trading_rules`、`get_energy_schedules`、`get_activity_sessions`）。
  - 新增 [tools/market_context.py](file:///d:/Dev_project/Python_Project/tt-trading-mcp/tools/market_context.py)（10 个工具：`get_latest_market_breadth`、`get_market_regime_history`、`get_daily_news_summary`、`search_stock_news`、`get_news_sentiment_summary`、`search_stocks_meta`、`get_stock_ohlcv`、`get_stock_fundamentals`、`get_system_logs`、`analyze_server_status`）。
  - 在 [config.py](file:///d:/Dev_project/Python_Project/tt-trading-mcp/config.py) 新增 `TTAI_LOG_DIR` 配置项；新增 [PLAN.md](file:///d:/Dev_project/Python_Project/tt-trading-mcp/PLAN.md) 与 [tests/test_merged_tools.py](file:///d:/Dev_project/Python_Project/tt-trading-mcp/tests/test_merged_tools.py)。
* **修改 (Changed)**:
  - 在 [server.py](file:///d:/Dev_project/Python_Project/tt-trading-mcp/server.py) 注册全部 27 个新工具（服务总工具数增至 40 个），并剔除与本仓库重复的 6 个旧版自选/选股工具。
  - 更新 [tests/test_main.py](file:///d:/Dev_project/Python_Project/tt-trading-mcp/tests/test_main.py)、[AGENTS.md](file:///d:/Dev_project/Python_Project/tt-trading-mcp/AGENTS.md) 与 [README.md](file:///d:/Dev_project/Python_Project/tt-trading-mcp/README.md)。

### [2026-11-18] 精简 `review_tonight_trades` 返回结构并新增该 MCP 工具
* **新增 (Added)**:
  - 新增 [tools/review_tonight_trades.py](file:///d:/Dev_project/Python_Project/tt-trading-mcp/tools/review_tonight_trades.py) 并在 [server.py](file:///d:/Dev_project/Python_Project/tt-trading-mcp/server.py) 注册为 MCP 工具 `review_tonight_trades`。
  - 支持按美东时间 (`America/New_York`) 交易日窗口（默认取当前美东日期，自动处理冬夏令时）从 `ftt_spread_trades`（垂直价差）和 `ftt_trades`（单腿期权）读取开/平仓记录。
  - 新增测试 [tests/test_review_tonight_trades.py](file:///d:/Dev_project/Python_Project/tt-trading-mcp/tests/test_review_tonight_trades.py)。
* **修改 (Changed)**:
  - 精简 `review_tonight_trades` 返回体：公共口径（`account`、`tz`、`unit`）收敛到 `summary`，逐笔明细移除重复字段与空/默认布尔标记、当日时间压缩为 `HH:MM:SS`、各腿成交价压缩为 `legs_in` / `legs_out`（`{side: fill_price}`）。
  - 同步更新 [tests/test_main.py](file:///d:/Dev_project/Python_Project/tt-trading-mcp/tests/test_main.py) 的注册工具清单及 [AGENTS.md](file:///d:/Dev_project/Python_Project/tt-trading-mcp/AGENTS.md)、[README.md](file:///d:/Dev_project/Python_Project/tt-trading-mcp/README.md) 文档。

### [2026-09-05] UW 的代理从「碰巧有」改成「明着配」

UW 的接口要出国，但这一路一行代理配置都没有——`core/uw.py` 用的是
`urllib.request.urlopen`，而它自己会去读 `HTTP_PROXY` 环境变量。开发机上
那个变量一直设着（`http://127.0.0.1:1080`），所以一直是走代理的，**但那是
环境碰巧给的，不是代码保证的**。

危险在于换个地方跑就没了：MCP 由 Claude Desktop 以子进程拉起、服务器上用
systemd 或计划任务跑，都不一定继承得到那些变量。而 `uw.get()` 吞掉一切异常
只返回 None，代理掉了的表现是「期权数据怎么全是空的」「美股小时线怎么又
退回库里了」——库里那份从 2026-09-03 起就不再更新，等于拿着越来越旧的数据
当实时用，还看不出根因。

* **新增 (Added)**: `UW_PROXY` 环境变量，UW 出网的代理显式写在 `.env` 里。
  开发机 `http://127.0.0.1:1080`，服务器 `http://127.0.0.1:1081`。留空则维持
  原行为（跟随系统 `HTTP_PROXY`），所以现有部署不改也照常跑。
* **新增 (Added)**: `core/net.py` —— 代理地址规范化 + opener 构造的公共模块。
  原来这段只长在 `core/notify.py` 里，UW 要用就得让「行情客户端」去 import
  「推送模块」，那条依赖半年后没人看得懂。现在两边平等地依赖它，
  `notify.normalize_proxy` 保留同名重新导出，别处的写法不用改。
* **新增 (Added)**: UW 请求失败时往 stderr 留一行（按异常类型去重，一类只喊
  一次）。返回值仍然是 None，调用方的降级契约一个字没变——但「代理断了」和
  「这只票本来就没有期权数据」在 None 面前长得一模一样，不出声就永远分不出来。
* **修改 (Changed)**: `UW_PROXY` 配成 SOCKS 会被明确挡下并说明原因，不静默
  退化成直连。Python 标准库的 urllib 不支持 SOCKS，跟 `TG_PROXY` 同一个限制。
* **修改 (Changed)**: `core/uw.py` 的注释里「代理」一词原本指的是
  `ai-tool.indevs.in` 那个 API 中转站，现在跟网络代理同名不同物，
  统一改称「中转站」，并在模块开头把两个概念分开写明。

实测五种情形（显式配代理 / 漏写 `http://` 前缀 / 留空跟随环境 / 端口配错 /
配成 SOCKS）行为都符合预期。另：跑全量测试时有 10 个用例失败，`git stash`
回到改动前实测确认是同样这 10 个，与本次改动无关。

**走代理不花时间，别再为这个纠结。** 交替采样比过（交替是为了把网络漂移
摊平到两条路上，不偏袒谁），单条路取中位数：

| | 直连 | 走代理 |
|---|---|---|
| 开发机 Windows | 1.07s | 1.10s |
| 服务器 | 1.12s | 1.06s |

四个数字全在 1 秒出头，差异小于单次波动（同一条路最快 0.84s、最慢 2.90s）。
**单次测量在这里毫无意义**——第一轮就是拿单次数据得出「直连快 1.6 秒」的
错误结论，多测几次立刻推翻。

稳定性上有一件没查清的事，记在这里免得下次重新惊讶一遍：开发机上直连曾
**连续 7 次 HTTPError**，而同一时段代理侧 14 次全成（顺序是代理 7 次全成 →
直连 7 次全败 → 代理 7 次全成）。之后又打了 60 余次请求一次都没能重现，
当时也没留下状态码，所以定不了性——中转站挂在 Cloudflare 后面，可能是临时
判定，也可能是那头抖了七八秒，两种都排除不掉。代理侧至今 0 失败，所以留着
当保险；哪天代理软件本身出问题，把 `.env` 里 `UW_PROXY` 那行注掉就回直连。

### [2026-09-04] 盯盘上线首轮就暴露一个误导：报的是昨天的事，看着像刚发生

服务装好 systemd 启动，第一轮就推了两条真报警（ORCL、NVO 假突破）。数据
核对下来内容全对，但**时点是错的**——那是前一天收盘时发生的事。

起因：盘前 UW 还没有当天的分钟线，`us_quotes` 取到的是前一个交易日收盘
那批数据。于是「当天最高价」实际是昨天的，「假突破」判定跟着算的也是昨天
那一趟。ORCL 确实在 9/3 冲到 156.77 撞了 156.22 又跌回 154.38 —— 信息本身
对持仓的人有价值（那道坎撞了没过去），但在盘前收到会以为正在发生。

* **修复 (Fixed)**: 报价带上 `as_of`——这批数据对应哪个交易日。
  美股用已经算出来的最新美东交易日（解析不出日期时给 `None`，宁可不标
  也不标错）；A股用北京当天（新浪快照没有日期字段，但它只在 A股交易时段
  被调用，必然是当天）。
* **修复 (Fixed)**: `notify.format_alert()` 新增可选参数 `today`，跟报价的
  `as_of` 对不上时在文案里加一行「⏰ 这是 09/03 收盘时的情况，不是刚刚发生」。
  位置排在持仓那行之前——读的顺序是「什么事 → 细节 → 什么时候的 → 我手上
  有多少」。数据就是当天的时候一个字都不多加。
* **新增 (Added)**: 8 条测试（报价带最新交易日而非数组里最早那天、解析不出
  日期时为 None、A股用北京当天；文案在过期数据上加标注、当天数据不加、
  没传 today 不猜、缺 as_of 不标、标注排在持仓行之前）。

**为什么不干脆压掉过期信号**：那条信息本身有价值——持仓的票撞了坎没过去，
用户应该知道。压掉是丢信息，标清时点才是对的。冷却机制（同一个位 5 个交易日
内不重复）已经保证了它不会每天早上重复轰炸。

### [2026-09-04] 盯盘首次真实冒烟：三条推送到手机，路上踩了两个静默坑

第一次拿真机器人、真代理跑通推送（启动通知 + 接近预警 + 假突破，3/3 送达）。
在那之前先踩了两个配置坑，**共同点是配错了不报错、只会安静地什么都不发**——
正是这个功能最怕的失败方式。

* **修复 (Fixed)**: **Telegram 键名两套都认**。用户配置里现成的是
  `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` / `TELEGRAM_PROXY`（别的脚本也在读），
  而代码里写的是 `TG_*`，于是 config 读到空字符串、`notify.available()` 返回
  False、进程起来直接报错退出。现在 `config.py` 两套都读、`TG_*` 优先，
  跟 `UW_T_ENDPOINT` / `UW_API_TOKEN` 那对同一个处理方式。
  **这是同一个坑在这个仓库里第二次发生。**
* **修复 (Fixed)**: **代理地址加规范化和明确报错**（`core/notify.py` 新增公开
  函数 `normalize_proxy()`）。两种配错以前都表现为静默超时：
  - 裸地址 `127.0.0.1:1081`——urllib 会把冒号前的 `127.0.0.1` 当成协议名解析，
    代理配置直接失效退化成直连。现在自动补 `http://`。
  - **SOCKS 代理 `socks5://…`——Python 标准库根本不支持**，`ProxyHandler`
    只认 HTTP/HTTPS。现在直接挡下并往 stderr 写清原因和解法（换 HTTP 端口），
    而不是花十几秒超时后返回一个没有上下文的 False。没有为此引入 PySocks——
    不值得为一个换端口就能解决的问题破「不引入新依赖」这条约束。
* **新增 (Added)**: **每条推送带来源标识和图标**（`notify.PREFIX` + `notify.title()`）。
  这个 Telegram 机器人跟别的系统共用（交易执行那套发的是「【SOS】✅ 已买入 · …」），
  盯盘发的一律是 `【盯盘】<图标> <说什么事> · <票>`：
  接近预警 ⚠️ / 有效突破 ✅ / 假突破 ❌ / 跌破 🔻 / 启动 🟢 / 故障告警 🔴。
  手机通知栏常常只显示第一行，所以票代码也放进了第一行。
* **修改 (Changed)**: 推送失败一律留痕，且**说清走的是直连还是哪个代理**——
  排查推送问题的第一个分岔口就是这个。新增 `_warn()` 统一往 stderr 写。
* **修复 (Fixed)**: `tests/test_core_notify.py` 的两条发送用例以前没隔离
  `config.TG_PROXY`，会读到运行机器上的真实代理配置。本机配置从裸地址改成
  SOCKS 地址之后它们立刻变红——红得有道理，但那不是这两条想测的东西。
  现在一并 patch 掉，类文档里写明了原因。
* **新增 (Added)**: 12 条测试覆盖代理规范化（空值/裸地址/已有协议头/SOCKS 三种
  大小写/发送时拒绝 SOCKS 并说明原因/把规范化后的地址交给发请求层/失败日志
  包含所走路径）和推送标题（四类信号各自带前缀、四个图标互不相同、
  盘前盘后标记不被挤掉、票代码在第一行）。

### [2026-09-04] 盯盘功能最终复查修复：把「以为在盯盘、其实什么都没发」的路全堵上

整支功能复查后的一次性修复，12 项。主线只有一条：这个功能最危险的失败
不是崩溃，是**进程还活着、但什么都没在看**——取价超时、算不出关键位、
读不到持仓，原来三处全是静默跳过，运维手段只有「看进程还在不在」。

* **修复 (Fixed)**: 取实时价的两处超时都不够，而且失败后一行日志都没有。
  A股全市场快照实测要 15 秒，却在用给小时线定的 20 秒（这台 AKTools 跟
  采集任务是同一个服务，采集跑起来更慢）；美股拉 K 线实测 12.9~14.0 秒，
  也在用默认的 20 秒——`core/uw.py` 早就为此定了 `OHLC_TIMEOUT`（≥30 秒）
  并写下了教训，新代码又踩了回去。现在 A股走新加的
  `core.akshare.spot_snapshot()`（自己的 60 秒超时），美股显式用
  `OHLC_TIMEOUT`，两条路失败都往 stderr 写一行。
* **修复 (Fixed)**: 美股分钟线的时间字段全部解析不出来时，原来回退成
  「扫全部原始行」，于是「当天最高价」会静悄悄变成「一个月最高价」。而
  「假突破」的判定条件正是「当天最高价越过关键位、现价又跌回下方」——
  一个月最高价几乎必然高于任何一道近档阻力，**每只美股持仓每天都会误报
  假突破**。改成只用最新那一根。
* **修复 (Fixed)**: 「接近预警」原来穿一次线就直接推，是全流程里唯一没有
  采样点确认的路径。可预警线比关键位还低 1%，恰恰是盘前那种「几百股就能
  打出来的孤立假价格」最容易穿过的一道线。现在预警也要连着站住
  2 个采样点（盘前 3 个）才推，计数跟到位判定分开存。
* **修复 (Fixed)**: 美股的「今天」原来按北京日期算，而北京零点正好是美东
  中午 12:00（夏令时）——每日配额闸在盘中清零（一只美股实际上限是设计说
  的两倍）、关键位缓存也在盘中被清空重算。改成 A股按北京日期、美股按美东
  日期。
* **修复 (Fixed)**: 冷却期原来按自然日算，周五报过的位下周三就解冻了；
  设计文档写的是 5 个**交易日**（下周五）。设计阶段那组噪音实测数据就是
  交易日口径跑出来的，换成自然日实际噪音高出约 40%。改成自己数交易日
  （跳周末，不管节假日，不引入新依赖）。
* **新增 (Added)**: `scripts/tt-watchdog.service` —— 用户级 systemd 服务，
  `Restart=always` / `RestartSec=30`。原计划是 `nohup` 挂后台：机器重启不会
  自己回来、崩了没人拉起、日志不轮转，而盯盘断掉是静默的。`scripts/deploy.sh`
  现在连它一起重启（只重启 `tt-mcp` 的话，部署完盯盘还在跑旧代码且没有提示）。
* **新增 (Added)**: 三样可观测性——启动时推一条 Telegram（「盯盘已启动，
  当前盯 N 只」）、每轮往 stderr 写一行带时间戳的摘要（时段 / 持仓数 /
  报价数 / 推送数）、某个市场连续 10 轮一只报价都拿不到就推一条告警。
* **修改 (Changed)**: `core/quotes.py` 不再越层调 `core.akshare._get`
  这个私有函数；`core/akshare.py` 的 `_get` 加了可选 `timeout` 参数，
  默认值不变，`hourly()` 行为一个字未动（有回归测试守着）。
* **修改 (Changed)**: 补注释三处——推送失败的回滚实际是「重新攒够确认
  才重推」（2~3 分钟后），不是原注释说的「下一轮重试」；盘后永远判不出
  「有效突破」是有意的降级（盘后没有可比的均量口径），不是漏了；
  `.gitignore` 加上 `.watchdog_state.json`（每台机器各跑各的，
  2026-08-29 有过一次被 `git add -A` 误提交的前科）。
* **修改 (Changed)**: `CLAUDE.md` 的 `.env` 清单补上 `WATCHDOG_STATE_FILE`。

### [2026-09-04] 端到端验收 + 文档收尾

拿 2026-09-04 601138 的真实数据走一遍盯盘功能的完整链路，确认设计文档
第十节的验收标准条条落地；同时把 CLAUDE.md 和设计文档补齐到跟代码一致。

* **新增 (Added)**: `tests/test_watchdog_acceptance.py`。用 601138 在
  2026-09-04 的真实日线走完整链路，断言当天该推两条：先在 64.19 报
  「快到坎了」，再在冲高回落之后报「假突破」——这就是用户当天真实
  遇到、但没有被任何东西接住的那件事（用户原话：「今天到了一个关键
  的突破位，我不知道这是突破位，我没有卖，导致到了突破位后就直接
  下跌了」）。以后谁改算法把这两条改红了，就是改坏了。另外补了一组
  **支撑位方向**的用例（手造数据，`core/levels.py` 一个字未动）：
  支撑位按「离现价从近到远」排序（价格降序）、1.2% 以内的相邻支撑位
  被正确合并、预警线画在支撑价**上方**——601138 那份真实数据的支撑位
  恰好是空的（垫底数据被 12% 距离过滤器筛掉），`merge_levels(reverse=
  True)` 这条支撑位路径此前从未被端到端测过。验收测试 7 条全绿，
  连同其余六个新模块一起回归（`test_core_levels/session/quotes/alerts/
  notify.py` + `test_watchdog.py` + 本文件）共 90 条全绿，无新增失败。
* **修改 (Changed)**: `CLAUDE.md` 补上 `watchdog.py` 和 `core/` 五个新
  模块（`levels.py` / `session.py` / `quotes.py` / `alerts.py` /
  `notify.py`）的位置说明，以及 `.env` 里 Telegram 推送新增的六个
  配置项（`TG_BOT_TOKEN` / `TG_CHAT_ID` / `TG_PROXY` /
  `WATCHDOG_INTERVAL` / `WATCHDOG_WARN_DIST` / `WATCHDOG_PREMARKET`）。
* **修复 (Fixed)**: 设计文档
  `docs/superpowers/specs/2026-09-04-盯盘关键位提醒-design.md` 第 3.4
  节还写着「只有北京时间 08:00–09:30 和 11:30–13:00 是空的」——漏了
  一个：A 股 15:00 收盘到美股盘前 16:00 开盘之间还有 1 小时空当，
  实为三段（1.5+1.5+1=4 小时，与「一天 24 小时减去 20 小时盯盘时长」
  对得上）。`core/session.py` 里的同一处错误已经在更早的提交
  （`7b82bf3`）修过，这次是把设计文档补齐到跟代码一致。

### [2026-09-04] 盯盘功能补全记录：关键位、交易时段、实时报价、Telegram 推送

前四个模块在各自的任务里实现并通过独立复查，但当时都漏记了
CHANGELOG；本次端到端验收时一并读现状、补齐差额，另记一处期间
发现并修复的真实缺陷（推送失败被静默吞掉）。

* **新增 (Added)**: `core/levels.py`——关键位计算，11 个测试用例。
  从日线找头顶的阻力和脚下的支撑，近/中/远三档窗口各取局部高低点，
  按距离现价 12% 以内筛、1.2% 以内合并、只留最近两档。**关键设计
  决定：一律用收盘价找位，不用最高价**——601138 在 2026-08-28 冲到
  66.69 又全部吐回来收在 64.04，如果用最高价，这根长上影线会把下面
  真正的阻力 64.84 整个盖住，09-04 那次假突破就会被漏掉。
* **新增 (Added)**: `core/session.py`——交易时段判断，14 个测试用例。
  A 股只有盘中（240 分钟），美股有盘前盘后（合计 20 小时），冬令时
  美股三段各顺延 1 小时，按日期算不写死；盘前流动性差（实测 AAPL 只
  占全天成交量 1%~2%），要求更多采样点确认且不判「有效突破」。
* **新增 (Added)**: `core/quotes.py`——实时报价，10 个测试用例。A 股
  走新浪全市场快照（一趟 15 秒拿回 5555 只，不管盯几只都一样快）；
  美股走 UW 的 1 分钟线并发拉（单票 11.4 秒，6 只一起也只要 13.8
  秒）。两个坑写进了模块文档：这台 AKTools 上东方财富源全部接口
  返回 500，只能用新浪；UW 没有「当前价」接口，只能取 1 分钟线最新
  一根，且**返回的是倒序**（第一行才是最新，取末行会拿到一个月前的
  价格）。任何一路失败只返回空，不抛异常，不拖累另一个市场。
* **新增 (Added)**: `core/notify.py`——Telegram 推送，10 个测试用例。
  真正发请求的 `_post` 单独抽出来，测试一律换掉，绝不真发消息。
  推送文案固定说清三件事：哪只票、发生了什么、手上有多少股、多少成本。
* **修复 (Fixed)**: **推送失败曾被静默吞掉**（`a63e47e`，复查发现）。
  `watchdog.py` 的 `run_once()` 原来不看 `notify.send()` 的返回值，
  不管发没发成功都当成「已处理」——但在推送之前，报警的冷却期和当日
  配额已经写入状态。代理断了或 Telegram token 失效时，后果是：报警
  记了冷却、占了配额，用户手机上却什么都没收到，下一轮也不会重试。
  这正是一个「以为在盯盘、其实什么都没发」的进程，比没有这个进程还糟。
  现在推送失败会撤销冷却记录（按 kind 撤 `fired` 或 `warned`）和配额
  计数、写一行 stderr 日志，让下一轮能重试；同时补了两条集成测试
  （保险丝真的挡在推送前面、推送失败真的会撤销记录），
  `tests/test_watchdog.py` 从 18 条增加到 20 条。

### [2026-09-04] 盯盘主循环：把五个模块串起来，独立进程每分钟跑一轮

前五个模块（关键位、时段、实时报价、报警判定、Telegram 推送）都已完成，
本次新增 `watchdog.py` 把它们接成一个可以连跑一整天的独立进程。

* **新增 (Added)**: `watchdog.py`——盯盘守护进程主循环。
  - `run_once()` 一轮完整流程：读持仓 → 按市场分流取实时价 → 跟关键位比对 → 触发则推送。
  - 持仓**每轮都重新读、不缓存**：用户在 Claude.ai 里加仓/平仓，下一分钟盯盘就得认。
  - 非交易时段直接返回空列表，一个请求都不发。
  - 任何一路（取持仓、取报价、算关键位）失败都只影响那一路，不让整轮或进程死掉。
  - `_within_daily_cap()` 保险丝：每票每天 4 条、全天 25 条，防止算法出 bug 时把手机炸掉。
  - 去重状态落地到本地 JSON 文件，进程重启不重复轰炸；文件坏了自动退回空状态。
  - `tests/test_watchdog.py` 18 个用例，覆盖持仓不缓存、非交易时段零请求、单路失败不致命、状态存取往返。
* **修改 (Changed)**: `tests/conftest.py` 的全局网络拦截名单加入 `core.notify._post`——
  测试机 `.env` 里配的是真实的 Telegram 机器人凭证，不拦的话每跑一次涉及推送的测试
  就会给用户手机发一条真消息，跟 AKShare / UW 那两处拦截同一个道理。

### [2026-09-04] 盯盘报警判定实现：只推要动手的三类，预警穿越触发

核心降噪逻辑已实现。实测 45 个交易日，把站上没量/受阻回落/探底守住也推出去会导致 73% 的天都在响；只推有效突破/假突破/跌破，降到 31%。预警穿越触发把报警从 27 条压到 17 条。

* **新增 (Added)**: `core/alerts.py` 四个核心函数和 18 个测试用例。
  - `intraday_volume_ratio()` — 盘中量比按已开盘时间折算，开盘半小时也能算出正确的成交量倍数。
  - `classify()` — 碰到关键位后看怎么走，分出有效突破/假突破/跌破/不推四种结果。
  - `crossed_warn_line()` — 穿越触发：只在价格从远处走过来穿过预警线时报，附近磨蹭不报。
  - `evaluate()` — 完整流程：连续采样点确认、冷却期去重、全流程输出。
* **修改 (Changed)**: 常量定义明确：触及带宽 0.5%、到位冷却 5 天、预警冷却 10 天、放量门槛 1.2 倍、跳过开盘 15 分钟、保险丝各 4 条/25 条。

### [2026-09-04] 盯盘设计补两处：提前预警、美股盘前盘后

用户看过设计后提了两点，都改进了设计的实用性。

* **修改 (Changed)**: **不等摸到关键位才提醒，提前 1% 就叫**。
  原设计要价格摸到 64.84 才响，那时人可能在开会、在吃饭，
  等看到手机已经冲上去又掉下来了。改成每个关键位往回退 1% 画预警线
  （64.84 → 64.19），且**必须是「穿越触发」**——价格从远处走过来、
  穿过预警线的那一刻才报，在附近磨蹭不报。提前距离是比出来的：
  2% 的预警线落在 63.54，而当天开盘就是 63.84，**一开盘就叫等于没用**。
  穿越触发把预警从 27 条压到 17 条。
* **修改 (Changed)**: **交易时段按三段拆开，A 股只有盘中，美股有盘前盘后**。
  合起来一天要盯 20 小时，只有北京 08:00–09:30 和 11:30–13:00 是空的。
  **盘前必须区别对待**：实测 AAPL 三天，盘前成交量只占全天 1.21%~2.20%
  （常规 75%~81%，盘后 17%~23%），几百股就能把价格打出去，
  所以盘前要 3 个采样点确认（常规 2 个）且不做「有效突破」判定。
  时段判断直接用 UW 分钟线自带的 `market_time` 字段（r/pr/po），不按时间猜；
  冬令时三段各顺延 1 小时，按日期算不写死。
* **决定 (Decided)**: **夜间不静音**。美股常规时段（北京 21:30–04:00）
  压在睡觉时间上，但那是成交量占 80% 的主战场，用户要求全部照推、
  自己在手机上设免打扰。实现上不加任何夜间特殊逻辑，也不做汇总推送。

### [2026-09-04] 盯盘关键位提醒：设计与可行性验证

用户持有的 601138 当天冲到 65.30 又跌回 63.55，撞的是 8/31 的收盘高点
64.84，但没有任何东西在盘中盯着，等他发现已经回落了。现有
`watch_positions` 只报「已经发生」的事，而且必须用户开口才跑。

* **新增 (Added)**: 设计文档
  `docs/superpowers/specs/2026-09-04-盯盘关键位提醒-design.md`——
  一个独立的盯盘守护进程，每分钟从 `user_positions` 读持仓、取实时价、
  跟当天关键位比对，触发就推 Telegram。不新建数据库表，
  「只写三张表」的规矩不破。
* **验证 (Verified)**: 关键位算法在 601138 近 45 个交易日上回放了三版。
  **第一版用「最高价」找位，漏掉了 09-04 这次**——8/28 那根冲到 66.69
  又全吐回来的长上影线，把下面真正的阻力 64.84、64.50 全盖住了。
  改用**收盘价**找位后抓到了（插针不算数，收得住才算数）。
  再叠加「只推要动手的三类（假突破/有效突破/跌破）+ 同一个位 5 天内
  不重复」，报警从 45 次降到 18 次、从 73% 的交易日在响降到 31%。
* **记录 (Noted)**: 探测中发现两个属于 tt-data-hub 的问题，**修在那边**：
  1. AKTools（地址见 `.env` 的 `AKSHARE_BASE_URL`）上**东方财富源整个挂了**，
     所有 `_em` 后缀接口返回 500（`stock_zh_a_spot_em`、
     `stock_bid_ask_em`、`stock_zh_a_hist_min_em`、`stock_zh_index_spot_em`）；
     新浪源全部正常，`core/akshare.py` 用的正是新浪的
     `stock_zh_a_minute`，**没受影响**。
  2. 美股日线 2026-09-03 **只采到 106 只**，其他日子都是 2,094 只，
     那一趟明显没跑完。
* **记录 (Noted)**: 三个实时取数的坑，写进设计文档备查：
  新浪小时线**对当天盘中只给成交量不给价格**（OHLC 全是 None），
  所以 A 股盯盘只能走全市场快照；UW **没有「当前价」接口**
  （`/info` `/quote` `/price` `/spot` 全返回 None），只能取 1 分钟线的
  最新一根，且 **UW 返回的是倒序**（第一行最新）。

### [2026-09-03] 部署改走 GitHub，不再走本机裸仓库

用户把 GitHub 仓库私有化并在服务器上配了持久凭据之后，部署通道从
「本地 → 本机裸仓库 → 服务器」改成「本地 → GitHub → 服务器」。

* **修改 (Changed)**: `scripts/deploy.sh` 推 `origin`（GitHub）而不是 `server`
  （裸仓库），服务器那边 `git remote set-url origin` 指到 GitHub 再 fetch。
  脚本里每次都校正一遍 origin——这台机器上的仓库历史上指过裸仓库，
  **指错了拉到的是一份过期代码而且不会报错**（tt-data-hub 2026-09-02
  就因为 origin 指着一个 `/tmp` 里的临时打包文件，差点把服务器退回一个月前）。
* **修复 (Fixed)**: 服务器上 `git remote` 已经改好。凭据是
  `credential.helper=store`（token 在 `~/.git-credentials`，权限 600），
  HTTPS 拉私有仓库不用交互。**SSH 那条路不通**：服务器 known_hosts 里
  没有 github.com，`git@github.com:` 形式报 Host key verification failed。
* **保留**: 本机的 `server` remote 和服务器上的裸仓库都留着当后备，
  日常不再用。

### [2026-09-03] 省掉三成 token；四张天天在写、这边一行没读过的表接上了

起因是两个问题：「省 token 还有别的地方吗」和「工具齐不齐，别有了数据没工具」。
分别量了一遍，两件事都有实打实的答案。

#### 一、省 token：八个工具全都变小了，合计 −31%

实测一次调用的返回体（按 3 字符 ≈ 1 token）：

| 工具 | 改前 | 改后 | |
|---|---|---|---|
| `list_watchlist` | 66,672 | 42,001 | −38% |
| `scan_swing` | 23,287 | 17,579 | −25% |
| `review_positions` | 16,092 | 11,597 | −28% |
| `screen_stocks` | 15,760 | 11,095 | −30% |
| `detect_catalysts` | 14,926 | 11,219 | −25% |
| `analyze_ticker` | 8,832 | 7,030 | −21% |
| `watch_positions` | 7,926 | 6,746 | −15% |
| `list_positions` | 4,114 | 2,746 | −34% |
| **合计** | **157,609** | **110,013** | **−31%** |

而且这是**在同时加进四块新数据之后**的结果。省下来的全是格式，不是信息。

* **修复 (Fixed)**: 小数一位没截。`"close": 142.229995727539` 这种，一个字段
  白占 10 个字符。自选那个工具里有 1,224 个，白占 11,310 字符——占它总量的
  17%；选股里 139 个占 9%。新模块 `tools/compact.py` 统一处理，
  **每个字段保留几位要逐个写**：库里 `pct_chg` 是百分点（2.61 = 涨 2.61%），
  而同一张表的 `dist_ma20_pct` 是小数（0.0443 = 4.43%），后者截 2 位就变成
  0.04，精度全丢。`avg_cost` / `shares` 故意不截——那是用户自己填的数字。
* **修改 (Changed)**: 去掉 `indent=2`。缩进是给人看的，这些 JSON 只有 Claude
  读，实测占 14%。tools/ 下 12 处一起去掉。
* **修改 (Changed)**: 时间戳只留到天或分钟。原样是
  `2026-08-29 10:30:31.444795+00:00`——看自选没有人需要知道微秒和时区偏移。
* **修改 (Changed)**: 新闻的压法搬到 `core/catalyst.py::slim_news` 两边共用。
  去掉英文标题副本（同一句话的另一个语种）、把那串票代码数组换成一个计数
  （有用的是「挂了几只票」这个数字，代码本身没用）。一条新闻 422 → 218 字符。
  **这套压法本来只长在 `review_positions` 里**，`analyze_ticker` 没有——
  同一件事一个工具做了另一个没做。
* **修改 (Changed)**: 国会交易改成「汇总 + 最近三笔」。原来 AAPL 近一年 11 条
  1,898 字符、占整只票体检的 21%，比小时线整块还大，而它只是个加分项。
  11 条大多是不同议员各买一两万美元的同质记录，有用的是买卖方向。
  分类时查了一遍库里 `txn_type` 的实际取值：Sell 464、Buy 450、**salo 51**、
  Exchange 6，外加两条明显是脏数据的长字符串。`salo` 认不出是买是卖，
  硬归到任何一边都是编数据，所以两边都不算——**`buy_count + sell_count`
  可能小于 `count`，差额自己说话**。
* **修复 (Fixed)**: ★ `is_market_wide()` 只认 `tickers` 数组 ★
  新闻压过之后那个数组就没了，`len(None or [])` 一路算出 0——**压过的新闻会
  全部被判成「个股专属」**，最该降权的大盘综述反而成了打分依据。现在两种
  形态都认。这个 bug 是在把压法搬去共用时撞出来的，原来只是没人这么用。
* **修改 (Changed)**: 空值字段**故意不省略**。实测只省 1%（自选 155 行里
  空值总共 19 个），代价却是返回体形状随数据变化，调用方每读一个字段都得
  先判断在不在。不值这个价。

#### 二、四张表天天在写，这边一行都没读过

对着 `tt-data-hub` 写的表和本仓库查的表做了一次差集，漏下四张（今天的数据都在）：

* **新增 (Added)**: `core/options.py`——期权和资金面的取数层。本仓库在这一块
  原来只有「大单往哪个方向砸了多少钱」和伽马墙，缺三样做期权的人一定会先问的：
  * **期权现在贵不贵**（`uw_screener_daily` 的 IV 排名，0~100）。同一个看多
    判断，期权贵的时候该用正股进、便宜的时候才该买期权，方向正好相反。
  * **今天的期权量是平时几倍**（`uw_options_volume_daily`）。
  * **有没有人在建新仓**（`uw_oi_change_daily`）。这条最要紧：成交量大可能只是
    原来的人在平仓了结，**未平仓合约数涨了才说明真有新的钱押进来**，
    原来那份逐笔异动数据分不出这两种。实测 NVDA：2027-01-15 到期、行权价 100
    的看跌期权，未平仓从 46,501 张涨到 185,341 张（2.99 倍）。
  * **全市场高管在买还是在卖**（`uw_market_ibs_daily`）。实测近 5 天卖 1,746 笔
    共 51 亿美元、买 247 笔共 2.96 亿，卖是买的 17.3 倍。
* **修改 (Changed)**: 接进三个工具，**没有新增 MCP 工具**——每多一个工具，
  它的名字和参数说明就常年占着 Claude 的上下文，而这四块数据天然属于已有
  工具的某一栏。`analyze_ticker` 加 `iv` / `options_volume` / `oi_builds`
  三栏；`scan_swing` 给美股入选的票带上 IV 排名和期权量比；
  `detect_catalysts` 顶层加一条全市场内部人买卖。
* **量级坑写在测试里**: 这四张表的 payload 里三种量级混着放——`iv_rank` 是
  0~100 的排名、`iv30d` 是小数（0.327 = 32.7%）、`implied_move` 是**美元金额**
  （NVDA 收 224、`implied_move` 14.3，那是 6.4% 不是 14.3%）、`oi_change` 是
  **倍数**（2.99 = 涨到原来的 2.99 倍）。每一种都有一条专门盯着的用例。
* **修复 (Fixed)**: `slim_news` 压两次结果必须一样。第二次进来时 `tickers`
  数组已经没了，只看数组的话计数被清零、`market_wide` 跟着翻脸。
  现在优先读 `ticker_count`。
* **新增 (Added)**: 37 条测试（`tests/test_core_options.py` 23 条新建、
  `test_core_catalyst.py` 7 条盯新闻压缩、`test_watchlist.py` 6 条盯体积、
  `test_screening.py` 改一条精度容差——返回值现在统一截 2 位，容差从 1e-6
  放宽到半分钱，测的还是「取的是不是最新那天的价」）。全套 830 条通过。

### [2026-09-03] 美股小时线也改按需实时——上游撤了每日定时，库里那份从此不再更新

起因是 `tt-data-hub` 今天把 `us_hourly` 从每天定时改成了按需跑
（`python -m hub.run us_hourly --tickers AAPL,TSLA`），crontab 里 09:40 那行撤掉了。
那边的依据是：UW 的 1h 接口一次就给整个 3M 窗口，随时拉随时有完整历史，
每天跑写 8 万行里 98% 是拿同样的旧数据覆盖自己。

**对本仓库的后果**：库里的美股小时线从此不再自己更新，会一天比一天旧。
实测当时库里最新停在 2026-09-02 15:00（美东），再没有人会往前推。
顺带发现覆盖面也一直是缺的：库里只有活跃池那 **247 只**票有小时线，
池子外的票一根都没有——跟 A股 2026-09-01 那次的毛病一模一样。

* **新增 (Added)**: `core/uw.py` 的 `hourly()` / `parse_ohlc_1h()`——美股小时线
  的实时取数。转出来的行**形状跟库里那条 SQL 完全一样**（ts / ts_local /
  session / open / high / low / close / volume / market），所以调用方不用为
  「实时」和「库里」写两套解析，跟 `core/akshare.py` 是同一个约定。
* **修改 (Changed)**: `tools/hourly.py` 两个市场统一成**先实时、拿不到才查库**。
  实测活跃池外的 APH（库里一根小时线都没有）现在能拿到 60 根、走的是实时。
* **新增 (Added)**: 返回体里的 `source` 字段（`uw_live` / `akshare_live` / `db`）
  和退回库里时的那句提示。**这是这次改动里最要紧的一条**：库里那份不再每天
  更新之后，用旧行情算出来的支撑阻力、量比、止损位，看着跟实时算的一模一样，
  不标出来没有任何人分得清。`analyze_ticker` 也跟着在 `data_gaps` 里写一条，
  跟已有的「期权数据取自数据库」是同一个道理。
* **修改 (Changed)**: `analyze_ticker` 把小时线丢进后台线程，跟下面那几路实时
  接口并着跑。理由是实测数据：改造前整只票体检 13.5 秒，而 UW 的 1h 接口单次
  就要 12.9~14.0 秒，**串着跑直接翻倍到 26 秒**。改完实测 AAPL 6.8 秒、
  活跃池外的 APH 15.5 秒（它那次是冷的）。
* **修改 (Changed)**: 窗口固定用 3M 档。实测 AAPL 的 1M 档只有 138 根盘中线，
  撑不起这里最多 200 根的需求（那条 200 小时均线会永远算不出来）；
  而且换小窗口也省不到时间——1M 12.9 秒 vs 3M 14.0 秒，开销几乎全在代理那头。
  `tests/test_core_uw.py` 有一条回归测试专门钉住这个档位。
* **修复 (Fixed)**: 只留盘中那一档（`market_time == 'r'`）。实测 AAPL 的 3M 档
  1040 行里盘中只占 390 行，盘前 390、盘后 260——照单全收的话「最近 60 根」
  有一半是夜里那几根几千手的稀量线，均线、量比、支撑阻力全被带偏，而且跟
  库里存的口径也对不上（那边只存盘中）。
* **修复 (Fixed)**: `^` 开头的指数代码在发请求之前就挡掉。指数喂给 UW 的
  【股票】接口一律 422，明知要失败还去等十几秒纯属拖慢工具。
  （`^VIX` 在用户自选里，tt-data-hub 今天也是在源头把它挡出了活跃池。）
* **修复 (Fixed)**: 两处已经不成立的提示。取不到小时线时不能再说
  「把它加进自选就会开始采」——上游那个定时任务已经撤了，加进自选也不会有人去采。
* **新增 (Added)**: `tests/conftest.py` 默认拦掉真实的 UW 请求
  （换掉 `core.uw._fetch`，跟 AKShare 同一个待遇）。不拦的话，配了真密钥的机器上
  `tests/test_hourly.py` 里几十个「mock 掉 pg.query 再断言假数据」的用例
  会先去打一次真接口、拿回真行情把断言全打红。真要联网冒烟的用例加一个
  `live_uw` fixture 就能放行（`test_flow_alerts_smoke` 和 `scan_swing` 里那条
  「两份期权流数字必须对不上」的用例用的就是它）。
* **修复 (Fixed)**: `tests/conftest.py` **从来没进过版本库**——`.gitignore` 里
  那份放行名单只写了 `!tests/test_*.py` 和 `!tests/__init__.py`，2026-09-01
  新建 conftest 时漏了它。而 `scripts/deploy.sh` 会在服务器上再跑一遍测试，
  服务器又配着真的 `AKSHARE_BASE_URL` 和 UW 密钥——少了这道拦截，那几十条
  「mock 掉数据库再断言假数据」的用例会先去打真接口。今天把美股也接上实时
  之后，受影响的用例从 A股那三条扩大到几十条，正好撞出来。
* **新增 (Added)**: 25 条测试（`test_core_uw.py` 15 条、`test_hourly.py` 8 条
  分在两个类里、`test_analyze_ticker.py` 2 条，含一条「小时线不许改回主线程
  串行」）。全套 794 条通过、7 条跳过。
* **修改 (Changed)**: 顺手改掉一处过期的事实——gamma 墙的覆盖面。
  `tt-data-hub` 2026-09-02 修掉了「传给 UW 的日期用北京时间算」的错之后，
  `uw_gex_daily` 从每天 41 只涨到了 218 只（今天查库确认：09-01 及以前 41 只、
  09-02 是 218 只），而这边的提示还在说「只覆盖约 40 只大盘股」。

### [2026-09-02] MCP 接上公网；堵掉网页面板随隧道一起裸奔的口子

* **新增 (Added)**: MCP 服务通过 Cloudflare Tunnel 接到公网
  （`tt.smartled.cc` → `localhost:8766`），Claude.ai 网页版从此能调用这 12 个
  工具。此前隧道只开了 `uw` 和 `pro` 两个域名，8766 完全没有公网入口——
  服务一直在跑，但从 Claude.ai 那边根本够不着，表现就是「部署完了却一次调用
  都没有」。域名同时加进 `.env` 的 `MCP_ALLOWED_HOSTS`，否则 MCP SDK 自带的
  Host 白名单会回 421。
* **修复 (Fixed)**: **网页面板曾随隧道一起暴露在公网上，约 40 分钟。**
  面板和 MCP 共用 8766 这一个端口，而面板的 25 个路由一个都没有鉴权，其中
  `GET /api/mcp-url` 会把 MCP 令牌明文交给任何访问者——**MCP 那层 token 校验
  因此完全失效**，等于门锁挂在门外。同时敞开的还有 `/api/positions`（持仓成本
  和股数）、`/analyze/history`（全部历史分析），以及 `/api/positions/add`、
  `/ticker/remove`、`/analyze` 这些能改数据、烧 AI 额度的写接口。
  修法是在隧道规则上限定 `path: ^/mcp`，紧跟一条同域名的 `http_status:404`，
  公网只留 MCP 一个入口；面板回到只能局域网访问。事后轮换了 `MCP_TOKEN`。
* **修改 (Changed)**: README「四、接到 Claude.ai」、`.env.example` 的
  `MCP_ALLOWED_HOSTS` 段、`CLAUDE.md` 的 Claude.ai Integration 一节都补上了
  这条约束和隧道配置样例，并写明改隧道前要先用
  `cloudflared --config <文件> tunnel ingress validate` 和 `... ingress rule <URL>`
  自测——配置写错会把同一条隧道上的其它域名一起带挂。

### [2026-09-01] A股小时线改成按需实时，A股新闻接进消息面

* **新增 (Added)**: `core/akshare.py`——自建 AKTools 的实时客户端，只做一件事：
  A股小时线。跟 `core/uw.py` 一个规矩，取不到返回空、不抛异常。
  新配置项 `AKSHARE_BASE_URL`（不配也不会坏，只是退回库里）。
* **修复 (Fixed)**: `tools/hourly.py` 的 A股路径先走实时，拿不到才查库。
  **原来的问题**：库里的 A股小时线只覆盖活跃池（约 150 只），而活跃池的粗筛
  条件是「站上 50 日均线 + 放量」——那是在挑今天强势的票，跟「用户想看哪只票」
  完全是两回事。2026-09-01 实测**贵州茅台、五粮液、宁德时代、比亚迪库里一根
  小时线都没有**，`analyze_ticker` 那一块直接是空的。
  改完实测四只全部 60 根、1.2~1.9 秒，收盘价跟当天实时行情对得上。
* **为什么不改成全池预采**: 小时线只有一个消费者（`analyze_ticker`），而且是
  **单只票、只要 60 根**；`scan_swing` / `review_positions` / `watch_positions` /
  `detect_catalysts` 一个都不用它。新浪一次调用给约 1970 根、2~3 秒。
  为这点需求预采 1500 只（约 145 万行、每天多跑 10 分钟）不划算，而且按需的
  **覆盖面反而更大**——任何一只 A股都能查，不限于 1500 只池子。
  美股不走这条路：它的小时线来自 Unusual Whales（付费、有频率限制），
  而且活跃池已经覆盖了持仓和自选，没有这个缺口。
* **修改 (Changed)**: `core/catalyst.py` 的两条新闻 SQL 改成 `news` 和
  `cn_news` 两张表 `union all` 之后再过滤。**只查 `news` 的话 A股等于没有
  消息面**——实测 82387 条里带 A股代码的只有 218 条（0.26%），`uw_news` 里 0 条。
  写成「先合并再过滤」，参数个数跟原来一样，而且执行计划确认两个分支各自走
  GIN 索引（`idx_news_related_tickers` / `cn_news_tickers_gin`），25~42 毫秒。
* **修改 (Changed)**: 找不到小时线时的报错文案分市场。A股现在是实时拉的，
  再说「把它加进自选就会开始采」会把人引到错的地方去。
* **配套**: A股新闻由 `tt-data-hub` 的新任务 `cn_news` 采集（工作日 18:00），
  首轮入库 8956 条。分级 High 141 / Medium 880 / Low 7935。
* **修复 (Fixed)**: `tools/analyze_ticker.py` 的低置信度提示原来只数**工具层**
  的缺口（`gaps`），而 `data_gaps` 是工具层 + 评分层 + 计划层三者合并。两个后果：
  提示里说「另有 1 项依据缺失」而清单里列着 5 条；更糟的是**提示会在数据变好之后
  失灵**——2026-09-01 当天实测：早上 000001.SZ 工具层缺三四项、条件碰巧成立，
  下午 A股数据补齐、小时线改走实时之后那些缺口消失，`len(gaps)` 掉到 1，
  于是「82 分但只用上 45% 评分表」这种最该被警告的结果反而不再警告。
  现在条件和文案都用跟 `data_gaps` 完全同一个集合，并加测试钉住两者条数相等。
* **新增 (Added)**: `tests/conftest.py`——默认拦掉测试里的真实 AKShare 请求。
  A股小时线改走实时之后，**只要跑测试的机器上配了 `AKSHARE_BASE_URL`，任何一个
  拿 A股代码走 `hourly_setup` 的用例都会静悄悄发真实网络请求**，结果随行情变化
  （`TestMarketInference` 三个用例当场红了：`pg.query` 一次都没被调用）。
* **修改 (Changed)**: 两个测试的期望值算错了，不是代码错：
  `test_core_regime.py` 的手算 SQL 用「每只票最新一行」而不是「当天那一行」，
  把停牌票 002870.SZ 的 8/31 旧数据当成「今天创新高」算了进去（246 卡着正确的
  245）；`test_scan_swing.py` 的并列断言漏了 `evidence_pct` 这一级排序键，
  一直靠运气绿，A股新闻接进来后分数分布一变就露出来。


### [2026-09-01] 修「全市场最新交易日」被单独一只票带偏

* **修复 (Fixed)**: 美股筛选器此前**只返回 `^VIX` 一只票**。
  `tools/screening.py` 和 `core/snapshot.py` 都用 `max(date)` 找全市场
  最新交易日，而 `^VIX` 是指数不是股票、计算时段跟股票不同，美股还没开盘
  它就先有了一根当天数据。于是 `max(date)` 拿到 2026-09-01——那天全市场
  **只有它一只**有数据——主查询再按这一天等值筛选，别的票全被排除。
  A股同时是正常的，因为 A股池子里没有这类指数。
  判断标准改成「最新且当天票数够多」：查最近 30 天每天的票数，
  取最新的、票数达到窗口内最热闹那天一半的那一天。
* **影响面**: 不是偶发。每天从 VIX 出当日数据、到晚上 `us_daily` 跑完之间
  （一天里的大部分时间）筛选器都是坏的。`core/snapshot.py` 被 5 个场景工具
  里的 4 个依赖，所以受影响的不止筛选器。
* **新增 (Added)**: `core.snapshot.pick_trading_day()` —— 纯函数，
  两个模块共用一份判断逻辑，免得将来只改一边。配 7 个回归测试，
  含「半截写完的一天该跳过」和「正常波动不该判掉整天」两种边界。

### [2026-09-01] 部署到服务器：用户级 systemd 服务与部署脚本

* **新增 (Added)**: `scripts/tt-mcp.service`——用户级 systemd 服务文件。
  用「用户级」而不是系统级，是为了跟这台服务器上已有的 `sos`、
  `sos-autotrader`、`v5-telegram` 保持一致，而且 `tao` 已开启 linger
  （开机自动拉起用户服务），顺带省掉 sudo。
* **新增 (Added)**: `scripts/deploy.sh`——推当前分支到服务器裸仓库、
  服务器拉取、装依赖、重启服务，最后自动验证首页 200 和错误令牌 401。
* **修复 (Fixed)**: `requirements.txt` 把 `mcp` 锁到 `<2`。原来写的是
  `mcp[cli]>=1.0.0`，没有上限。mcp 2.0 把 `FastMCP` 改名成了 `MCPServer`，
  服务器全新装依赖时拿到 2.1.1，`server.py` 一导入就 `ModuleNotFoundError`，
  服务起不来；本地锁在 1.27.2 所以测试全绿，完全看不出来。
* **修复 (Fixed)**: 远程 MCP 端点此前**完全不可用**，三个原因叠在一起，
  都是第一次真正部署才暴露的：
  1. Starlette 不会自动运行被 `Mount` 进来的子应用的 lifespan，而 MCP 的
     会话管理器就在那里面启动。结果每个请求 500，报
     `RuntimeError: Task group is not initialized`。现在在组合应用上把
     子应用的 lifespan 显式接了上来。
  2. 地址对不上。`streamable_http_path` 默认是 `/mcp`，而应用又把整个
     MCP 应用 `Mount` 在 `/mcp` 下，两段路径叠成了 `/mcp/mcp`——跟
     README、CLAUDE.md 和网页 `/api/mcp-url` 交出去的地址都不一致。
     现在设成 `/`，地址回到文档里写的 `/mcp`。
  3. `Mount("/mcp")` 只匹配 `/mcp/...`，**裸的 `/mcp` 不匹配**，会掉到网页
     应用上返回 404，鉴权中间件根本没被调用（现象是错误令牌返回 404 而不是
     401）。新增 `NormalizeMcpPath` 把裸路径改写成带斜杠的，对客户端透明。
* **新增 (Added)**: `MCP_ALLOWED_HOSTS` 环境变量。MCP SDK 自带防 DNS 重绑定的
  Host 白名单，默认只放行 `127.0.0.1`/`localhost`/`[::1]`——**局域网直连
  `（内网地址，见 .env）` 和走 Cloudflare Tunnel 的 `mcp.你的域名` 默认都是
  421**。没做成关掉防护，而是做成可配置的。
* **新增 (Added)**: `tests/test_main.py` 的 `TestCombinedAppServesMCP`——
  真正起一次「网页 + MCP 同端口」的组合应用，跑一次完整 MCP 握手。
  上面这三个 bug 能一路溜到部署，就是因为此前**没有任何测试起过这个组合
  应用**，测的都是 stdio 那条路。

### [2026-08-31] 五个交易场景工具上线，MCP 工具从 10 个变 12 个

* **新增 (Added)**: `core/` 取数层——`uw.py`（UW 实时客户端）、
  `snapshot.py`（日线技术面）、`scoring.py`（形态与评分）、
  `catalyst.py`（消息面）、`fundamental.py`（基本面）、
  `regime.py`（市场环境）、`cn_signals.py`（A股信号）。
* **新增 (Added)**: 五个场景工具——`review_positions` 持仓分析、
  `scan_swing` 波段扫描、`analyze_ticker` 个股分析、
  `detect_catalysts` 突发消息侦测、`watch_positions` 持仓盯盘。
  工具内部算完直接返回结论，不调用大模型。
* **新增 (Added)**: `tests/test_sql_rules.py`——五条查询规矩和
  「短线不许碰基本面」的回归测试，扫源码，不连库。
* **修复 (Fixed)**: `config.py` 读不到 UW 密钥。`.env` 里的键名是
  `UW_T_KEY` / `UW_T_ENDPOINT`，代码里读的是 `UW_API_TOKEN`，
  一直拿到空字符串。现在两套名字都认。
* **修复 (Fixed)**: `core/snapshot.py`、`core/fundamental.py` 交出来的比率
  字段单位是百分点（`3.18` 代表 3.18%），跟数据库原始存的小数（`0.0318`）
  差 100 倍。这个量级错位此前真实导致过三处结论算错：短线三种形态判断里
  有一种因为门槛按错的量级写，永远不会触发；中线基本面评分（满分 35 分）
  有 12 分因为同样原因全场归零；给用户看的营收增速把 `+106%` 显示成了
  `+1.1%`。现在两个模块统一对外交百分点，并在 `CLAUDE.md` 里补了一节说明
  这个单位约定，防止下一个人在这里再摔一次。
* **修改 (Changed)**: 下线 `analyze_watchlist`（被 `scan_swing(scope="pool")`
  取代）和 `analyze_positions`（被 `review_positions` 取代），函数一并删除。
* **修改 (Changed)**: `get_hourly_setup` 不再单独暴露为 MCP 工具，
  逻辑并进 `analyze_ticker`。函数保留在 `tools/hourly.py`，网页面板还在用。
* **修改 (Changed)**: `CLAUDE.md` 的查询规矩从四条变五条，
  补上 `stock_ohlcv_min` 的三种周期（`1h`/`1m`/`5m`）必须带 `timeframe` 条件。

#### 最终评审修复轮（同日）

* **修复 (Fixed)**: **同一只票，两个工具给出打架的结论。** 持仓分析判断
  「跌破止损」时只看有没有站回 20 日均线，不看是盈是亏、也不看破了多久，
  结果 AMZN（浮盈 8.3%、只跌破 0.17%）拿到最高紧急度 90、文案「按纪律该
  减仓或清掉」，而同一天持仓盯盘对同一只票说的是「移动止损被触及、还在
  浮盈 204 美元、不必马上动」。现在三分类（浮盈触线 / 最近击穿 / 长期在
  止损位下方）由 `core/scoring.classify_stop_breach()` 统一判定，两个工具
  共用同一个函数、同一个「连续破位多少天」的界值，结论不可能再打架。
* **修复 (Fixed)**: **一路数据源报错就把整个工具打死。** 持仓分析、波段
  扫描、个股分析三个工具原本整个函数只有一个 try，新闻、期权流、市场环境
  任意一路抛异常，返回体就只剩一个 error，一条结论都不给——只覆盖约 40 只
  大盘股、注释自己写着「缺了不致命」的 gamma 墙也能干掉整只票的体检。
  现在每一路各自 try/except，挂了只写进 `data_gaps`，其余照常给结论。
* **修复 (Fixed)**: **「创 20 个交易日新低」漏报 96%。** 窗口把当天自己也
  算进了最低价，再拿收盘价去比——数学上只有「收盘价正好等于当日最低价」
  才成立，实测 2082 只美股命中 9 只，按真实语义应该 218 只。这是盯盘工具
  里的安全告警，而这个工具的信条是「没事就说没事」，它的沉默会被当成安全。
  A股市场宽度的「创 20 个交易日新高」是同一个 bug（155 vs 316），一并修。
  原来的回归测试用同一个错误条件挑候选票，所以永远绿，一并重写。
* **修复 (Fixed)**: **缺一半依据的评分照样打高分，且低置信度提示恰好被
  挡住。** 平安银行走中线档时基本面 35 分 + 相对强度 20 分整块被移出分母，
  剩下 45 分打出 82 分，而基本面齐全的 ORCL 只有 77 分——两个数放在一起
  会让人把平安银行排在 Oracle 前面。评分结果现在带 `evidence_pct`（这个分
  用上了百分之多少的评分表），低于 60% 时另加一条明写的提醒；个股分析里
  那条置信度提示原来写成 `if plan and (...)`，没识别出形态时整条被跳过，
  而「没形态 + 缺一堆依据」恰恰是最该警告的一类，现在跟 plan 解耦。
* **修复 (Fixed)**: 波段扫描的评分依据（库里 5 天的期权归档）被后补的实时
  期权流覆盖到同一个键上，两者都不带标签，出现过「理由写净 −10%、旁边
  证据是净 −43.7%」。现在两份分开放、各自标明来源、时间窗和用没用来打分。
* **修复 (Fixed)**: 「大盘综述不算个股消息」的过滤只做了两个工具。NVDA 靠
  一条挂 7 只票的行业综述在波段扫描里拿到消息面满分，ORCL 靠一条挂 8 只票
  的综述把紧急度从 20 抬到 65。阈值收进 `core/catalyst.py` 只定义一次，
  五个工具共用。
* **修复 (Fixed)**: 盯盘会为 1 万美元的期权异动发告警（分位数门槛按定义
  每只票每天都有约 10% 的行满足，等于保证天天有货）。加了绝对下限 25 万
  美元（硬门槛的一半，按真实分布定），低于它整条不发。
* **修复 (Fixed)**: 「底部反转」不要求价格真在底部，把离 52 周高点只有 6%
  的 MSFT 标成底部反转（全市场命中 353 只，其中多只贴着 52 周新高）。
  补两条判据：离 52 周高点至少 15%、且收盘价高出 200 日均线不超过 15%。
* **修复 (Fixed)**: 个股分析原来是五个工具里唯一没有 `warnings` 的，
  采集任务挂了它一个字不提；持仓分析的新闻按票复制整条原始记录；
  三个工具的价格和 RSI 没有四舍五入；波段扫描的 `as_of` 取第一只票的日期
  （停牌票排在首位会给整批结果贴错日期）、且缺 `truncated`/`returned_count`；
  盯盘在新闻标题缺失时把「None」直接印给用户。
* **修改 (Changed)**: 波段扫描的财报排雷窗口从 3 改成 5——模块文档声明的是
  「财报 3 个交易日内直接剔除」，而 `days_until` 是自然日，3 个交易日跨周末
  是 5 个自然日。三个工具的财报窗口为什么不一样，理由写进了常量注释。
* **修改 (Changed)**: `CLAUDE.md` 里「`sector` 大约覆盖一半」改成实测数字
  ——34683 行里只有 2020 行有值（5.8%），A股一行都没有。

### [2026-08-30] A 股四张信号表已建好，设计文档第五、六节改写

* **修改 (Changed)**: A 股信号表已在 `tt-data-hub` 建好并挂上定时（那边提交
  `26ad54e`），不再是「待补」。原计划列的 10 张待建表，实际动手时发现同花顺
  的接口**一次给全市场**，原来要逐票调 1500 次的活变成一次请求，于是 P0 和
  P1 合并成 4 张表、2 个任务：`cn_fund_flow_daily`（个股资金流，5208 只）、
  `cn_board_flow_daily`（行业 90 + 概念 387 个板块）、`cn_limit_pool_daily`
  （涨停板）、`cn_lhb_daily`（龙虎榜）。「板块轮动」因此白捡——不用先建成分股
  表再汇总。
* **修改 (Changed)**: 第十节分期说明更新——第二期不再被数据卡住，可以接着
  第一期做。
* **修复 (Fixed)**: 记下两个影响下游查询的单位口径问题（采集端已统一处理）：
  板块资金流原始单位是**亿元**而个股资金流是「1.04亿」这种字符串，库里统一
  存**元**；同花顺把股票代码当**数字**返回，库里统一存 `002190.SZ` 这种补过
  前导零的标准格式。
* **修改 (Changed)**: 第六节「挂掉的接口」一节改写——**判据不是 `_em` 后缀**。
  东财的「全市场排名/实时快照」类全挂，「按日期查明细」类正常，新浪/百度/
  同花顺全部正常。按后缀一刀切会挡掉龙虎榜和涨停板，而这两个正是在用的。

### [2026-08-30] 设计文档补充数据来源策略，核对数据就绪情况

* **新增 (Added)**: 设计文档第二节加入「什么走数据库、什么走实时 UW」的分工表。
  实测 UW 代理接口耗时 8~12 秒、限额剩余百万次/分钟——**约束是延迟不是限额**。
  全市场扫描 2082 只美股走数据库 0.1 秒、走 UW 约 15 分钟，故批量扫描留在数据库；
  持仓盯盘改为**实时 UW 为主**（数据库里的期权异动每天只灌两次，做盯盘名不副实）。
* **新增 (Added)**: 记下一个实现必踩的坑——**UW 代理挡在 Cloudflare 后面，请求头
  不带浏览器 User-Agent 会被 403 拒掉**（Error 1010），与 token 无关。
* **修改 (Changed)**: 第六节「美股待修」按 2026-08-30 实况重写。`us_news` 采集已
  修复；小时线覆盖此前判断有误——155 只自选中 154 只、6 只持仓全部都有小时线，
  实际够用。新增记录 `cn_fundamentals` 的字段溢出 bug、`us_universe` 熔断阈值过紧。
* **修改 (Changed)**: 第十节标注数据就绪情况——第一期所需美股数据全部到位可以开工，
  第二期所需的 10 张 A 股表当前一张都未建。

### [2026-08-29] 五个交易分析 skill 的设计文档

* **新增 (Added)**: `docs/superpowers/specs/2026-08-29-五个交易分析skill设计.md`。
  把项目从「10 个通用工具」重做成 5 个围绕实际交易场景的 MCP 工具：持仓分析、
  波段扫描（盘后）、个股分析、突发消息侦测、持仓盯盘。工具内部把结论算完直接返回，
  不再由服务端调大模型生成报告。底下抽出 `core/` 五个可复用取数模块。
* **新增 (Added)**: 文档第三节给出**短线与长线的数据分界表**——短波段只看价格走势、
  资金流向和有没有雷，市盈率、营收增长、毛利率、分析师目标价一律禁用，且在代码里
  按档拆分评分函数，写错会直接报错而不是悄悄用上。
* **新增 (Added)**: 文档第六节是给 `tt-data-hub` 的 A 股补数清单。实测了局域网
  AKTools 服务（AKShare 1.18.64）的十余个接口，按 P0/P1/P2 列出 9 张待建表和字段，
  并明确列出**不采**的品类（债券、外汇、加密货币、期货、Tick 数据、宏观指标、
  港股等）及理由。
* **修复 (Fixed)**: 排查发现三处与文档不符的实际情况——`stock_ohlcv_min` 里还有
  第三种周期 `5m`（美股 98 万行，原文档只写了 `1h`/`1m`）；美股盘前盘后分钟数据
  近 5 天只剩 18 只票（历史曾覆盖 301 只）；`us_news` 采集任务当日为 `failed`。
  已作为待修项写入设计文档。

### [2026-08-29] README 与 .env.example 重写，补回自选池漏掉的两只票

* **修改 (Changed)**: **`README.md` 整篇重写**。旧版还在讲 yfinance + Finnhub 采集、
  本地 DuckDB 文件、`--update` / `--cron` 参数、9 个 MCP 工具——这些东西现在一样都
  不存在了，照着它做没有一步是对的。新版开篇就讲清楚数据在哪、谁写的：一张
  「采集端 tt-data-hub 写 → 自建 Postgres → 本项目只读消费」的图，两张表格分别列
  **只读的 9 个数据源**（每张表的规模、装什么、坑在哪）和**本项目只写的 3 张表**，
  再加一张「不在数据库里的配置」（模型清单、密钥、提示词、语言各自在哪个文件）。
  另外把写查询必须守的四条规矩、10 个 MCP 工具、6 个网页页面、目录结构都更新到
  与代码一致。
* **修改 (Changed)**: **`.env.example` 重写**。旧版**根本没有 `PG_READ_URL` 和
  `PG_WRITE_URL`**——照着它配的人连不上数据库，却留着 `FINNHUB_KEY` /
  `DB_PATH=./trading.duckdb` / `NEWS_DAYS` 这些早已没有任何代码引用的变量
  （`tests/test_pg.py` 里有条测试专门钉住「config 里不许再出现这些名字」）。
* **修复 (Fixed)**: **自选池补回 AAPL 和 SPCX**。旧 DuckDB 的自选池有 6 只票，
  线上那 153 行来自另一套系统，比对后发现这两只线上没有。两只都在 `stocks_meta`
  里、都有日线，属于迁库时漏掉的，补进去之后自选池 155 只。
* **修改 (Changed)**: 核对旧 `trading.duckdb` 与线上库的覆盖情况，确认可以删除
  （详见下方 2026-08-29 那条里的保留说明）：9 个模型、3 条分析历史、6 笔持仓
  都已迁走；新闻的时间窗（2026-06-03 ~ 07-16，5276 条）完整落在线上 8.0 万条
  （2024-12 起）里面；日线线上是 2021-02 起、更新到 2026-08-28，比旧库的
  2024-07 ~ 2026-07 又长又新。唯一不重合的是 199 只已经掉出「市值 20 亿美元
  以上」股票池的票，选股本来就不看它们。

### [2026-08-29] 四张漏迁的表各归其位，从旧 DuckDB 抢回 6 笔真实持仓

改造成只读 Postgres 消费端时，有四张表被漏掉了——它们在线上库里**根本不存在**，
所以对应功能一直是坏的，而界面上看起来只是「没配置」。

* **修复 (Fixed)**: **从 `trading.duckdb` 里抢回 6 笔真实持仓**（GOOGL / AMZN /
  MP / RKLB / NVO / ORCL），而线上 `user_positions` 当时是**空的**。差一点就
  跟着旧库一起删掉了。迁进 Supabase 之后接上现价验证过整条链路：成本
  11935.6 → 现值 10961.3（−8.16%）。新表比旧的多两列 `market` 和 `currency`，
  所以汇总是**分币种**的，美股美元和 A股人民币不会被加到一起。
* **新增 (Added)**: `sql/001_analysis_history.sql` —— 建 `analysis_history` 表。
  这是本项目自己的数据（不是行情），迁库时漏了，所以「分析历史」页面一直是空的。
  `write_user` 有建新表的权限（改已有表不行——那些表属主是 postgres），
  所以这个文件不需要管理员执行。旧库里 3 条历史记录已迁入。
* **修改 (Changed)**: AI 模型配置从 `ai_models` 表搬到 `config/models.json`。
  旧库里 9 个模型配置已导出。**密钥不在 JSON 里**——每个模型只带一个
  `api_key_env` 字段指向环境变量名，实际值在 `.env`。改模型直接改文件，
  网页上的模型页改成只读。
* **修改 (Changed)**: 自定义技能提示词从 `skill_prompts` 表搬到
  `config/skill_prompts.json`。文件不存在就直接用 `skills/*.md`，第一次在
  设置页保存时才生成。「重置」= 删掉 JSON 里那一条。
* **移除 (Removed)**: 语言切换整个删掉（原来存 `app_settings` 表）。
  现在只看环境变量 `LANG_UI`。
* **移除 (Removed)**: `/update`、`/update/status`、`/update/stream` 三个端点，
  连同 `home.html` 的 UPDATE 按钮、`index.html` 的更新按钮、以及浮动的管道
  日志面板。采集搬到 `tt-data-hub` 之后这些都是假按钮，点了只会报错。
* **修复 (Fixed)**: 分析历史现在在 `/analyze/stream` 里也存。原计划只在
  `/analyze` 存，但网页上三个页面点「分析」走的**全是**流式那条，只存
  `/analyze` 等于历史页永远是空的。存历史失败只记日志，不影响分析本身
  ——结果已经返回给用户了。
* **修改 (Changed)**: 摘掉最后一个 `xfail` 标记。`stocks_meta.sector` 补到
  2020 只（选股池覆盖 2018/2121 = 95%），越过了那条测试等的门槛。补的
  过程有波折：第一轮撞上 UW 转售端点整体故障（全域 530 约 20 分钟），
  2133 只里 530 只没采到只补到 1555；并发从 6 降到 3 重跑后失败数降到 7。
  `ticker_over20b` 那段临时兜底**暂时保留**（还有约 100 只票靠它），
  另加了一条探针测试，等覆盖率到 100% 会提醒可以删。
* **修改 (Changed)**: `.env.example` 补上 `DASHSCOPE_API_KEY` /
  `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `LANG_UI` 四个键并说明用途。
* **修改 (Changed)**: `CLAUDE.md` 新增完整的数据来源结构表——什么数据、
  存在哪、谁写。本仓库只写三张表（`user_watchlist` / `user_positions` /
  `analysis_history`），行情类一律只读。

**当前状态**：9 个模型里 4 个 Claude 和 1 个 GPT **拿不到密钥**（`.env` 里没有
`ANTHROPIC_API_KEY` 和 `OPENAI_API_KEY`），设置页会给它们标红「No key」——
这是诚实显示不是故障。默认的两个（分析走 Qwen3.7 Max、情绪走 DeepSeek V4
Flash）密钥齐全，能用。

**`trading.duckdb`（277 MB）暂时保留**，等确认新的模型配置和分析历史页面
都正常再删。`migrate_from_duckdb.py` 是配套的一次性脚本，同样等删。


### [2026-08-29] 删掉 DuckDB 与全部采集代码（计划 2 的任务 6）

采集职责移交 `tt-data-hub` 之后，这个项目里的采集代码整个删掉，四个
「import 就炸」的模块全部修好。316 个测试全绿。

* **移除 (Removed)**: `data_sources/` 整个目录、`db/init.py`、`db/update.py`、
  `db/fetch.py`、`db/universe.py`、`db/fetch_universe.py`、`db/migrate.py`、
  `db/run_daily_update.bat`。`db/` 下现在只剩 `pg.py`。
* **移除 (Removed)**: `main.py` 从 180 行砍到 49 行——去掉 `--update`、`--cron`、
  整个定时线程，以及 2026-07-16 加的百炼异步情绪打分后台线程（它依赖
  `db.update`）。现在只剩 `--mcp` 和 `--port`。
* **修改 (Changed)**: `skills/__init__.py` 改连 Postgres。按代码取数时先按后缀
  把票分成美股/A股两组分别查，不会串市场；取最新交易日改成票少的按票走主键
  倒扫、扫全池的先单查该市场最新日再等值比较，原来的相关子查询全删。
  另外改成**以传进来的代码列表为驱动表**——原来 `FROM stock_ohlcv_daily JOIN
  stocks_meta` 的写法，一只票只要缺行情或缺元数据就整行消失，而自选池是用户
  自己维护的，票被数据缺失悄悄吞掉比返回一行空数据更糟。
* **修改 (Changed)**: `web/app.py` 全部重写。**凡是 `tools/` 里已有的能力，端点
  改成直接调那个函数**，不再维护第二份 SQL：持仓的增删改查、自选的增删、
  板块下拉、最新交易日、行业别名展开。`/api/positions` 现在把工具算好的
  **分币种汇总**原样透出，网页层不再把美元和人民币加到一起。
  `/screen` 因为支持的筛选条件是 MCP 工具的超集（多头排列、离均线距离上下限、
  振幅、涨跌幅区间、是否在自选池里），仍然自己拼 SQL，但复用了工具里的
  两段式取最新交易日和行业别名展开。
* **修复 (Fixed)**: `ai_client.py` 的 `get_model()` / `list_models()` 一直在查
  `ai_models` 这张表，而**线上 Postgres 根本没有它**——所以 `get_model()` 永远
  返回 None，网页里的 AI 分析是完全不能用的，表现却是「没配置模型」，会让人
  以为是自己配错了。改成直接从环境变量读（`config.py` 里本来就有
  `AI_API_BASE` / `AI_API_KEY` / `AI_MODEL` 和一套 `SENTIMENT_*`）。
* **修复 (Fixed)**: 只读连接加 `statement_timeout = 3min`。2026-08-29 实测：
  被 kill 掉的进程发出去的查询在**数据库端继续跑了 2~3 小时**（后端进程不会
  跟着客户端死），其中一条正是本项目改造前那个 `group by market` 的全表扫写法，
  一直在扫 380 万行的日线表，把整个库的 IO 拖慢，而进程列表里看不出任何异常。
  排查还多绕一圈：`write_user` 没权限终结 `readonly_user` 的连接，只能用同一个
  角色去 `pg_cancel_backend`。**写连接不加**——大批量写入正常就要跑几分钟。
* **修改 (Changed)**: `/status` 的行数改用 Postgres 自己的统计值
  （`pg_class.reltuples`）而不是真数一遍全表，首页从 1124 毫秒降到 251 毫秒，
  误差千分之四。这个数字没有任何模板在显示，不值得为它花 425 毫秒/市场。
* **修改 (Changed)**: `/ticker/{ticker}` 去掉「库里没有就现场采一份写回库」的
  回落路径——那是采集，现在归 `tt-data-hub`。有测试钉住这个端点一个字都不写库。
* **新增 (Added)**: `pytest.ini` 限定 `testpaths = tests`。根目录下未跟踪的
  `scratch/` 里有一批同样叫 `test_*.py` 的临时脚本，**在 import 阶段就去调
  Unusual Whales 接口**，不限定目录的话光收集用例就要 15 分钟。
* **修改 (Changed)**: `requirements.txt` 删掉 `duckdb` / `yfinance` /
  `pandas-ta` / `finnhub-python`（最后那个全项目一次 `import finnhub` 都没有，
  本来就是死依赖），补上 `psycopg2-binary`。
* **修改 (Changed)**: 摘掉 9 个 `xfail` 标记——它们当初标的是「等 tt-data-hub
  灌数据」，现在 A股日线 105 万行、A股小时线 14.4 万行、美股小时线 2.9 万行、
  行业分类 1555 只都到位了，这些测试变成 XPASS。摘掉标记它们才重新是守卫。
  行业分类那条保留 xfail（只补到 1555，目标 2000，因为 `meta_refresh` 那轮撞上
  UW 端点整体故障，2133 只里 530 只没采到）。
* **修改 (Changed)**: `web/templates/settings.html` 删掉「数据更新时间表」卡片、
  `home.html` 删掉「AUTO 17:30」角标——采集不在这个项目里了，留着是假按钮。

**已知未完成 / 待决定**：任务 7（部署与 Cloudflare Tunnel）没做；网页上还有
一批端点的底层表线上不存在（`ai_models` / `app_settings` / `skill_prompts` /
`analysis_history`），对应的「改语言」「自定义技能提示词」「分析历史」页面
是摆设，删还是保留待定；`tools/watchlist.py` 的 `list_watchlist` 实测 1281 毫秒
（用的是已知较慢的 `group by` 写法），`/watchlist` 页面 1414 毫秒里 1281 毫秒
花在这；`analysis.html` 的总盈亏 getter 会把不同币种加到一起，等有了 A股持仓
就会显示假数字。


### [2026-08-29] 改造为只读 Postgres 消费端（计划 2 的任务 1~5）

数据采集已全部移交独立的 `tt-data-hub` 服务，本项目退化成只读消费端
（自选和持仓的增删改除外）。五个任务由五个实施者并行完成，全部走
「先写红测试 → 改代码 → 变绿」，并做了变异测试（故意改坏实现，验证测试
真的咬得住）。新增 211 个测试，全绿。

* **新增 (Added)**: `db/pg.py` —— 唯一的数据库出入口。读走 `readonly_user`
  且连接额外设为只读事务（第二道防误写闸，实测报错从「没有权限」变成
  「只读事务里不能执行 INSERT」），写走 `write_user`。把 Postgres 的
  `numeric` 统一转成 `float`：`stock_fundamentals` 有 20 多个 numeric 列，
  psycopg2 默认给出 `Decimal`，而 `Decimal` 不能直接转 JSON，不在访问层
  处理的话上面每个工具都要各踩一遍。
* **新增 (Added)**: `tools/hourly.py` 的 `get_hourly_setup` —— 小时线买点
  工具。返回约 1.5 KB 的纯标量摘要，不含任何原始 K 线（有测试钉住：不许
  出现 dict 数组、JSON 必须小于 3000 字符）。覆盖做波段真正要问的四件事：
  现价卡在区间哪个位置、均线方向（含斜率，比「站没站上」更说明问题）、
  量能几倍（均量刻意排除最后一根，否则放量会被自己稀释）、每根平均波动
  多少钱（止损别放得比这更近）。支撑阻力用摆动高低点算，不是简单的区间
  最高最低——那才是市场真正掉过头的位置。
* **修改 (Changed)**: `tools/watchlist.py` / `positions.py` / `screening.py`
  全部改连 Postgres。占位符从 DuckDB 的 `?` 换成 `%s`；每条查询都带上
  `market` 条件（日线表现在混了美股和 A股）。
* **修改 (Changed)**: 持仓改为**分币种**汇总——美股计美元、A股计人民币，
  汇总里根本不存在跨币种的总数。
* **修改 (Changed)**: `screen_stocks` 新增 `market` 参数；行业分类暂时从
  `ticker_over20b` 兜底取（最新交易日 2082 只可筛票里 2074 只能取到，
  而 `stocks_meta.sector` 目前全为空），每次返回都带 `sector_source`
  字段明说这是临时兜底；板块名做了归一化和同义词映射（「Health Care」
  和「Healthcare」自动相等）。
* **修复 (Fixed)**: 选股查询的性能。实测对比四种取最新交易日的写法
  （日线表 275 万行）：原来的相关子查询 738 万代价 / 6750 毫秒；计划推荐的
  `group by market` 公共表达式**同样是全表扫**，18 万代价 / 375 毫秒；
  按票 LATERAL 倒扫 13 万 / 203 毫秒；最终选的两段式（先单查最新日再当
  常量传进主查询）**5541 代价 / 109 毫秒**。性能守卫盯的是「计划内最大
  节点代价」而不是顶层——顶层有 `limit` 兜着会漏掉底下藏的全表扫。
* **修复 (Fixed)**: 三处按真库实测纠正的移植错误 ——
  ① `user_watchlist` 的冲突键是 `(ticker)` 单列不是 `(user_id, ticker)`，
  按后者写会直接抛唯一键冲突；② `user_positions` 主键只有 `ticker` 不含
  `market`，`ON CONFLICT (ticker, market)` 会报「没有匹配的唯一约束」；
  ③ 时间列叫 `created_at` / `updated_at`，不是 DuckDB 那版的 `added_at`。
* **修改 (Changed)**: `config.py` 改用 `PG_READ_URL` / `PG_WRITE_URL`
  （各保留一次对旧键名 `READ` / `WRITE` 的兼容读取），新增
  `WATCHLIST_USER_ID`；删除全部采集相关配置（`DB_PATH`、`FINNHUB_*`、
  `NEWS_*`、`OHLCV_BATCH_SIZE`、`FUNDAMENTALS_STALE_DAYS`、`TICKERS_FILE`、
  `load_tickers`）。
* **移除 (Removed)**: `analyze_positions` 里那段走 `data_sources/uw_client.py`
  的调用。那个客户端从来没调通过——它写死了 `https://api.unusualwhales.com/api`，
  读的配置项 `UW_API_TOKEN` 在 `.env` 里根本不存在。文件本身没删，留给任务 6。

**已知未完成**：任务 6（删 DuckDB 与采集代码）、任务 7（部署与 Cloudflare
Tunnel）尚未开始；另外 `web/app.py` 里有一套跟 MCP 工具完全平行的 DuckDB
实现（20 处 `get_conn()`、`/screen` 端点自己拼 SQL 且不带 market 条件、
持仓端点没有币种），任务 6 一删 `db/init.py` 它就会 import 失败，需要单独
排一个任务。


### [2026-08-28] 统一数据层实施计划（两份）
* **新增 (Added)**: [`docs/superpowers/plans/2026-08-28-tt-data-hub.md`](file:///d:/Dev_project/Python_Project/tt-trading-mcp/docs/superpowers/plans/2026-08-28-tt-data-hub.md) —— 新建统一采集服务的 10 个任务，含完整代码、单元测试、试跑与验收命令。覆盖：仓库骨架与代码格式转换、数据库统一写入层与运行日志、技术指标移植（附与旧实现对拍的步骤）、yfinance 日线采集（逐只重试补齐现在每天漏的 110 只）、拆股断层检测与修复、UW 客户端与活跃池与美股小时线、A股股票池与日线（含除权漂移检测）、A股小时线与行业分类补齐、UW 快照迁移与基本面、部署与定时任务切换。
* **新增 (Added)**: [`docs/superpowers/plans/2026-08-28-tt-trading-mcp-postgres.md`](file:///d:/Dev_project/Python_Project/tt-trading-mcp/docs/superpowers/plans/2026-08-28-tt-trading-mcp-postgres.md) —— 本项目改造为只读消费端的 7 个任务：Postgres 读写分离访问层、自选/持仓/选股三个工具的移植、新增 `get_hourly_setup` 小时线买点工具、删除 DuckDB 与全部采集代码、systemd 部署与 Cloudflare Tunnel。
* **修复 (Fixed)**: 实测推翻了 spec 里两处设计并已更正 —— ① **UW 没有 `/splits` 端点（404），且 UW 的 K 线数据本身就不复权**（KLAC 在 UW 那边同样是 2411→254 的断崖），所以日线主源必须是 yfinance（实测它的历史是连续的 241→254），UW 改为只负责小时线与衍生数据；② 拆股修复方案改为「跳空体检 + yfinance 重拉历史 + 清空该票小时线」。
* **修改 (Changed)**: 补充 UW 接口的四个实测坑到 spec 4.2.1 —— 不带浏览器 User-Agent 会被 Cloudflare 拦（Error 1010）；K 线每个交易日返回盘前/盘中/盘后三行，只有 `market_time='r'` 是真的；`total_volume` 字段数值错误，只能用 `volume`；`limit`/`start_date` 参数无效，控制窗口的是 `timeframe`（1M/3M/6M/YTD/1Y/2Y/3Y/5Y，MAX 报 422）。
* **修改 (Changed)**: 发现三处 spec 之外的移植障碍并写入计划 —— 线上 `user_watchlist` 的时间列是 `created_at` 而非 `added_at` 且有非空 `user_id`；现有选股 SQL 的相关子查询在 Postgres 里是全表扫 274 万行（执行计划代价 722 万），须改写为 `latest` 公共表达式；`stock_fundamentals` 主键是 `(ticker, updated_at)`，连表须用 `distinct on` 取最新一条。
* **移除 (Removed)**: 删除已过时的 `PLAN.md` 与 `docs/supabase_uw_integration_architecture.md`（两者均声称 Supabase 在云端、`stocks_meta` 含行业分类，与实情不符）。
* **修复 (Fixed)**: 更正此前「UW 没有拆股端点」的错误结论 —— 端点存在，路径是 `/api/companies/{票}/splits`（此前误试 `/api/stock/{票}/splits` 才 404）。实测 KLAC 返回 `{"effective_date":"2026-06-12","split_factor":10.0}`，与库内断层完全吻合。拆股方案据此改为「日历判断 + yfinance 修复」两段式，弃用原先靠成交量比值猜测的启发式（降级为查不到日历时的兜底）。
* **新增 (Added)**: spec 5.5.1 给出「存本地 vs 用时再拉」的判断规则（命中「需跨股票批量筛选」「当天不采永久丢失」「每次分析都要用」任一条才存），并按此规则把 UW 全部数据分类归位：新增该存的三样（完整基本面、上市退市清单、空头比例），停掉国会交易两张表，内部人交易由每日降为周更，伽马墙从 466 行扩为活跃池每日全采；暗池、财报历史、盈利预期、季节性、机构持仓等一律不存，按需拉取。
* **新增 (Added)**: spec 5.5.2 —— 基本面改用 UW `/api/companies/{票}/profile`（51 字段）。**实测证实现有 `stock_fundamentals` 数据本身是准确的**（AAPL 的 `return_on_equity_ttm` 1.488、`quarterly_revenue_growth_yoy` 0.164 与库内完全一致，说明本就同源），更换理由是字段太少与更新太慢：字段由 18 扩至约 30，补上填充率仅 49% 的 `peg_ratio`、71% 的净利润增速，并新增前瞻市盈率、企业价值倍数、beta、52 周高低点、五档分析师人数分布等。
* **新增 (Added)**: spec 5.5.3 与 `us_universe` 任务 —— 用 `/api/companies/listings` 一次拉全量（活跃 14389 只、已退市 9449 只，含上市日与退市日），每周滚动收录新上市、标记已退市、写入上市日期。**这同时解决了每日漏采 110 只票的部分成因**：其中一些早已退市，采集程序仍在天天拉取并失败；`us_pool()` 相应加入 `delisted_at is null` 过滤。
* **修改 (Changed)**: 明确「小时线是最细的工作周期」这条边界 —— 本系统做波段、长线与持仓分析，不做日内交易，故分钟线定位为**只供手工复盘的归档数据，不建任何定时任务**。据此三处收紧：① 新增只覆盖小时线的部分索引 `idx_ohlcv_min_hourly`（小时线与 1994 万行分钟线同表，而现有唯一索引的 `timeframe` 排在第四列，按票查小时线会扫到大量分钟线行）；② 两个小时线任务的清理语句补上 `market` 条件，避免互删对方市场的数据；③ 验收清单新增第 7 条，用等值断言分钟线行数未被改动，防止哪条清理语句漏写 `timeframe = '1h'`。

### [2026-08-28] 统一数据层设计方案（美股 + A股）
* **新增 (Added)**: 设计文档 [`docs/superpowers/specs/2026-08-28-统一数据层设计.md`](file:///d:/Dev_project/Python_Project/tt-trading-mcp/docs/superpowers/specs/2026-08-28-%E7%BB%9F%E4%B8%80%E6%95%B0%E6%8D%AE%E5%B1%82%E8%AE%BE%E8%AE%A1.md)。核心内容：把散在四个项目里的采集合并成独立的命令行采集程序 `tt-data-hub`（唯一写库角色），`tt-trading-mcp` 退化为只读消费端；A股经 AKShare 并入现有表（`market='CN'`，代码格式 `600519.SH`）；新增小时线层，只采「持仓 + 自选 + 粗筛命中」的活跃池约 400 只，复用 `stock_ohlcv_min` 的 `timeframe='1h'`。含建表 SQL、任务清单、定时排期、14 步不断档切换顺序与验收清单。
* **新增 (Added)**: 实测确认 A股数据源可用性 —— 那台 AKShare 服务（AKTools 1.18.64）的**东方财富源全部返回 500**，方案改为只用新浪源：日线 `stock_zh_a_daily`、小时线 `stock_zh_a_minute`（单次 1970 根覆盖两年）、全市场快照 `stock_zh_a_spot`、市值 `stock_zh_a_gdhs`、财务 `stock_financial_abstract`。
* **修复 (Fixed)**: 定位到一个**现存数据故障**并写入修复方案 —— `stock_ohlcv_daily` 只写最新一天、从不回刷历史，导致拆股后新旧价格断裂。库内 2026 年有 115 处，KLAC（1拆10）、CRWD（1拆4）、MNST（1拆2）、MQ（4合1）等大票的均线、20/55日新高、ATR 目前全部失真。方案新增 `us_splits` 任务（UW 拆股日历为主 + 全库跳空体检兜底）并要求上线前做一次全量修复。
* **修改 (Changed)**: 纠正上一版架构文档的两处事实偏差 —— ① Supabase 是自建在局域网 `（内网地址，见 .env）`，不在云端，故 MCP 部署需配 Cloudflare Tunnel；② `stocks_meta` 实际只有 7 列，**没有 sector/industry/market**，`screen_stocks` 的板块筛选参数迁移后会直接失效，方案增加 `meta_refresh` 任务补齐。

### [2026-08-28] Supabase (PostgreSQL) 资产梳理与 Unusual Whales 全量融合架构落地
* **新增 (Added)**: 创建架构设计文档 [`docs/supabase_uw_integration_architecture.md`](file:///d:/Dev_project/Python_Project/tt-trading-mcp/docs/supabase_uw_integration_architecture.md)，系统梳理 Supabase 中 9 张核心资产表（274 万行技术面行情、3 万条基本面、8000+ 条 UW 结构化新闻流、2500 笔真实交易记录），并设计由单机 DuckDB 往 Supabase (PostgreSQL) 中央数据库的平滑迁移与融合蓝图。
* **新增 (Added)**: 创建并更新项目推进计划 [`PLAN.md`](file:///d:/Dev_project/Python_Project/tt-trading-mcp/PLAN.md)，详细拆解 API 调研、K 线深度验证、数据层迁移、增量更新与多维量化分析的实施步骤。
* **修改 (Changed)**: 完成 Unusual Whales API (adv KEY) 的实测与深度校验：确认 `1h`（覆盖 7+ 个月）、`30m`（覆盖 4 个月）、`15m` 及 `1m/5m` 的单次 2500 根 K 棒拉取能力与历史回溯参数支持，确立“大周期日线定方向 + 小周期1h/30m找买点与风控”的双周期架构。

### [2026-07-16] 阿里云百炼 (Bailian) 异步新闻情绪批推理改造
* **新增 (Added)**: `main.py` — 新增后台 `sentiment-harvester` 守护线程。当启动 Web 容器时，该线程会在后台每隔 **5分钟** 自动醒来，调用百炼 API 查询是否有完成的批量新闻打分任务，若有则自动下载并悄悄写回数据库。这保证了在服务运行期间，处理完的情绪能够被完全自动导入，实现全自动无人值守。
* **修改 (Changed)**: `db/update.py` — 重构 `update_news_sentiment()` 调度流程。从同步阻塞、多线程高频调用，改造成**非阻塞、异步分步轮询**架构。每次更新时优先检测本地是否存在活跃的 Batch ID，若百炼平台已运行完毕（status=completed）则直接下载 JSONL 并回写入库，同时删除活跃 ID；若没有活跃 ID 则筛选最新未评分新闻上传（每批限 1000 条内）并创建新的 Batch 任务存入 settings 表。
* **修改 (Changed)**: `ai_client.py` — 新增 `submit_news_sentiment_batch()`、`check_batch_status()` 与 `download_batch_results()` 接口函数，以符合百炼/OpenAI兼容 Batch 推理的 API 标准。
* **修改 (Changed)**: `ai_client.py` — 增加百炼批处理机型自动重映射。由于百炼 Batch 模式暂不支持 `deepseek-v4-flash` 模型，提交至批推理任务时模型会自动重映射/平滑降级到官方完全支持的 `qwen-plus` 运行。
* **修改 (Changed)**: `ai_client.py` — `get_model` 添加 `api_key` 的环境变量 fallback。若数据库中密钥为空（在 Web 界面尚未配置），程序会自动回退到本地 `.env` 中的 `SENTIMENT_API_KEY`/`AI_API_KEY`，解决 CLI 环境因授权失败导致的 401 错误。

### [2026-07-16] Finnhub API 速率优化 — Fundamentals 更新提速约 4 倍
* **修改 (Changed)**: `data_sources/us_market.py` — 新增 `FinnhubRateLimiter` 线程安全单例限速器，替代每次调用后固定 `time.sleep(1.1)` 的粗放策略。限速器在请求前 throttle，自动扣除 HTTP 请求耗时，Finnhub 配额利用率从 ~45% 提升至 ~90%（实际吞吐量翻倍）。
* **修改 (Changed)**: `data_sources/us_market.py` — `fetch_fundamentals()` 新增 `light=True` 模式：仅拉 metrics+profile（2 次 API），跳过 earnings calendar 和 insider transactions（省 2 次 API），适用于 universe tier 的非核心 ticker。
* **修改 (Changed)**: `data_sources/base.py` — `FundamentalsProvider` 抽象接口新增 `light` 参数。
* **修改 (Changed)**: `db/update.py` — `update_fundamentals()` 改为接收 `tickers_with_tier`（含 tier 信息），core ticker 使用完整 4 调用，universe ticker 使用 light 2 调用。预计 2169 ticker 全量更新从 ~5 小时降至 ~1.2 小时。

### [2026-07-16] UW API 集成代码审查修复
* **修复 (Fixed)**: `config.py` — UW API Token 不再硬编码为默认值，改为空字符串，防止源码泄露密钥。`.env` 中变量名从 `UW_API_KEY` 修正为 `UW_API_TOKEN`，与 config 读取一致。
* **修复 (Fixed)**: `skills/__init__.py` — `gather_context("position_review")` 中 UW Smart Money 数据（`uw_*` 前缀字段）被丢弃的 bug，现已正确合并到 LLM 上下文中。
* **修复 (Fixed)**: `tools/screening.py` + `tools/positions.py` — 未配置 `UW_API_TOKEN` 时，跳过 UW API 调用，避免 100 次无效 401 请求拖慢响应。
* **修改 (Changed)**: `data_sources/uw_client.py` — 去掉 URL query param 重复传递 token 的安全风险；`get_comprehensive_profile` 改为 `ThreadPoolExecutor` 并行调用 10 个端点（5 线程），单 ticker 从 ~2s 降至 ~0.5s。
* **修改 (Changed)**: `tools/screening.py` — Insider 数据从无意义的 `len(data[:5])` 改为区分买/卖计数（`uw_insider_buys` / `uw_insider_sells`）；`import datetime` 移到文件顶部。
* **修改 (Changed)**: `tools/positions.py` — 复用 `_extract_uw_summary()` 提取函数，消除 screening/positions 间的重复代码。
* **新增 (Added)**: `data_sources/uw_client.py` 新增 `uw_available()` 便捷方法，用于检查 UW Token 是否已配置。
* **修改 (Changed)**: `.env.example` 新增 `UW_API_TOKEN` 配置项说明。

### [2026-07-16] 接入 Unusual Whales API (Smart Money 策略)
* **新增 (Added)**: 创建 `data_sources/uw_client.py`，专门为**长线持仓**和**短线波段 (Swing)** 接入了 10 个 Unusual Whales 接口：内部人交易、国会议员持仓、机构持仓趋势、真实做空比例、FTDs、暗盘交易、月度季节性规律、波动率异常得分、期权多空立场等。
* **修改 (Changed)**: 更新 `tools/screening.py`。在 DuckDB 基础筛选后，对排名前 10 的候选股票自动拉取全套 UW "Smart Money" 和 "Options Flow" 深度数据，极大增强 LLM 的选股判断力。
* **修改 (Changed)**: 更新 `tools/positions.py` (`analyze_positions`)。分析当前持仓时，将深度整合机构资金与空头数据，帮助捕捉波段见顶或长线建仓信号。
* **修改 (Changed)**: `config.py` 中增加 `UW_API_TOKEN` 环境变量配置。

### [2026-07-13] 新增项目英文介绍文档
* **新增 (Added)**: 在 `docs/introduction_en.md` 新增极简版项目英文介绍文档（控制在600字符以内），阐述了项目的核心卖点、设计理念（"LLM as the UI"）和核心功能。

### [2026-06-23] 项目重命名 TT AI Screener + 移除 setup.py + 重写 README
* **修改 (Changed)**: 项目从 `tt-trading-mcp` 重命名为 **TT AI Screener**（`tt-ai-screener`），更新 server.py、web/app.py、main.py、manage.sh、run_daily_update.bat、settings.html 中所有引用。
* **修改 (Changed)**: 删除过时的 `setup.py` 首次安装向导。`main.py` 启动时已自动初始化数据库，数据更新用 `--update`，ticker 管理已迁移到 watchlist 工具。
* **修改 (Changed)**: 重写 `README.md`，反映当前架构：`main.py` 统一入口、Remote MCP 连接、Web Dashboard、AI 配置、完整工具列表和项目结构。
* **修改 (Changed)**: `.env.example` AI 配置改为注释（可选），注明推荐通过 Web Settings 页面配置，`.env` 仅作首次 seed。

### [2026-06-23] 品牌重命名 + Home 首页 + 共享导航 + 定时更新 + News 重试
* **修改 (Changed)**: 全站品牌从 `tt-trading-mcp` 重命名为 **TT AI Screener**，统一所有页面 title 和 header
* **新增 (Added)**: `header.html` 共享导航模板 — 所有 6 个页面 include 同一份 nav，通过 `active_nav` 变量高亮当前页，右侧区域各页面自定义
* **新增 (Added)**: `/` 路由改为 Home 首页 — 紧凑双栏一页布局：左侧更新摘要 + MCP 连接（带 Copy Prompt 一键复制），右侧 Quick Start（含 AI 模型配置引导）
* **新增 (Added)**: Quick Start 新增 **Configure AI Models** 步骤 — 引导用户在 Settings 配置 News Sentiment 和 Stock Analysis 两个 AI 模型
* **新增 (Added)**: `GET /api/mcp-url` 端点 — 自动获取当前 host 的 MCP endpoint URL（含 token），Copy Prompt 包含完整使用说明
* **新增 (Added)**: `update_history` 表 + `GET /api/update/last` — 持久化每次更新的摘要（各阶段 ticker 数、耗时、错误）
* **新增 (Added)**: Settings 页新增 Data Update Schedule — 开关 + 时间选择，支持运行时动态启停 cron 线程
* **新增 (Added)**: `GET/POST /settings/schedule` 端点 — 配置存入 `app_settings` 表
* **修改 (Changed)**: `main.py` 启动时自动读取 DB schedule 配置；支持 `apply_schedule()` 运行时切换
* **修改 (Changed)**: Watchlist 页面从 `/` 迁移到 `/watchlist`
* **修复 (Fixed)**: `update_news` 新增重试机制（max_retries=2, sleep 3s），SSL 断连的 ticker 可自动重试

### [2026-06-17] 重写 swing_screen skill（对齐 JSON 架构）
* **修复 (Fixed)**: 形态 B（均线回调买点）死逻辑 —— 内置筛选强制 `close>MA20`，原 `dist_ma20_pct` 负值区间永远命中不了。重定义为 [-0.02, 0.025] 浅回调，并注明负值仅在 screener 源出现。
* **修改 (Changed)**: 去掉 prompt 内全部 SQL，改为「系统投喂 JSON、AI 只做分析」的写法，与 Prompt Design Guide 的设计规则对齐（cn + en 两版同步）。
* **新增 (Added)**: swing 量化评分 rubric（形态/量能/动量/情绪/风险 共 100 分）+ A/B/C 评级阈值；财报风险与超买强制封顶 B。
* **新增 (Added)**: 止损/目标用 `atr_pct` 折算为具体价位（MA50 或 2×ATR 止损、3×ATR 或前高目标）；新增数据来源差异说明（built-in 已预筛 vs screener 未过滤需自检趋势）。
* **修改 (Changed)**: Prompt Design Guide（`settings.html`）的 Swing 段不再写「Same fields as Long-Term」，改为列出内置 swing 实际投喂的字段，并注明 built-in（仅技术字段）与 screener（含完整基本面、未预筛趋势）两种来源的差异。

### [2026-06-17] i18n + custom skill prompts + Prompt Design Guide
* **Added**: EN/CN dual-language skills (`skills/en/`, `skills/cn/`)
* **Added**: `skill_prompts` DB table — per-skill per-language custom prompt storage
* **Added**: Settings page: Skill Prompts section with inline editor, save/reset/view-default per skill
* **Added**: Prompt Design Guide — one-click copyable meta-prompt with full data schema, field descriptions, value ranges, JSON examples, and design rules; users paste to any AI to generate custom prompts
* **Added**: `GET/POST /settings/prompts`, `POST /settings/prompts/reset` API endpoints
* **Added**: `app_settings` DB table + `GET/POST /settings/lang` API for language preference
* **Added**: Language selector on Settings page (English / 中文 toggle)
* **Changed**: `load_skill_prompt()` priority: DB custom > file default, with `lang` param
* **Changed**: Analysis endpoints dynamically switch response language based on setting
* **Changed**: `.skills/*.skill` ZIP files rebuilt with English content for MCP/Claude Desktop

### [2026-06-17] Analysis 页面 + 持仓管理
* **新增 (Added)**: `/analysis` 页面 — 3 个 Skill 分析卡片（Long-Term / Swing / Position Review）
* **新增 (Added)**: 持仓管理表格 — 添加/删除持仓，自动从 DB 获取当前价、计算盈亏百分比和金额、市值汇总
* **新增 (Added)**: `user_positions` 表 — 存储用户持仓（ticker, avg_cost, shares）
* **新增 (Added)**: `GET /positions` 端点 — 返回持仓列表，含实时价格和 P&L 计算
* **新增 (Added)**: `POST /positions/add` / `POST /positions/remove` 端点
* **修改 (Changed)**: 三页导航栏统一为 Watchlist / Screener / Analysis

### [2026-06-17] 统一入口 main.py + 实时更新进度
* **新增 (Added)**: `main.py` — 统一入口，支持 `--web`（Web only）、`--mcp`（MCP only）、默认 Web+MCP 同时启动
* **新增 (Added)**: `--cron HH:MM` 参数 — 每日定时自动更新数据（跳过周末）
* **新增 (Added)**: `--update` 参数 — 一次性运行数据更新
* **新增 (Added)**: `GET /update/stream` SSE 端点 — 实时推送数据更新进度到前端
* **新增 (Added)**: `GET /update/status` 端点 — 返回当前更新状态（running/stage/log）
* **修改 (Changed)**: `POST /update` 改为进程内 threading 运行（不再用 subprocess），支持状态追踪
* **修改 (Changed)**: `db/update.py` 全部 `print()` 替换为 `_log()` 共享状态机制，支持 web 实时读取
* **修改 (Changed)**: Watchlist 首页新增更新进度面板，实时显示 OHLCV/Fundamentals/Analyst/News/Sentiment 各阶段进度

### [2026-06-17] AI 分析引擎 + Skills 重写 + 情绪分析
* **新增 (Added)**: `ai_client.py` — OpenAI 兼容 AI 客户端，支持情绪分析和 Skills 分析两个独立 API 配置
* **新增 (Added)**: `skills/` 目录 — 重写 3 个 skills（longterm_screen, swing_screen, position_review），去除 market_regime_history 依赖，直接用本地 DuckDB 筛选数据
* **新增 (Added)**: `POST /analyze` 端点 — 本地调用 AI API 运行 Skills 分析，支持 longterm_screen / swing_screen / position_review
* **新增 (Added)**: `GET /analyze/status` 端点 — 返回 AI 配置状态和可用 Skills 列表
* **新增 (Added)**: `update_news_sentiment()` — 数据更新管道新增 AI 批量新闻情绪评分步骤
* **新增 (Added)**: `news` 表新增 `sentiment_score` 列（DOUBLE），配合已有的 `sentiment_label`
* **修改 (Changed)**: `.env.example` 扩展为完整配置模板，包含 `SENTIMENT_API_*`、`AI_API_*`、`FINNHUB_TIER` 等
* **修改 (Changed)**: `config.py` 新增 AI API 和 Finnhub 会员配置项
* **修改 (Changed)**: Finnhub rate limit 根据 `FINNHUB_TIER` 自动调整（free=1.1s, premium=0.12s）
* **修改 (Changed)**: Screener 页面左侧面板 w-72→w-80，右侧详情面板 w-80→w-420px
* **修改 (Changed)**: 右侧详情面板新增 Analyst Rating / Target / MSPR / Earnings Dates 展示
* **修改 (Changed)**: 右侧详情面板新增 AI Position Review 分析按钮
* **修改 (Changed)**: 导航栏新增 Long-Term / Swing AI 分析快捷按钮（仅在 AI 配置后显示）
* **修改 (Changed)**: `.skills/*.skill` ZIP 文件同步更新为去除 market_regime 依赖的版本

### [2026-06-17] Fundamentals 数据补全：分析师、财报日期、内部人交易
* **新增 (Added)**: `analyst_rating`、`analyst_target_price`、`analyst_count` 字段，数据来自 yfinance
* **新增 (Added)**: `next_earnings_date`、`last_earnings_date` 字段，数据来自 Finnhub earnings calendar API
* **新增 (Added)**: `mspr`（内部人净买入比率），基于 Finnhub Form 4 insider transactions 原始数据自主计算 90 天滚动 MSPR
* **修改 (Changed)**: `FinnhubFundamentalsProvider` 重写为混合数据源（Finnhub metrics/profile + yfinance analyst + Finnhub earnings/insider），每 ticker 5 次 API 调用
* **修改 (Changed)**: `update_fundamentals` SQL 扩展为 18 字段完整写入，与老项目 TTAiTradingSystem 数据结构对齐

### [2026-06-17] 新增前端股票筛选页面
* **新增 (Added)**: `/screener` 页面，支持按板块、趋势（MA200/MA50/均线多头排列）、RSI、距MA20距离、成交量比率、ATR波动率、近期涨跌幅等多维条件筛选全部 2150+ 支股票
* **新增 (Added)**: 筛选结果表格支持列排序、CSV 导出、点击行查看个股详情（Fundamentals + 新闻）侧边栏
* **新增 (Added)**: `_get_all_sectors()` 辅助函数，从 `stocks_meta` 获取全量板块列表
* **修改 (Changed)**: 首页和筛选页统一导航栏，支持 Watchlist / Screener 页面切换

### [2026-06-17] 数据管道架构重设计
* **新增 (Added)**: Provider 抽象层 (`data_sources/base.py`, `data_sources/us_market.py`, `data_sources/registry.py`)，支持按市场字段插拔数据源
* **新增 (Added)**: `stocks_meta` 表新增 `market` 和 `tier` 字段，支持 core/universe 分层
* **新增 (Added)**: CSV 股票池导入脚本 (`db/universe.py`)，支持导入数千支 ticker
* **新增 (Added)**: Web 筛选端点 `POST /screen`（按板块+技术条件本地 DB 筛选）和 `GET /ticker/{ticker}`（cache-first 详情）
* **新增 (Added)**: Windows 每日更新批处理脚本 (`db/run_daily_update.bat`)
* **修改 (Changed)**: `db/update.py` 重写为批量 OHLCV 下载 + 7 天 fundamentals 缓存 + tier 门槛新闻拉取
* **修改 (Changed)**: `db/fetch.py` 重写为基于 Provider 抽象层的初始数据拉取
* **修改 (Changed)**: `config.py` 新增 `FUNDAMENTALS_STALE_DAYS`、`NEWS_MOVER_PCT_THRESHOLD`、`NEWS_MOVER_VOLRATIO_THRESHOLD`、`OHLCV_BATCH_SIZE` 配置项

### [2026-06-11] 更新 OpenClaw 配置文件
* **修改 (Changed)**: 修改了 [openclaw.json](file:///D:/Dev_project/Python_Project/tt-trading-mcp/openclaw.json)，移除了已失效或不再使用的 `minimax` 与 `tencent` 提供商及其关联模型，新增了阿里云 `aliyun` 提供商，配置了 DashScope 兼容接口及 API Key，并增加了 `deepseek-v4-flash`、`deepseek-v4-pro`、`qwen3.7-plus`、`qwen3.7-max` 模型支持，同时将默认主模型（primary）设为 `aliyun/qwen3.7-plus`。

### [2026-06-11] 配置本地环境变量
* **新增 (Added)**: 创建了本地 [`.env`](file:///D:/Dev_project/Python_Project/tt-trading-mcp/.env) 配置文件，配置了 `FINNHUB_KEY`、`DB_PATH` 以及 `NEWS_DAYS`，完成项目运行所需的凭证配置。

### [2026-06-11] 新增并校正 Gemini 指南
* **新增 (Added)**: 创建了 [GEMINI.md](file:///D:/Dev_project/Python_Project/tt-trading-mcp/GEMINI.md)，提供关于本仓库系统结构、真正实现的 4 个 MCP 工具、DuckDB 数据表模式以及 Gemini 平台的优化和编码规范。
* **修改 (Changed)**: 基于对真实 codebase（[server.py](file:///D:/Dev_project/Python_Project/tt-trading-mcp/server.py)、[config.py](file:///D:/Dev_project/Python_Project/tt-trading-mcp/config.py)、[db/init.py](file:///D:/Dev_project/Python_Project/tt-trading-mcp/db/init.py)）的深入排查，在 [GEMINI.md](file:///D:/Dev_project/Python_Project/tt-trading-mcp/GEMINI.md) 中剔除了从 `CLAUDE.md` 继承而来的 SQLite 和多余占位工具等错误信息，校正为真实的 DuckDB 数据架构。
