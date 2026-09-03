# -*- coding: utf-8 -*-
"""工具 5：持仓盯盘——我的持仓上有没有需要立刻处理的信号。

## 没事就说没事

不触发就返回空的告警列表加一句「N 只持仓，无异常」。给一堆看着热闹但不用管
的东西，等于没有盯盘——用户会开始忽略它，然后真出事的那天也一起忽略了。

所以门槛定得高：期权异动要单笔溢价 ≥ 50 万美元，或者 score 进这只票近 30 天的
前 10%（实测 NVDA 和 AMZN 的 90 分位都是 60 分）。

## 期权异动按票汇总，不按笔数扇出

**这条是拿真实数据实测出来的，不是猜的。** 用现有 6 只美股持仓、默认
`hours=24`（往回看 2 天）实测：仅 AMZN 一只票就有 39 笔交易过了「单笔
≥ 50 万美元」这条线，8 天窗口下 AMZN 一只票涨到 54 笔。如果一笔对应一条
告警，光期权这一路就能刷出小一百条，跟同目录 `detect_catalysts.py` 那次
「一条新闻按票数扇出成 N 条」是同一类问题，也正撞上这个工具自己的第一条
设计原则——「没事就说没事」，不是「有点动静就说」。

所以这里改成**一只票一条**：把过线的交易按票分组，数出看涨看跌各几笔、
各自的溢价合计、以及金额最大的那一笔，合成一条告警。哪边溢价合计大就按
哪边定调（`big_bullish_flow` / `big_bearish_flow`）。这样「AMZN 这两天
期权很热」只出现一次，而不是同一个结论用 39 种措辞重复 39 遍。

UW 实时警报（`/api/alerts`）同理：同一只票在 100 条里触发好几条自定义规则
时，也合并成一条，把命中的规则名都列出来，不按规则条数扇出。

## 每笔取数是按票取的，不是全局截断（评审 I3）

`flow_alerts_archive` 查询早期写法是「全局按溢价倒排取前 200 行」，2026-08-31
评审实测：6 只持仓近 2 天有 251 行、近 8 天有 719 行（AMZN 一只就占 239 行），
8 天窗口下 519 行被截断丢掉，而且截断是按金额排的——**小额但确实过线的行会
被挤掉，导致「N 笔合计多少」这些汇总数字不是真实总量**，持仓越多、活跃度
越高，丢得越狠，而且没有任何字段告诉用户丢了多少。

现在改成按票查（`unnest` + `lateral`，跟 `core/snapshot.py` 批量取快照是
同一个模式）：每只票各自最多取 1000 行自己的数据，不会因为别的票交易活跃
就把这只票的行情挤掉。1000 这个数字纯粹是防止极端情况的安全网（实测过的
最活跃单票 8 天也就 239 行），不是预期会用到的截断点。

## 每条告警说清三件事，还要说清是哪一路测出来的

发生了什么、对我的仓位意味着什么、建议怎么处理。只说「NVDA 出现大额看跌期权」
是没用的——用户还得自己算这对手上 20 股意味着什么。

**每条告警都带一个 `source` 字段**，说明这条结论是从哪一路数据算出来的
（`price`/`earnings`/`options_flow_archive`/`cn_fund_flow`/`lhb`/`news`/
`uw_live_alert`）。这不是摆设：`options_flow_archive`（数据库里聚合的
整体期权异动）和 `uw_live_alert`（用户自己配置的实时扫描规则命中）是
两种独立的测量，同一只票上完全可能给出相反的方向结论，`source` 字段
让用户一眼看出这两条不是同一件事的重复播报（详见下面「两路期权信号
可能打架」一节）。

## 期权异动一句话里不能混两个口径（评审 I2）

早期写法「看涨 N 笔 / 看跌 N 笔」数的是 `sentiment` 字段（按在卖价/买价
成交推出来的方向），但「最大一笔（CALL/PUT…）」印的却是 `opt_type`
（合约本身是看涨还是看跌期权）——这是两个不同的口径，混在一句话里会让人
以为算错了。2026-08-31 评审实测：近 2 天 200 行里有 88 行两者不一致；
真实输出出现过「看涨 26 笔…最大一笔 362 万美元（**PUT**，行权价 270）」
这种读起来自相矛盾的句子。

现在统一：「看涨/看跌」这两个计数和「情绪判定为看涨/看跌」这句话，
一律用 `sentiment`；合约本身是 CALL 还是 PUT 单独标成「XX 合约」，
不再跟方向词混在一起说。

## 期权异动的措辞和严重度要配得上它的量级（评审 I4）

`is_big_flow()` 的门槛是「单笔溢价 ≥ 50 万美元，或者 score 进这只票近
30 天前 10%」——这条判据是 brief 给的，没有改。但**这两条门槛不是同一
量级**：前者是绝对金额，后者是相对分位数，而分位数这条按定义每只票永远
有约 10% 的行满足，等于保证每天都有货，直接跟「没事就说没事」这条设计
原则冲突。2026-08-31 评审抓到一个真实案例：RKLB 一笔 **18.98 万美元**
的交易，只是因为 `score` 排进了这只票的 90 分位就被判成「大额」，措辞
写成「有大资金在这个方向上集中押注」、严重度给到 70（比跌破 20 日均线
还高）——一笔 19 万美元不该跟一笔 362 万美元用同一种说法。

判据没有改：is_big_flow() 的门槛值和函数本身一字未动。改的是**汇总后
的措辞和严重度**：一只票汇总的总金额（看涨看跌合计）如果连 50 万美元
这条硬门槛都没到（说明它是单靠分位数这条软门槛过线的），就不再说「大
资金押注」，改成如实的「量级不算大，只是排进了近期活跃度前 10%」，
严重度也大幅降档（见 `OPTIONS_FLOW_SMALL_*_SEVERITY`）。

## UW 实时警报的坑

`/api/alerts` 返回的 `symbol` 是**期权合约代码**（`NVDA261002P00190000`），
不是股票代码；标的藏在 `meta.underlying_symbol` 里。而且 100 条里有 14 条是
`potus_schedule` 这种噪音，`symbol` 直接是 null。拿 symbol 直接跟持仓比对
会一条都对不上，必须先用 uw.underlying_of() 解出标的。

这个接口不像 `flow_alerts_archive` 那样直接给 `sentiment` 字段，**不代表
它没有方向可判断**——2026-08-31 实测：`meta` 里有 `ask_volume` / `bid_volume`
（这批合约成交量里，多少手是在卖价成交、多少手是在买价成交），`symbol` 里
的 OCC 合约代码能解出这是看涨还是看跌期权（到期日后紧跟着的那个字符，
`C`=call、`P`=put）。这两样凑在一起，跟 `core/uw.split_premium()` 判断
`flow_alerts_archive` 里期权异动方向用的是**同一套逻辑**：在卖价成交=
买方主动，在买价成交=卖方主动；买看涨=看多、卖看涨=看空、买看跌=看空、
卖看跌=看多。所以这里直接复用 `split_premium()`，不用自己另造一套判断。

早期写法图省事，不管三七二十一把这条来源的告警一律标成 `big_bullish_flow`——
这不是「少给了信息」，是**告诉用户一个可能错的方向**：2026-08-30 实测命中
的两条都是 `Stealth Accumulation Scanner`，解出来一看，AMZN/GOOGL 那两条
的合约都是 **PUT**，而且 `ask_volume` 远大于 `bid_volume`（买方在追价买入
看跌期权），按上面那套逻辑读出来是**看空**，原来的写法却报成了「看多」——
方向整个反了。现在按合约代码解方向，解不出来（比如合约代码格式不对、或者
`ask_volume`/`bid_volume` 缺失/相等分不出谁占优）就**不猜**，改成「有大额
异动但方向判断不了」，并把这一路的这个空缺写进 `data_gaps`。宁可说不知道，
不许猜一个方向说得像知道——那样错的代价，是用户按「有资金在看多」去加仓，
实际那批钱在看空。

## 两路期权信号可能打架，打架时不能装作没这回事

`big_bullish_flow` / `big_bearish_flow` 这两个 kind 可能来自两条完全不同
的测量：

1. `options_flow_archive`——数据库里聚合的 `flow_alerts_archive`，看的是
   这段时间（`hours` 参数决定）**整体权利金往哪边压**。
2. `uw_live_alert`——用户自己配置的 UW 实时扫描规则命中的**单笔大单**。

这两路**合法地可以方向不一致**——不是 bug，一个是统计口径，一个是单笔事件
口径，本来就可能各说各话。但如果原样把两条方向相反的结论并列摆出来
（2026-08-30/31 实测：AMZN、GOOGL 都命中过这个场景——整体权利金流向偏多，
但 UW 扫描器抓到的单笔大单偏空），交易者要么无所适从、要么只信先读到的
那条；而且两个 kind 各自的默认严重度纯粹是历史遗留的分档，**不代表哪一路
更权威**，把它们并列摆出来还会让人误读成「看空那条更重要」。

所以两路信号方向不一致时，**合成一条 `options_signal_conflict`**，把双方
原始结论都摆出来，并且明说「这是两种独立测量，谁也不比谁权威，分歧本身
就是信息」——不替用户选边，也不用一个具体的严重度去暗示某一边更可信
（见 `_conflict_alert()`）。方向一致时两条照常都发，只是各自带上 `source`
字段。

## 实时接口挂了也要能用，某一路挂了也不能连累别的路

价格触发（跌破均线、跌破止损、创新低）和财报临近都是从库里算的，
不依赖任何外部接口。UW 那两路（期权异动、实时警报）取不到就在
`checked_sources` 里少一项，其余照常给——**每一路各自 try/except**，
出错写进 `data_gaps`，不会因为一路挂了把另一路已经拿到的结果一起丢掉。
同目录另外四个工具（`review_positions.py` / `detect_catalysts.py` /
`scan_swing.py` / `analyze_ticker.py`）现在也都是这个约定——评审 C2 之前
这句话是假的，那三个被点名的工具一个都没做，任何一路取数抛异常整个工具
就只返回 `{"error": ...}`，一个结论都不给。**`checked_sources` 只在这一路真正跑完（查询和处理都没
报错）之后才追加**——早期写法把 `sources.append(...)` 放在查询之前就
执行，查询哪怕挂了、`checked_sources` 里照样有这一项，会跟 `warnings`
里同时报的失败信息自相矛盾（评审 M8）。

行情新不新鲜、采集任务有没有失败或者只跑一半，也在这里查一次并写进
`warnings`——跟前面几个工具的约定一致，「行情是新的但某个采集任务挂了」
这种情况必须无条件报出来，不能挂在「行情陈旧」判断下面变成潜台词。

## 止损告警：浮盈还是浮亏、跌破多深、破了多久，都要说清楚

`position_stop()` 的止损位是「成本-2×ATR」和「20 日均线」取高的那个——均线
在成本上方时说明有浮盈，止损会抬到均线护住。这带来三个坑，都是拿真实持仓
（AMZN/GOOGL/MP/NVO/ORCL/RKLB）实测跑出来才发现的（2026-08-30/31）：

**坑一：浮盈还是浮亏，文案要跟真实盈亏方向一致。** 早期写法不分青红皂白
一律印「浮亏 {数字}」，AMZN 当天明明浮盈 204 美元（成本 246、现价
266.43），被印成「浮亏 204 USD」——把赚钱的仓位说成在亏钱。现在按盈亏
正负分别措辞，数字一律取绝对值（不再出现「浮亏 -184」这种双重负号）。

**坑二：「止损位是均线、仓位还在浮盈」跟「止损是成本减 ATR、仓位已经亏钱」
是两件性质完全不同的事，不能用同一个严重度。** 拆成 `stop_breached`（真
击穿，仓位已经浮亏）和 `trailing_stop_touched`（还在浮盈，只是碰到了护
利润的移动止损线），严重度按跌破幅度分档，界值用真实数据的分布定的
（见 `STOP_BREACH_DEEP_PCT`/`TRAILING_TOUCH_DEEP_PCT`）。

**坑三（评审 C1，最严重）：破位「什么时候发生的」这个维度，之前完全没有。**
2026-08-31 评审用真实数据独立算出：MP 已经连续 **58 个交易日**在止损位
下方（上次站上是 2026-06-04），ORCL 连续 **56 个交易日**（上次站上是
2026-06-08）——但两个月前的这件旧事，这个工具**每天**都拿 `stop["breached"]`
这一个布尔值去判断，报成「现价跌破了建议止损位…按纪律该减仓或清掉」，
严重度给到最高档 95，跟「今天刚破位」的票（比如 RKLB，7 个交易日）完全
同一个待遇。这个工具叫「有没有要**立刻**处理的信号」——把两个月前的旧闻
每天当成最高优先级重播，后果是两种，都很糟：用户要么被催着在底部割肉，
要么看几天就麻木，把真正「今天刚破」的那条一起忽略掉，正撞上这个工具
第一条设计原则要避免的事。

而 `core/scoring.py` 的 `position_stop()` 自己的注释早就说清楚了：仓位深度
浮亏时，「成本-2×ATR」这一档止损位会落在市价上方——**「那个止损几周前
就破了，是历史事实，不是今天能用的价位」**，所以它专门给了一个 `forward_stop`
字段（「要继续拿的话，止损该放哪」，永远在市价下方，今天真能用）。早期
实现从没读过这个字段。

所以这里补上「破了多久」这个维度（`_consecutive_breach_days()`）：每天
用**那一天自己的** ma_20/ATR 重新算一遍止损位（不是拿今天的止损位去套
过去的价格——过去每天的均线和 ATR 本来就不一样），从最近一天往前数，
数出连续多少个交易日都是「破位」状态，直到遇到第一个「没破」的日子为止。

- **连续天数 ≤ `STOP_BREACH_RECENT_DAYS`**（15 个交易日，约 3 周）：
  这是「最近发生」的信号，维持原来的分档和措辞（`stop_breached`，按跌破
  幅度分 95/75）。
- **连续天数 > `STOP_BREACH_RECENT_DAYS`**：改成新 kind
  `stop_breached_longstanding`——严重度大幅降档（`SEVERITY[
  "stop_breached_longstanding"]`），措辞从「按纪律该减仓或清掉」改成
  如实反映情况（「这个状态已经持续了 N 个交易日，不是今天刚发生的，
  不该每天都当成新的紧急信号」），并且**不再报那个已经是历史价位的
  `suggested_stop`**，改用 `forward_stop`（今天真能用的价位）告诉用户
  「如果决定继续持有，参考止损该放哪」。

`STOP_BREACH_RECENT_DAYS = 15` 这个界值用今天六只真实持仓的分布定的：
NVO/AMZN/RKLB 在 2~7 个交易日之间，GOOGL 14 个交易日，ORCL/MP 在
56~58 个交易日——**15~55 个交易日这段区间里，今天没有任何一只票落在
这里**，阈值放在这个空档里的任何一点效果都一样，选 15（约 3 周）是因为
它是个好记的整数，不是卡着某一只票的边界拍出来的。

**这三分类跟 `review_positions.py` 是同一个函数算出来的**
（`core/scoring.classify_stop_breach()`，评审 C1）。早期两边各判各的：
那边只看「有没有站回 20 日均线」降一档，不看盈亏、不看破了多久，结果
2026-08-30 同一天同一只 AMZN（浮盈 204 美元、只跌破 0.17%），这里报
「移动止损被触及 / 严重度 40 / 不必马上清仓」，那边报「跌破止损 /
紧急度 90（最高档）/ 按纪律该减仓或清掉」——同一个用户同一天连着调两个
工具，先读到哪个就信哪个。现在分到哪一类是同一个答案，档位名称和措辞
两边仍然可以不一样（一个是「严重度」语境、一个是「紧急度」语境），
但结论不可能再打架。

`trailing_stop_touched`（浮盈中触发）不做这个降档区分——它的止损依据只有
`ma_20`（均线每天用最新数据重新算，天然不会变成「历史价位」），本身就不
存在「suggested_stop 是几个月前的旧价位」这个问题，严重度也已经在整个
量表的最低档，没有必要再拆一层。但文案里还是会带一句「已经连续 N 个交易
日在这条线下方」，让用户看得出这不是当天新发生的（信息透明，不影响严重
度判断）。

**坑四（评审 C1 顺带）：止损依据就是 20 日均线时，不要再单独发一条「跌破
20 日均线」的告警**——那是同一件事。`position_stop()` 的 `basis` 只有两种
取值：`ma_20` 或 `成本 - 2×ATR`（没有 `ma_50` 这个选项），所以这个去重
只会跟 `below_ma20` 冲突，不会影响 `below_ma50`（那是独立的中期趋势信号）。

## 突发消息：中性情绪不算「正面」，大盘综述不算个股专属，每票上限不能在过滤前生效

**中性情绪不该被硬塞进「正面」。** 早期写法是「负面否则正面」的二分法，
2026-08-30 实测抓到一个真实反例：一条 `sentiment_label=Neutral`、同时挂了
8 只票（NVDA/MRVL/GOOGL/AMZN/AAPL/MSFT/META/TSLA）的科技股综述，被报成
AMZN、GOOGL 的「重大正面消息」。现在补上 `neutral_news` 这一档：情绪中性
的重大消息照样告诉用户「出了件大事」，但不替它编一个方向。

**挂了很多只票的新闻是大盘综述，不是这只票的专属消息，直接不发这条。**
`tools/detect_catalysts.py` 已经用真实分布定过这条线：实测近 7 天 759 条
新闻，95.1% 挂在 6 只票以内，挂 7 只以上的抽查全是「大盘/行业综述」而非
个股专属消息（`MARKET_WIDE_TICKER_COUNT = 7`）。这里直接复用同一个数字和
同一条实测依据，不凭空另定一个阈值。

**每票只发一条的上限，必须在「过滤掉大盘综述」之后生效，不能在之前
（评审 M9）。** 早期实现从数据库只拉每票最新 1 条（`per_ticker=1`），
再判断是不是大盘综述——如果那唯一的一条恰好是大盘综述，这只票当天就
一条新闻告警都没有，哪怕它下面紧跟着一条真正的个股专属消息也进不来。
现在改成每票拉 3 条候选，按顺序找第一条「不是大盘综述」的发出来，跟
`detect_catalysts.py` 的一条新闻只算一个事件是不同的问题（那边关心的是
「同一条新闻别按票扇出」，这里是「过滤和限量的先后顺序」），但都是同一类
「悄悄丢掉不该丢的东西」。

新闻三档的严重度（`bad_news`/`good_news`/`neutral_news`）都在 `SEVERITY`
里给了显式值，不再走 `.get(kind, 30)` 的默认档——依据见 `SEVERITY` 定义处
的注释。

## 「阶段新低」的窗口不能把今天算进去（评审 C3）

这条告警栽过两次。

**第一次**：SQL 窗口是 40 个自然日（约 27~28 个交易日），告警文字却写
「20 日新低」——文字和窗口对不上。改成用 `row_number()` 按票分组取最近
20 根日线，不再拿自然日近似。

**第二次（更严重）**：改成 `rn <= 20` 之后，**当天自己也被算进了
`min(low)`**，然后拿 `close <= low_20` 比。数学上 `close ≥ 当日 low ≥
min(low)`，所以这个条件只有在「收盘价正好等于当日最低价、而且这个最低价
恰好是 20 日最低」时才成立。2026-08-31 实测（2082 只美股）：**这么写命中
9 只，按真实语义应该命中 222 只，漏报 96%。**

而且当时的回归测试**用同一个错误条件去挑候选票**，所以它永远绿——测试和
实现犯同一个错误，互相印证。这是一条盯盘工具里的安全告警，而这个工具的
第一条信条是「没事就说没事」，它的沉默会被用户读成「安全」。

现在的口径写死成一句话：**今天的最低价跌破了此前 20 个交易日的最低价**
（窗口 `rn between 2 and 21`，不含今天），判定拆成纯函数 `is_new_low()`
以便拿全市场真实数据直接测命中量级——命中数掉回个位数就是窗口又把当天
算进去了。告警文字把「今日最低」和「此前 20 日最低」两个数都印出来，
读的人能自己核对。

## 期权异动的时间窗口标签要跟实际查询范围对上（评审 I5）

早期写法：`days = max(1, hours // 24 + 1)`，SQL 用 `day >= current_date -
days`，但文案里直接印 `days` 这个数当成「近 N 天」。`hours=24` 时
`days=2`，SQL 实际覆盖 `current_date-2` 到 `current_date` 共 **3 个自然
日**（`day` 是自然日列，不是时间戳，`hours=24` 跟自然日之间天然有粒度
差——现在如果是上午，「过去 24 小时」有一部分落在昨天，所以往回多留
一天缓冲；这个缓冲逻辑本身没问题），但文案却写「近 2 天」，少算了一天。
现在文案直接用 `days + 1`（SQL 查询范围实际覆盖的自然日数）来说，两处
不再各自算一遍、对不上。

## 返回体大小有上限

`alerts` 按严重度排序后最多返回 `MAX_ALERTS` 条，`alert_count` /
`returned_count` / `truncated` 如实写明总共命中多少条、实际返回多少条，
不会默默截断。加了按票汇总之后，正常情况下用不到这个上限（几只持仓顶多
出几条告警），但持仓只数会变、UW 规则会变，这道上限是留给将来的安全网。
"""
import json

from core import catalyst, cn_signals, regime, scoring, snapshot, uw
from db import pg

#: 单笔期权溢价到这个数（美元）才值得打扰用户
BIG_PREMIUM = 500_000

#: 严重度。数字只用来排序。
#: `stop_breached` / `trailing_stop_touched` 不在这里——它们的严重度按跌破
#: 幅度动态算，见 `_stop_alert_severity()`，不是查这张表。
#: `options_signal_conflict` 的依据见 `_conflict_alert()`：两路期权信号
#: 打架时的不确定性本身值得关注，但不该盖过已经确凿的价格类信号
#: （止损、跌破均线），所以定在中等档，也不刻意偏向 70 或 45 哪一边。
#: `bad_news`/`good_news`/`neutral_news` 三档不能都走默认的 30 分——
#: 一条「重大负面消息」明显比一条「情绪中性、连方向都判断不出来」的消息
#: 更贴近「现在要不要处理」这个问题，`bad_news` 定得比一般期权信号
#: （45~70）低、但比价格已经跌破止损/均线（60~95）更低——毕竟消息还没
#: 反映到价格上，可能已经计入也可能根本不影响；`good_news` 对已经持有
#: 多头仓位的人来说信息量有限，不构成任何需要现在处理的理由，定得比看多
#: 的期权信号（45）还低；`neutral_news` 连方向都判断不出来，对「要不要
#: 处理」这个问题贡献最小，垫底。
#: `stop_breached_longstanding`（评审 C1 新增）：仓位仍在浮亏，但破位已经
#: 不是新信号，是持仓状态——不该跟「今天刚破位」同一档，也不该低到让人
#: 以为没事了（毕竟真的还在亏钱），定在中等档，比 `cn_outflow`/`below_ma20`
#: 略低，反映「知道就好，不用今天动」的紧要程度。
#: `uw_alert_direction_unknown`（评审 M7）：一条用户自己配置的规则确实
#: 触发了，但方向解不出来——比什么都没有的默认值（30）该更显眼一些，
#: 但明显不如已经解出方向的信号（45/70），定在 `trailing_stop_touched`
#: 浅档同一个位置（40）。
SEVERITY = {
    "below_ma50": 80,
    "earnings_soon": 75,
    "big_bearish_flow": 70,
    "cn_outflow": 65,
    "below_ma20": 60,
    "new_low": 60,
    "options_signal_conflict": 55,
    "bad_news": 55,
    "stop_breached_longstanding": 50,
    "lhb": 50,
    "big_bullish_flow": 45,
    "uw_alert_direction_unknown": 40,
    "good_news": 35,
    "neutral_news": 20,
}

#: 财报还有几个交易日算「临近」（5 个交易日 ≈ 7 个自然日；`days_until`
#: 是自然日，所以常量也写成自然日）。
#:
#: 三个工具用三个不同的数，这是故意的，理由是时间尺度不同（评审 I2）：
#:   - `scan_swing.EARNINGS_BLACKOUT_DAYS = 5`（3 个交易日）：决定
#:     **要不要新开一笔波段**，完全可以绕开，窗口小一点就够。
#:   - `review_positions.EARNINGS_SOON_DAYS = 5`（3 个交易日）：决定
#:     **手上这笔要不要在财报前减仓**，跟上面同一个尺度。
#:   - 这里 7 天（5 个交易日）：这是**盯盘**，职责是提前打招呼，给用户
#:     留出准备时间，所以窗口要比另外两个更长。
EARNINGS_SOON_DAYS = 7

#: alerts 最多返回这么多条。见模块 docstring「返回体大小有上限」。
MAX_ALERTS = 50

#: 一条新闻挂到这么多只票以上，就不是个股新闻而是大盘综述，直接不发。
#: **定义在 `core/catalyst.py`，这里只是引用**——同一批新闻数据，同一天，
#: 同一个用户会同时用到好几个工具，判据没道理不同（评审 I3：以前这里是
#: 「照抄」一个字面量 7，抄一份就多一处会走偏的地方）。
MARKET_WIDE_TICKER_COUNT = catalyst.MARKET_WIDE_TICKER_COUNT

#: 每票候选新闻拉几条，再从里面找第一条不是大盘综述的（评审 M9：
#: 拉 1 条再判断的话，那 1 条恰好是综述就会让这只票整天没有新闻告警）。
NEWS_CANDIDATES_PER_TICKER = 3

#: 止损告警严重度分档的跌破幅度界值。2026-08-30/31 用真实 6 只持仓的分布定的
#: （见模块 docstring「止损告警」一节的表格）：MP/ORCL/RKLB 跌破 10.86%~
#: 13.80%（且都是浮亏），明显该是最高档；GOOGL 只跌破 1.06%但也是浮亏；
#: AMZN/NVO 跌破 0.17%/1.69%但还在浮盈。
STOP_BREACH_DEEP_PCT = 8.0
TRAILING_TOUCH_DEEP_PCT = 3.0

#: 止损被击穿后，连续多少个交易日算「最近发生」——超过这个数就该降级成
#: 「持仓状态」，不再当「立刻处理」的信号。依据和真实分布见模块 docstring
#: 「止损告警」一节「坑三」。**定义在 `core/scoring.py`，跟
#: `review_positions` 共用同一个数**（评审 C1）。
STOP_BREACH_RECENT_DAYS = scoring.STOP_BREACH_RECENT_DAYS

#: 算「连续破了多久」时，往回查几根日线。120 个交易日约半年，MP 那笔
#: 实测 58 个交易日的连续破位在这个窗口里能完整数完，留了一倍以上余量。
STOP_HISTORY_LOOKBACK_DAYS = snapshot.STOP_HISTORY_BARS

#: 期权异动汇总后，总金额没到这条硬门槛（哪怕是靠 score 分位数这条软门槛
#: 过线的），就不该说「大资金押注」，严重度也该明显更低——分位数门槛
#: 按定义每只票永远有约 10% 的行满足，等于保证每天都有货，评审 I4 抓到
#: 的真实案例：RKLB 一笔 18.98 万美元的交易被叫成「大资金集中押注」，
#: 严重度给到 70，比跌破 20 日均线还高。看跌方向定得比看涨略高
#: （现有 `big_bearish_flow`/`big_bullish_flow` 也是 70 对 45 的相对关系），
#: 但两者都远低于「真大额」的档位。
OPTIONS_FLOW_SMALL_BEARISH_SEVERITY = 30
OPTIONS_FLOW_SMALL_BULLISH_SEVERITY = 25

#: 一只票汇总下来的期权异动总金额低于这个数（美元），**整条告警不发**
#: （评审 I5）。
#:
#: 上一轮只改了措辞和严重度，没改「发不发」——而 `is_big_flow()` 的第二
#: 条门槛是分位数，按定义每只票每天永远有约 10% 的行满足，等于保证天天
#: 有货，直接撞上这个工具「没事就说没事」的第一条设计原则。2026-08-31
#: 实测 17 条告警里有 3 条是这么来的：NVO 汇总 1.4 万美元、MP 2.7 万、
#: RKLB 20.1 万。
#:
#: 下限取 `BIG_PREMIUM` 的一半（25 万美元），依据是真实分布：拿持仓+自选
#: 的 156 只美股跑一遍，有过线行的 100 只票汇总额从 4 千到 7928 万，
#: 中位数 19.5 万；门槛放在 25 万时保留 45 只、砍掉 55 只，正好把上面
#: 那 3 条噪音全部砍掉（NVO/MP/RKLB 都在门槛下），AMZN（4331 万）、
#: ORCL（404 万）、GOOGL（106 万）照常保留。
#:
#: 为什么不直接把下限定成 `BIG_PREMIUM`（50 万）：那样「靠分位数过线」
#: 这条路就整个死了，`is_big_flow()` 的第二条判据和「量级不算大」那套
#: 措辞会变成永远走不到的死代码。留出 25 万~50 万这一段，是「值得知道
#: 但不用动手」的量级，如实说清楚就好。
OPTIONS_FLOW_MIN_TOTAL_PREMIUM = BIG_PREMIUM / 2

_POSITIONS_SQL = """
select ticker, market, avg_cost, shares,
       coalesce(currency,
                case market when 'CN' then 'CNY' else 'USD' end) as currency
from user_positions
order by ticker
"""

# 按票取（unnest + lateral），不是全局按溢价倒排截断——见模块 docstring
# 「每笔取数是按票取的」一节。1000 是防极端情况的安全网，不是预期截断点。
_FLOW_RECENT_SQL = """
select t.ticker, f.opt_type, f.total_premium, f.score, f.sentiment, f.day,
       f.strike, f.expiry
from unnest(%s::text[]) as t(ticker)
left join lateral (
    select opt_type, total_premium, score, sentiment, day, strike, expiry
    from flow_alerts_archive
    where ticker = t.ticker
      and day >= current_date - %s
    order by total_premium desc
    limit 1000
) f on true
where f.total_premium is not null
"""

_FLOW_P90_SQL = """
select ticker,
       percentile_cont(0.9) within group (order by score) as p90
from flow_alerts_archive
where ticker = any(%s)
  and day >= current_date - 30
group by ticker
"""

#: 「创 N 个交易日新低」的 N。窗口是**今天之前的** N 根日线，不含今天
#: ——见 `_NEW_LOW_SQL` 的注释。
NEW_LOW_WINDOW = 20

# 「创 20 个交易日新低」= **今天的最低价跌破了此前 20 个交易日的最低价**。
#
# 两个坑都踩过：
# 1. 窗口早期是 40 个自然日（约 27~28 个交易日），文字却写「20 日新低」，
#    对不上（见模块 docstring「阶段新低窗口」一节）。
# 2. 改成 `rn <= 20` 之后，**当天自己也被算进了 min(low)**，再拿
#    `close <= low_20` 去比。数学上 `close ≥ 当日 low ≥ min(low)`，
#    所以这个条件只有「收盘价正好等于当日最低价、而且这个最低价恰好是
#    20 日最低」时才成立——2026-08-31 实测（2082 只美股）：这么写命中
#    9 只，按真实语义（当日 low 跌破前 20 个交易日的最低 low）应该命中
#    222 只，**漏报 96%**。这是一条盯盘工具里的安全告警，而这个工具的
#    信条是「没事就说没事」，它的沉默会被用户当成安全。
#
# 现在窗口取 `rn between 2 and NEW_LOW_WINDOW + 1`（今天之前的 20 根
# 日线），当天自己的最低价单独取出来（`rn = 1`）交给 `is_new_low()` 比。
_NEW_LOW_SQL = f"""
select ticker,
       min(low) filter (where rn = 1) as today_low,
       -- 只有真的凑够 {NEW_LOW_WINDOW} 根历史日线才给结论。刚上市的票
       -- 可能总共只有 5 根日线，拿 4 天的最低价说「创 {NEW_LOW_WINDOW} 个
       -- 交易日新低」是在编。凑不够就返回 null，is_new_low() 会当成
       -- 「判断不了」直接跳过，而不是给一条听起来很确定的假告警。
       case when count(*) filter (where rn between 2 and {NEW_LOW_WINDOW + 1})
                 = {NEW_LOW_WINDOW}
            then min(low) filter (where rn between 2 and {NEW_LOW_WINDOW + 1})
       end as prior_low_{NEW_LOW_WINDOW}
from (
    select ticker, low,
           row_number() over (partition by ticker order by date desc) as rn
    from stock_ohlcv_daily
    where ticker = any(%s)
      and market = %s
) ranked
where rn <= {NEW_LOW_WINDOW + 1}
group by ticker
"""

#: 止损「破了多久」要靠这批历史行情重新按天算。SQL 和取数都在
#: `core/snapshot.py`（`review_positions` 也要用同一份，评审 C1），
#: 这里保留同名常量只是为了让已有测试和调用处不用改。
_STOP_HISTORY_SQL = snapshot._STOP_HISTORY_SQL


def _round2(value):
    """价格类数字只保留 2 位小数交出去。行情快照里的浮点数原样透传的话，
    十几位小数（比如 56.1300010681152）直接糊到用户脸上，没有必要
    （评审 M6；同 `tools/detect_catalysts.py` 的 `_round2()`）。"""
    return None if value is None else round(value, 2)


def is_new_low(today_low, prior_low) -> bool:
    """今天的最低价有没有跌破**此前** `NEW_LOW_WINDOW` 个交易日的最低价。

    拆成一个独立函数是为了能拿全市场真实数据直接测命中量级——命中数掉回
    个位数就说明窗口又把当天算进去了（见 `_NEW_LOW_SQL` 的注释）。
    任何一边缺数据就返回 False：判断不了不等于创新低。
    """
    if today_low is None or prior_low is None:
        return False
    return float(today_low) <= float(prior_low)


def is_big_flow(row: dict, p90: float | None) -> bool:
    """这笔期权异动够不够大，值不值得报。"""
    premium = row.get("total_premium")
    if premium is not None and float(premium) >= BIG_PREMIUM:
        return True
    score = row.get("score")
    if score is not None and p90 is not None and float(score) >= float(p90):
        return True
    return False


def _stop_alert_severity(kind: str, breach_depth_pct: float) -> int:
    """止损相关告警的严重度：跌破幅度越深越严重，且「止损被击穿」
    （已经在浮亏）整体比「移动止损被触及」（还在浮盈）更严重。

    界值和分档依据见模块 docstring「止损告警」一节——用 2026-08-30/31
    六只真实持仓的分布定的，不是拍脑袋。只用于「最近发生」的破位；
    「持续很久」的破位走 `SEVERITY["stop_breached_longstanding"]`，
    不再按深浅分档（模块 docstring「坑三」）。
    """
    depth = abs(breach_depth_pct)
    if kind == "stop_breached":
        return 95 if depth >= STOP_BREACH_DEEP_PCT else 75
    # trailing_stop_touched
    return 55 if depth >= TRAILING_TOUCH_DEEP_PCT else 40


def _consecutive_breach_days(avg_cost: float,
                             daily_rows: list[dict]) -> tuple[int, object]:
    """连续多少个交易日在止损位下方，以及上次站上止损位是哪一天。

    本体在 `core/scoring.consecutive_breach_days()`——`review_positions`
    要用同一份判定（评审 C1）。这里留一层同名薄封装，是为了让已有测试
    能继续 monkeypatch 这个名字来控制返回值。
    """
    return scoring.consecutive_breach_days(avg_cost, daily_rows)


def _uw_live_direction(item: dict) -> str | None:
    """从一条 `/api/alerts` 实时警报里判断方向：看涨还是看跌。

    这个接口不像 `flow_alerts_archive` 那样直接给 `sentiment` 字段，但给了
    期权合约代码（能解出 call/put）和 `meta` 里的 `ask_volume`/`bid_volume`
    （在卖价成交的量 vs 在买价成交的量）。用跟 `core/uw.split_premium()`
    判断 `flow_alerts_archive` 期权异动方向**同一套逻辑**：在卖价成交=
    买方主动，在买价成交=卖方主动；买看涨=看多、卖看涨=看空、买看跌=看空、
    卖看跌=看多。

    解不出合约类型、或者拿不到/两边打平分不出谁占优时返回 None——
    不知道就说不知道，不猜一个方向出来（见模块 docstring）。
    """
    symbol = item.get("symbol")
    # OCC 合约代码：股票代码 + 6 位到期日 + C/P + 8 位行权价，
    # 后缀（到期日+C/P+行权价）固定 15 位，第 7 个字符（索引 6）是 C/P。
    if not symbol or len(symbol) <= 15:
        return None
    tail = symbol[-15:]
    kind = {"C": "call", "P": "put"}.get(tail[6])
    if kind is None:
        return None

    meta = item.get("meta") or {}
    ask_vol, bid_vol = meta.get("ask_volume"), meta.get("bid_volume")
    if ask_vol is None or bid_vol is None:
        return None
    try:
        ask_vol, bid_vol = float(ask_vol), float(bid_vol)
    except (TypeError, ValueError):
        return None

    up, down = uw.split_premium(kind, ask_vol, bid_vol)
    if up == down:
        return None
    return "bullish" if up > down else "bearish"


def _alert(ticker, kind, what, means, action, source: str,
           severity: int | None = None) -> dict:
    return {
        "ticker": ticker,
        "kind": kind,
        "what_happened": what,
        "what_it_means": means,
        "suggested_action": action,
        "severity": severity if severity is not None else SEVERITY.get(kind, 30),
        # 这条结论是从哪一路数据算出来的：price/earnings/
        # options_flow_archive/cn_fund_flow/lhb/news/uw_live_alert。
        # 见模块 docstring「每条告警说清三件事，还要说清是哪一路测出来的」
        "source": source,
    }


def _conflict_alert(ticker: str, of_alert: dict, uw_alert: dict) -> dict:
    """期权异动统计（数据库聚合，看的是这段时间整体权利金往哪边压）和
    UW 实时警报（单笔扫描命中）是两种独立的测量，方向可能合法地不一致。

    这时不能把两条矛盾的结论原样并列摆出来——那样用户要么无所适从，要么
    只信先读到的那条；而且两个 kind 各自的默认严重度（比如 70 对 45）
    本身不代表谁更权威，不该被读成「看空那条更重要」。所以两边打架时
    合成一条，把双方原始结论都摆出来、并且明说「这是两种独立测量，谁也
    不比谁权威」——不替用户选边，也不用某个具体严重度去暗示某一边更可信
    （见 `SEVERITY["options_signal_conflict"]` 的注释）。
    """
    return {
        "ticker": ticker,
        "kind": "options_signal_conflict",
        "what_happened": (
            f"两路期权信号方向不一致：整体权利金流向统计说"
            f"「{of_alert['what_happened']}」；UW 实时警报说"
            f"「{uw_alert['what_happened']}」"),
        "what_it_means": (
            "这是两种独立的测量——一个看这段时间整体权利金往哪边压，"
            "一个是你自己配置的实时扫描规则命中的单笔大单——谁也不比谁"
            "更权威。分歧本身就是信息：说明这只票的期权市场眼下没有形成"
            "一致方向"),
        "suggested_action": (
            "别只挑一条信自己想信的方向；等价格自己给出确认（站稳或跌破"
            "关键均线）再决定，不要单靠期权信号下判断"),
        "severity": SEVERITY.get("options_signal_conflict", 55),
        "source": f"{of_alert.get('source')}+{uw_alert.get('source')}",
    }


def _freshness_warnings(market: str, fresh: dict) -> list[str]:
    """把 `regime.freshness()` 的结论翻成人话警告。拆成纯函数是为了能测——
    真实数据哪天行情不陈旧、采集任务都正常，端到端测这条逻辑就会全绿，
    测不出「陈旧警告被删掉了」这种改坏（`detect_catalysts.py` 踩过这个坑）。
    """
    out = []
    if fresh["stale"]:
        out.append(f"{market} 行情最新只到 {fresh['latest_trading_day']}，"
                   f"距今 {fresh['days_since']} 天")
    if fresh["failed_jobs"]:
        out.append(f"{market} 有采集任务失败：{fresh['failed_jobs']}")
    if fresh["partial_jobs"]:
        out.append(f"{market} 有采集任务只跑了一半（数据写了，某个可选源"
                   f"没取到）：{fresh['partial_jobs']}")
    return out


def watch_positions(hours: int = 24) -> str:
    """我的持仓上有没有需要立刻处理的信号。

    四路一起看：价格触发（跌破均线、跌破止损、创 20 个交易日新低）、
    财报临近、期权异动（A股换成主力资金和龙虎榜）、突发消息，外加 UW
    实时警报流里命中持仓的部分。

    **没事就说没事**——不触发就返回空列表加一句话，不制造噪音。
    每条告警都会说清楚：发生了什么、对你的仓位意味着什么、建议怎么处理，
    还带一个 `source` 字段说明是哪一路数据算出来的。同一只票的多笔期权
    异动会合并成一条，不会因为一只票交易活跃就刷屏；期权异动统计
    （数据库聚合）和 UW 实时警报（单笔扫描命中）方向不一致时也不会原样
    并列摆出来，会合成一条说清楚分歧（`options_signal_conflict`）。

    止损相关的告警分三种、严重度按跌破幅度和「破了多久」分档，不是固定分：
    `stop_breached` 是仓位已经浮亏、且最近才破位的真实信号（跌破越深分越
    高）；`stop_breached_longstanding` 是仓位已经浮亏、但破位已经持续超过
    `STOP_BREACH_RECENT_DAYS` 个交易日——不再是新信号，是持仓状态，严重度
    大幅降档，且给的是今天真能用的 `forward_stop` 而不是早就是历史价位的
    原止损位；`trailing_stop_touched` 是仓位还在浮盈、只是碰到了用来护
    盈利的移动止损线（整体严重度远低于前两者）。止损依据本来就是 20 日
    均线时，不会重复再发一条「跌破 20 日均线」。详见模块 docstring
    「止损告警」一节。

    价格触发和财报临近不依赖任何外部接口，UW 那两路（期权异动、实时警报）
    挂了不连累其它路，会在 `checked_sources` 里少一项、在 `data_gaps`
    里写明原因。行情陈旧、采集任务失败或只跑一半会写进 `warnings`。

    hours: 消息和期权异动往回看几小时，默认 24
    """
    try:
        hours = max(1, min(int(hours), 24 * 7))
        rows = pg.query(_POSITIONS_SQL)
        if not rows:
            return json.dumps({"alerts": [], "checked": 0,
                               "checked_sources": ["price"],
                               "hours": hours, "warnings": [],
                               "data_gaps": [], "alert_count": 0,
                               "returned_count": 0, "truncated": False,
                               "message": "没有持仓，没什么可盯的"},
                              ensure_ascii=False)

        by_market: dict[str, list[str]] = {}
        for row in rows:
            by_market.setdefault(row["market"], []).append(row["ticker"])
        cost_of = {r["ticker"]: float(r["avg_cost"] or 0) for r in rows}
        shares_of = {r["ticker"]: float(r["shares"] or 0) for r in rows}
        currency_of = {r["ticker"]: r["currency"] for r in rows}
        all_tickers = [r["ticker"] for r in rows]

        snaps: dict[str, dict | None] = {}
        earnings: dict[str, dict | None] = {}
        # ticker -> {"today_low": ..., "prior_low_20": ...}，见 `_NEW_LOW_SQL`
        lows: dict[str, dict] = {}
        stop_history: dict[str, list[dict]] = {}
        gaps: list[str] = []
        warnings: list[str] = []

        # ---- 每个涉及到的市场：行情快照、财报日、阶段新低、止损历史、
        # 新鲜度。一个市场里某一项挂了，不连累这个市场的其它项，也不连累
        # 另一个市场 ----
        for market, tickers in by_market.items():
            try:
                snaps.update(snapshot.get(tickers, market))
            except Exception as exc:
                gaps.append(f"{market} 行情快照拉取失败：{exc}")

            try:
                earnings.update(catalyst.earnings_for(tickers, market))
            except Exception as exc:
                gaps.append(f"{market} 财报日拉取失败：{exc}")

            try:
                for low_row in pg.query(_NEW_LOW_SQL, (tickers, market)):
                    lows[low_row["ticker"]] = low_row
            except Exception as exc:
                gaps.append(f"{market} 阶段新低查询失败：{exc}")

            try:
                stop_history.update(snapshot.stop_history(
                    tickers, market, STOP_HISTORY_LOOKBACK_DAYS))
            except Exception as exc:
                gaps.append(f"{market} 止损历史查询失败：{exc}")

            try:
                fresh = regime.freshness(market)
            except Exception as exc:
                fresh = None
                gaps.append(f"{market} 行情新鲜度检测失败：{exc}")
            if fresh:
                warnings.extend(_freshness_warnings(market, fresh))

        alerts: list[dict] = []
        sources = ["price"]

        # ---- 第一路：价格触发。不依赖任何外部接口，只用上面已经拿到的数据 ----
        for ticker, snap in snaps.items():
            if not snap:
                continue
            close = snap["close"]
            currency = currency_of[ticker]
            stop = scoring.position_stop(cost_of[ticker], snap)
            # 止损依据就是 20 日均线、且这条已经发过告警时，跌破 20 日均线
            # 是同一件事，不再单独重复一条（模块 docstring「坑四」）
            suppress_below_ma20 = False
            if stop.get("breached"):
                pnl = (close - cost_of[ticker]) * shares_of[ticker]
                depth = abs(stop.get("distance_pct") or 0.0)
                consecutive_days, last_above = _consecutive_breach_days(
                    cost_of[ticker], stop_history.get(ticker, []))
                # 分到哪一类由 core/scoring 统一判，review_positions 用的是
                # 同一个函数——两个工具对同一只票不可能再各说各话（评审 C1）
                state = scoring.classify_stop_breach(
                    pnl, consecutive_days, STOP_BREACH_RECENT_DAYS)
                streak_note = (f"，已经连续 {consecutive_days} 个交易日了"
                               if consecutive_days > 1 else "")

                if state == scoring.STOP_STATE_TRAILING:
                    # 均线在成本上方时止损本来就抬到了均线上（护浮盈），
                    # 这时候破位是「浮盈还在，但保护线没了」，不是「亏钱
                    # 了」。ma_20 每天用最新数据重算，不存在「历史价位」
                    # 的问题，所以不做「破了多久」的降档，只在文案里
                    # 提一句透明度（模块 docstring「止损告警」一节）
                    kind = "trailing_stop_touched"
                    alerts.append(_alert(
                        ticker, kind,
                        f"现价 {_round2(close)} 跌破了建议止损位 "
                        f"{stop['suggested_stop']}（依据：{stop['basis']}，"
                        f"跌破幅度 {depth:.2f}%）",
                        f"这笔仓位现在还是浮盈 {pnl:.0f} {currency}，"
                        f"但价格已经跌破了用来锁定利润的止损线{streak_note}",
                        "考虑先落袋一部分利润，或者把止损重新定在一个"
                        "你能接受的位置；这不是「这笔交易错了」，"
                        "不必马上清仓",
                        source="price",
                        severity=_stop_alert_severity(kind, depth)))
                elif state == scoring.STOP_STATE_RECENT:
                    # 最近才破位：真实、紧急的信号，维持原来的分档
                    kind = "stop_breached"
                    alerts.append(_alert(
                        ticker, kind,
                        f"现价 {_round2(close)} 跌破了建议止损位 "
                        f"{stop['suggested_stop']}（依据：{stop['basis']}，"
                        f"跌破幅度 {depth:.2f}%）{streak_note}",
                        f"这笔仓位当前浮亏 {abs(pnl):.0f} {currency}，"
                        "已经跌出了当初设的容错范围",
                        "按纪律该减仓或清掉。要留就得说清楚新的止损"
                        "放哪、为什么这次不一样",
                        source="price",
                        severity=_stop_alert_severity(kind, depth)))
                else:
                    # 破位已经持续很久：不是新信号，是持仓状态。原止损位
                    # 是历史价位，今天用不上了，改用 forward_stop（模块
                    # docstring「坑三」，评审 C1）
                    kind = "stop_breached_longstanding"
                    forward = stop.get("forward_stop")
                    last_above_text = (f"（上次站上止损位是 {last_above}）"
                                       if last_above else "")
                    if forward is not None:
                        action = (
                            f"如果决定继续持有，参考止损可以放在 "
                            f"{forward}（在现价下方，是今天真能用的价位，"
                            "原止损位已经是历史价位）；如果已经不认同这笔"
                            "交易的逻辑了，该走的话早晚要走，拖时间不会让"
                            "决定变容易")
                    else:
                        action = ("按纪律该减仓或清掉。要留就得说清楚新的"
                                  "止损放哪、为什么这次不一样")
                    alerts.append(_alert(
                        ticker, kind,
                        f"已经连续 {consecutive_days} 个交易日在止损位"
                        f"下方{last_above_text}——原止损位 "
                        f"{stop['suggested_stop']}（依据：{stop['basis']}）"
                        "已经是历史价位，今天已经用不上了",
                        f"这笔仓位当前浮亏 {abs(pnl):.0f} {currency}，"
                        f"而且这个状态已经持续了 {consecutive_days} 个"
                        "交易日，不是今天刚发生的——不该每天都当成新的"
                        "紧急信号来看",
                        action, source="price",
                        severity=SEVERITY["stop_breached_longstanding"]))
                if stop.get("basis") == "ma_20":
                    suppress_below_ma20 = True
            if snap.get("above_ma50") is False:
                alerts.append(_alert(
                    ticker, "below_ma50",
                    f"跌破 50 日均线（{_round2(snap['ma_50'])}）",
                    "中期趋势转弱了，波段的多头依据没了",
                    "波段仓位考虑离场；要留就当成中线仓，重新定止损",
                    source="price"))
            elif snap.get("above_ma20") is False and not suppress_below_ma20:
                alerts.append(_alert(
                    ticker, "below_ma20",
                    f"跌破 20 日均线（{_round2(snap['ma_20'])}）",
                    "短期动能转弱，但中期趋势还在",
                    "先减一部分观察，等站回均线再补",
                    source="price"))
            low_row = lows.get(ticker) or {}
            today_low = low_row.get("today_low")
            prior_low = low_row.get(f"prior_low_{NEW_LOW_WINDOW}")
            if is_new_low(today_low, prior_low):
                alerts.append(_alert(
                    ticker, "new_low",
                    f"创 {NEW_LOW_WINDOW} 个交易日新低：今日最低 "
                    f"{_round2(float(today_low))}，跌破了此前 "
                    f"{NEW_LOW_WINDOW} 个交易日的最低价 "
                    f"{_round2(float(prior_low))}（收盘 {_round2(close)}）",
                    "没有买盘接，下跌还在延续",
                    "别在下跌途中摊平成本，先等企稳信号",
                    source="price"))

        # ---- 第二路：财报临近。同样只用已经拿到的数据 ----
        for ticker, info in earnings.items():
            days_until = (info or {}).get("days_until")
            if days_until is not None and 0 <= days_until <= EARNINGS_SOON_DAYS:
                alerts.append(_alert(
                    ticker, "earnings_soon",
                    f"{days_until} 天后财报（{info['next_earnings_date']}）",
                    "财报是掷骰子，一晚上可能跳 10% 以上，方向不可预测",
                    "波段仓位建议财报前减到能承受的量，或者干脆先出来",
                    source="earnings"))

        # ---- 第三路：期权异动（美股）/ 主力资金和龙虎榜（A股）----
        # 期权异动统计的方向暂存在这里，不直接进 alerts——要等 UW 实时
        # 警报那一路也算完，两边合并检查方向是否打架（模块 docstring
        # 「两路期权信号可能打架」一节）
        options_flow_direction: dict[str, dict] = {}

        us_tickers = by_market.get("US", [])
        if us_tickers:
            try:
                # hours 转成自然日窗口：day 是自然日列，跟 hours 有粒度
                # 差，往回多留一天缓冲。calendar_days_covered 是 SQL 实际
                # 覆盖的自然日数（含今天），文案直接用这个数，不再像以前
                # 那样另算一遍导致对不上（评审 I5）
                lookback_days = max(1, hours // 24 + 1)
                calendar_days_covered = lookback_days + 1
                p90 = {r["ticker"]: r["p90"]
                       for r in pg.query(_FLOW_P90_SQL, (us_tickers,))}
                big_by_ticker: dict[str, list[dict]] = {}
                for row in pg.query(_FLOW_RECENT_SQL,
                                    (us_tickers, lookback_days)):
                    if is_big_flow(row, p90.get(row["ticker"])):
                        big_by_ticker.setdefault(row["ticker"], []).append(row)

                # 按票汇总，不按笔数扇出——见模块 docstring
                for ticker, big_rows in big_by_ticker.items():
                    bullish = [r for r in big_rows if r["sentiment"] == "bullish"]
                    bearish = [r for r in big_rows if r["sentiment"] == "bearish"]
                    neutral = [r for r in big_rows
                               if r["sentiment"] not in ("bullish", "bearish")]
                    bullish_sum = sum(float(r["total_premium"] or 0)
                                      for r in bullish)
                    bearish_sum = sum(float(r["total_premium"] or 0)
                                      for r in bearish)
                    # 对**全部**过线的行求和，不是只加看涨和看跌两桶。
                    # 库里有大量 sentiment 既非 bullish 也非 bearish 的行
                    # （近 30 天 1658 笔，最大一笔 3.68 亿美元；持仓票里
                    # AMZN 出现过一笔 54 万美元）。漏掉它们会让一笔已经
                    # 超过 50 万硬门槛的交易被判成「量级不算大」——
                    # 方向跟评审 I4 相反，是低报。
                    total_sum = sum(float(r["total_premium"] or 0)
                                    for r in big_rows)
                    # 汇总总额连绝对下限都没到：整条不发（评审 I5）。
                    # 分位数那条软门槛按定义每只票每天都有约 10% 的行满足，
                    # 不加这条下限就等于保证天天有货，正撞上「没事就说没事」
                    if total_sum < OPTIONS_FLOW_MIN_TOTAL_PREMIUM:
                        continue
                    biggest = max(big_rows,
                                 key=lambda r: float(r["total_premium"] or 0))
                    net_bearish = bearish_sum > bullish_sum
                    kind = "big_bearish_flow" if net_bearish else "big_bullish_flow"
                    # 「看涨/看跌」的计数和「最大一笔」的方向判定统一用
                    # sentiment，合约本身是 CALL 还是 PUT 单独标出来，
                    # 不混进方向词里说（评审 I2）
                    biggest_dir = {"bullish": "看涨", "bearish": "看跌"}.get(
                        biggest["sentiment"], "中性")
                    is_large = total_sum >= BIG_PREMIUM
                    scale_phrase = ("大额期权交易" if is_large else
                                    "期权交易（进了这只票近期活跃度前 10%，"
                                    "但合计金额不算大）")
                    # 有方向判不出来的行时必须把它们说出来，否则
                    # 「有 5 笔：看涨 2 笔、看跌 1 笔」会让人找不到另外 2 笔
                    neutral_phrase = (
                        f"、方向判不出来的 {len(neutral)} 笔合计 "
                        f"{sum(float(r['total_premium'] or 0) for r in neutral) / 1e4:.0f}"
                        f" 万美元" if neutral else "")
                    what_happened = (
                        f"近 {calendar_days_covered} 天有 {len(big_rows)} 笔"
                        f"{scale_phrase}：看涨 {len(bullish)} 笔合计 "
                        f"{bullish_sum / 1e4:.0f} 万美元、看跌 {len(bearish)} "
                        f"笔合计 {bearish_sum / 1e4:.0f} 万美元"
                        f"{neutral_phrase}，最大一笔 "
                        f"{float(biggest['total_premium']) / 1e4:.0f} 万美元，"
                        f"情绪判定为{biggest_dir}（{biggest['opt_type']} 合约，"
                        f"行权价 {biggest['strike']}，到期 {biggest['expiry']}）")
                    if is_large:
                        what_it_means = (
                            "有大资金在这个方向上集中押注，" +
                            ("跟你的多头仓位是反的" if net_bearish else "跟你的方向一致"))
                        suggested_action = (
                            "别只凭这一条动手，配合价格看——"
                            "价格同时跌破均线才是真信号" if net_bearish
                            else "可以作为继续持有的旁证，不构成加仓理由")
                        severity = SEVERITY[kind]
                    else:
                        # 只是靠 score 分位数这条软门槛过线（这条门槛按
                        # 定义每只票永远有约 10% 的行满足），总金额没到
                        # 50 万美元这条硬门槛——不该叫「大资金押注」
                        # （评审 I4）
                        what_it_means = (
                            "量级不算大，只是恰好排进了这只票近期期权"
                            "交易活跃度的前 10%，还谈不上「大资金押注」")
                        suggested_action = "量级不大，不用为这一条单独采取行动，留意就好"
                        severity = (OPTIONS_FLOW_SMALL_BEARISH_SEVERITY
                                   if net_bearish
                                   else OPTIONS_FLOW_SMALL_BULLISH_SEVERITY)
                    options_flow_direction[ticker] = _alert(
                        ticker, kind, what_happened, what_it_means,
                        suggested_action, source="options_flow_archive",
                        severity=severity)
                # 放在查询和处理都成功之后，查询挂了就不会假装这一路
                # 成功过（评审 M8）
                sources.append("options_flow")
            except Exception as exc:
                gaps.append(f"期权异动拉取失败：{exc}")

        cn_tickers = by_market.get("CN", [])
        if cn_tickers:
            try:
                for ticker, flow in cn_signals.fund_flow(cn_tickers).items():
                    if not flow:
                        continue
                    net_5d = flow.get("net_5d") or 0
                    if net_5d < -100_000_000:
                        alerts.append(_alert(
                            ticker, "cn_outflow",
                            f"主力 5 日净流出 {abs(net_5d) / 1e8:.2f} 亿",
                            "机构资金在持续出货，散户接盘的行情很难走远",
                            "考虑减仓，至少不要在这个位置加",
                            source="cn_fund_flow"))
                sources.append("cn_fund_flow")
            except Exception as exc:
                gaps.append(f"A股主力资金拉取失败：{exc}")

            try:
                for ticker, records in cn_signals.lhb(cn_tickers, days=5).items():
                    if records:
                        alerts.append(_alert(
                            ticker, "lhb",
                            f"近 5 天上过龙虎榜（{records[0]['reason']}）",
                            "游资在这只票上进出，波动会明显放大",
                            "把止损放宽一点或者减小仓位，别被日内波动扫掉",
                            source="lhb"))
            except Exception as exc:
                gaps.append(f"龙虎榜拉取失败：{exc}")

        # ---- 第四路：突发消息。中性情绪不算正面；挂票数够多的大盘综述
        # 不算这只仓位的专属消息，直接不发；每票的上限在过滤之后才生效
        # （模块 docstring 有详细说明，评审 M9）----
        try:
            news = catalyst.news_for(all_tickers, days=max(1, hours // 24),
                                     min_significance="High",
                                     per_ticker=NEWS_CANDIDATES_PER_TICKER)
            for ticker, items in news.items():
                for item in items:
                    if catalyst.is_market_wide(item):
                        continue  # 大盘综述，跳过找下一条候选
                    label = (item.get("sentiment_label") or "").lower()
                    if label == "negative":
                        kind, tag, means = ("bad_news", "负面",
                                            "重大消息通常会改变这只票未来"
                                            "几天的走势方向")
                    elif label == "positive":
                        kind, tag, means = ("good_news", "正面",
                                            "重大消息通常会改变这只票未来"
                                            "几天的走势方向")
                    else:
                        # 情绪中性（或缺失）：告诉用户出了件大事，但不替
                        # 它编一个正面/负面的方向（模块 docstring 有真实
                        # 案例：一条中性、挂 8 只票的综述曾被报成「正面」）
                        kind, tag, means = ("neutral_news", "",
                                            "这条消息的情绪判断是中性的，"
                                            "没有明确指向利好还是利空，"
                                            "自己看一眼原文再判断")
                    # 标题偶尔是 None（上游抓到了链接但没抓到标题）。
                    # 不兜底的话会直接印出「重大消息（情绪中性）：None」
                    # 给用户看
                    title = item.get("title") or "（这条新闻没有标题，"\
                                                 "去原文看）"
                    alerts.append(_alert(
                        ticker, kind,
                        f"重大{tag}消息：{title}" if tag
                        else f"重大消息（情绪中性）：{title}",
                        means,
                        "先看股价反应过了没有——已经跳完的消息不用再动作；"
                        "还没反应的才需要决定",
                        source="news"))
                    break  # 每票只发这一条最新的个股专属消息
            # `catalyst.news_for()` 会给每只查询到的票预填空列表，
            # `if news:` 这种写法永远为真（评审 M8）——要判断的是「有没有
            # 真的查到内容」，不是「返回的字典存不存在」
            if any(items for items in news.values()):
                sources.append("news")
        except Exception as exc:
            gaps.append(f"消息拉取失败：{exc}")

        # ---- UW 实时警报：解出标的、解方向，同一只票多条规则合并成一条。
        # 方向已解出的暂存在这里，跟期权异动统计合并检查方向打不打架
        # （模块 docstring「两路期权信号可能打架」一节）----
        uw_live_direction: dict[str, dict] = {}
        try:
            if uw.available():
                live = uw.alerts(limit=100)
                if live is not None:
                    held = set(all_tickers)
                    # ticker -> {"bullish": [规则名...], "bearish": [...],
                    #            "unknown": [...]}
                    grouped: dict[str, dict[str, list[str]]] = {}
                    for item in live:
                        underlying = uw.underlying_of(item)
                        if not underlying or underlying not in held:
                            continue
                        name = item.get("name") or item.get("noti_type") or "?"
                        direction = _uw_live_direction(item) or "unknown"
                        bucket = grouped.setdefault(
                            underlying, {"bullish": [], "bearish": [],
                                        "unknown": []})
                        if name not in bucket[direction]:
                            bucket[direction].append(name)

                    for ticker, buckets in grouped.items():
                        bullish, bearish, unknown = (buckets["bullish"],
                                                     buckets["bearish"],
                                                     buckets["unknown"])
                        if bullish and not bearish:
                            kind, names = "big_bullish_flow", bullish
                        elif bearish and not bullish:
                            kind, names = "big_bearish_flow", bearish
                        else:
                            # 方向不明（拿不到 ask/bid 量、解不出合约类型），
                            # 或者同一只票里既有看多又有看空的规则，方向
                            # 说不清——不猜，如实说不知道
                            kind = None
                            names = bullish + bearish + unknown
                            gaps.append(
                                f"{ticker} 的 UW 实时警报方向判断不了"
                                f"（{len(unknown)} 条缺方向数据"
                                + (f"，{len(bullish)} 条看多、"
                                   f"{len(bearish)} 条看空，方向不统一"
                                   if bullish or bearish else "") + "）")

                        if kind:
                            uw_live_direction[ticker] = _alert(
                                ticker, kind,
                                f"实时警报触发：{'、'.join(names)}",
                                "你配置的扫描规则在这只持仓上触发了，"
                                "解出来的期权方向是"
                                + ("看多" if kind == "big_bullish_flow" else "看空"),
                                "去 Unusual Whales 看这条警报的细节再决定",
                                source="uw_live_alert")
                        else:
                            alerts.append(_alert(
                                ticker, "uw_alert_direction_unknown",
                                f"实时警报触发但方向判断不了：{'、'.join(names)}",
                                "你配置的扫描规则触发了，但这批数据没法"
                                "确定是偏多还是偏空",
                                "去 Unusual Whales 看这条警报的细节，"
                                "自己判断方向",
                                source="uw_live_alert"))
                    sources.append("uw_alerts")
        except Exception as exc:
            gaps.append(f"UW 实时警报拉取失败：{exc}")

        # ---- 合并期权异动统计和 UW 实时警报两路的方向信号。方向一致就
        # 两条都正常发；方向不一致就合成一条说清楚分歧，不原样并列摆出来
        # （模块 docstring「两路期权信号可能打架」一节）----
        for ticker in sorted(set(options_flow_direction) | set(uw_live_direction)):
            of_alert = options_flow_direction.get(ticker)
            uw_alert = uw_live_direction.get(ticker)
            if of_alert and uw_alert and of_alert["kind"] != uw_alert["kind"]:
                alerts.append(_conflict_alert(ticker, of_alert, uw_alert))
            else:
                if of_alert:
                    alerts.append(of_alert)
                if uw_alert:
                    alerts.append(uw_alert)

        alerts.sort(key=lambda a: (-a["severity"], a["ticker"]))
        alert_count = len(alerts)
        returned = alerts[:MAX_ALERTS]
        truncated = alert_count > len(returned)

        message = ""
        if not returned:
            message = f"{len(rows)} 只持仓，无异常"

        return json.dumps({
            "alerts": returned,
            "alert_count": alert_count,
            "returned_count": len(returned),
            "truncated": truncated,
            "checked": len(rows),
            "checked_sources": sources,
            "hours": hours,
            "warnings": warnings,
            "data_gaps": sorted(set(gaps)),
            "message": message,
        }, ensure_ascii=False, default=str)

    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
