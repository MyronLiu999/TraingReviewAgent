# TradingReview · Al Brooks 交易助手

**把当前行情截图给它，它用 Al Brooks 的价格行为理论告诉你：做多还是做空、在哪里入场、止损放哪、目标在哪、这笔交易该不该做、做多少手。**
同一套**受保护的 Al Brooks 原典知识库**和你的**长期学习记忆**还支持复盘和学习：

| 模式 | 做什么 |
|---|---|
| 🎯 **交易助手 Trade**（默认页） | 当前行情截图 → **TAKE 建议入场 / WAIT 等待触发 / NO_TRADE 不做**；入场方式（突破单/限价单）和价格、止损、目标1 + 目标2、交易者方程、仓位、持仓管理、"该不该做"检查清单，并把入场/止损/目标线**画在你的截图上**（**只分析，不下单**） |
| 📊 **复盘 Review** | 截图 → 在原图上标注价格行为结构，每个标注有编号（#1、#2…）可追问、可质疑、改动留版本 |
| 📖 **学习 Study** | 问任何 Brooks 理论问题：先直接回答，再讲原理、结合图表、常见错误；每条结论标注 `[BROOKS SOURCE]` 等来源和书页 |
| 📡 **实时监控 Monitor**（下一阶段，接口已就绪） | 盯着你导出的K线数据或截图文件夹：新K线收盘后重新分析，入场触发 / 到目标 / 止损时提醒你 |

架构、数据结构和全部操作说明见 **[ARCHITECTURE.md](ARCHITECTURE.md)**。

### 🎯 交易助手怎么帮你做决定

模型按一份 Brooks 交易流程（playbook）一步步看图：先看图（品种、周期、最后一根K线是否收盘）→ 市场阶段（强趋势 / 通道 / 交易区间 / 突破模式 / 窄幅区间）→ Always In 方向和**位置**（区间上沿/中间/下沿、EMA、前高前低、高潮之后）→ 适合这个背景的 setup（H1/H2、L1/L2、楔形、失败突破、双顶双底、主要趋势反转…）→ 信号K线质量 → 入场方式 → 止损 → 目标 → 交易者方程 → 检查清单 → 建议。

**程序（不是模型）负责把关**，模型说了不算：
- 入场 / 止损 / 目标的方向必须一致（做多：止损 < 入场 < 目标），否则价格作废；看不清价格轴就不给数字
- **风险收益和交易者方程由程序计算**：概率等级 高（≈60%+）/ 中（≈50%）/ 低（≈40%-）× 回报 − 失败 × 风险；低概率交易至少要 2R、中等 1.5R、高概率 1R，不满足就从 TAKE 降为 WAIT
- 检查清单里有一项"致命"（fail）、setup 等级低于你设的最低等级、或者止损宽到 1 手都超过你的单笔风险 → **不会给 TAKE**
- **仓位**按 `config.toml` 的 `[trading]`（账户规模 × 单笔风险 %）计算手数，内置 ES/MES/NQ/MNQ/YM/MYM/GC/MGC/CL/MCL 等合约每点价值
- 只用截图里最后一根K线之前的信息；概率数字只能来自原书

> ⚠️ 没有任何工具能保证赚钱。这个助手的作用是让你**只做符合 Brooks 条件、风险算清楚的交易，并且在不该做的时候忍住不做**。是否下单、下多少由你决定；系统没有、也不能下单。

复盘图里有：
- 用色块标出市场阶段：**Always In Long**（绿）、**Always In Short**（红）、**交易区间**（蓝框）
- 用圆圈标出关键 K 线：高潮、反转点、双顶/双底、更高低点/更低高点
- 用带箭头的标签说明每个形态（英文术语 + 中文解释）
- 附复盘总结和文字分析（当时好的交易是什么、常见错误）

主要通过**电脑上的网页界面**使用，截图后直接粘贴即可。Telegram 是可选功能（截图发 `/trade` 拿交易计划、新闻提醒、实时监控提醒）。

---

## 🚀 第一次使用（约 10 分钟）

### 1. 安装 Python（只需一次）
到 https://www.python.org/downloads/ 下载安装。**Windows 安装时一定要勾选 "Add python.exe to PATH"。**

### 2. 准备 OpenAI API Key
1. 登录 https://platform.openai.com （可以用 ChatGPT 账号）。
2. **Settings → Billing** 充值，例如 $5。⚠️ ChatGPT Plus 会员**不包含** API 额度。
3. 打开 https://platform.openai.com/api-keys ，点 **Create new secret key**，复制以 `sk-` 开头的密钥。

### 3. 下载并启动
1. 在 GitHub 仓库页面点 **Code → Download ZIP**，解压。
2. **Windows：** 双击 `start.bat`；**Mac：** 在终端进入文件夹，运行 `./start.sh`。
3. 第一次会自动安装（约 1 分钟），然后打开一个 `.env` 文件。填两项后保存：
   ```
   APP_PASSWORD=自己设一个网页登录密码
   OPENAI_API_KEY=sk-...你的密钥...
   ```
4. 再双击一次 `start.bat`（或运行 `./start.sh`）。浏览器会自动打开 **http://localhost:8000**。

### 4. 使用：交易助手（打开网页默认就是这一页）
1. 输入你设的密码登录，停在 **🎯 交易助手 Trade**。
2. 截图**当前**行情（带上右侧价格轴），按 **Ctrl+V** 粘贴。
3. 在「品种 + 周期」里写上例如 `MES 5分钟`（用来算仓位），问题可以不改：`现在这里能不能做？如果做，在哪里入场、止损放哪、目标在哪？`
4. 点 **🎯 给我交易计划**。结果包括：画好入场/止损/目标线的图、TAKE / WAIT / NO_TRADE、交易者方程、仓位、检查清单、持仓管理、失效条件。
5. 下面可以继续追问：「为什么止损在这」「没触发怎么办」「入场后怎么管理」「对手盘怎么想」，或者直接反驳它。
6. 第一次用之前，建议在 `config.toml` 的 `[trading]` 里填 `account_size`（账户规模）和 `risk_per_trade_pct`（单笔风险 %，默认 1%），这样它会告诉你做几手。

### 5. 使用：复盘
1. 切到 **复盘 Review**。
2. 在交易软件里截图（Windows：`Win+Shift+S`；Mac：`⌘+Shift+4`），回到网页按 **Ctrl+V**（Mac：**⌘+V**）粘贴。也可以把图片拖进来，或点击选择文件。
3. 可选：写一句说明，例如 `MYM 5分钟，我在高点做空，止损被打`。
4. 点 **开始复盘**。30–120 秒后显示复盘图、复盘分析和总结。
5. 复盘后就是一段**持续对话**：点任意标注（如 **#3 H2**）或用快捷按钮（整体逻辑 / 为什么这个方向 / 其他解释 / 什么会让你改判），也可以直接质疑「我觉得右边已经是交易区间了」「#2 标错了，因为…」。每次回答都有：
   - **推理过程**：观察（哪根K线）→ 依据（Brooks 原则 + 书页）→ 推断
   - **对你观点的判断**：同意 / 部分同意 / 不同意，以及理由（不会因为你反驳就附和）
   - **考虑过的其他解释**、**什么情况会让它改变判断**、**把握程度**
   - 结尾可能**反问你一个问题**；你回答后它会接着你的回答讲
   
   它真的改变判断时，标注存成新版本、整体判断记为修订，原始判断和旧图都保留。决策结果和学习页同样可以连续追问。
6. 事后可以在「后续结果」里写下行情怎么走的，教练会对比当时的判断（不会改动原判断），总结出的经验只进入**候选区**。
7. **学习**页提问理论；**交易助手**页可以附 OHLC CSV（价格以数据为准）；**记忆**页查看、批准、拒绝、删除教练记住的东西。
8. 左侧是**历史记录**（🎯 = 交易计划），点开随时回看。可以下载复盘图，也可以一键发送到 Telegram。

以后每次使用，只要双击 `start.bat` 就行。**使用期间保持那个黑色窗口开着**，关掉窗口就停止。

---

## 📚 你的学习资料

把资料放进 `docs/` 文件夹（支持 `.md`、`.txt`、`.pdf`）。每次复盘都会读取这些资料，并优先使用资料里的术语和规则。资料改了不用重启。

**Al Brooks 原典知识库（Canonical KB，只读）**：`config.toml` 的 `canonical_store`（默认 `D:/Uchicago Courses/Trading/BrooksCanonicalKB`）。
- 里面是经批准的教材抽取文字（逐页、带页码）和 AI 整理笔记（标为派生知识）的**只读快照**，每个文件都有 SHA-256 记录，启动时自动校验。
- 聊天、复盘、反思、你的观点都**不会**写进去；只有你在终端里输入授权短语的管理命令才能创建新版本或回滚：
  ```powershell
  .venv\Scripts\python -m knowledge.admin status
  .venv\Scripts\python -m knowledge.admin verify          # 加 --full 连原始 PDF 一起校验
  ```
- 复盘时用其中的 `11_REVIEW_AGENT_PRIMER.md`（读图规则）；学习/解释/决策时按问题检索原书页面并给出页码。
- `docs/` 里的 `.md`/`.txt` 笔记会作为 `[USER NOTE]` 一起使用；知识库可用时 `docs/` 里的原书 PDF 会被跳过（它们就是知识库的来源）。

## ⚙️ 设置（`config.toml`，可选）

| 设置 | 说明 |
|---|---|
| `review_model = "gpt-4o"` | 读图用的模型（`study_model` / `decision_model` 为空时也用它） |
| `canonical_store` | Brooks 原典知识库位置（只读） |
| `data_folder = "data"` | 记忆、任务记录、日志的位置 |
| `decisive_style = true` | 交易助手先给结论再给证据 |
| `[trading] account_size / risk_per_trade_pct` | 账户规模和单笔风险 %，用来算手数（0 = 不算） |
| `[trading] max_risk_per_trade / max_contracts` | 单笔最大亏损（美元）/ 最多手数，可选 |
| `[trading] min_setup_quality = "B"` | 低于这个等级的 setup 不会给 TAKE |
| `[trading] min_rr_high / min_rr_even / min_rr_low` | 各概率等级至少需要的回报风险比（默认 1 / 1.5 / 2） |
| `[trading.point_values]` | 其它品种的每点价值，例如 `SPY = 1` |
| `[monitor]` | 实时监控设置（见下方） |
| `review_snap_targets = true` | 把箭头吸附到最近的 K 线高/低点（记录原位置） |
| `review_auto_layout = true` | 把标签自动放到所指 K 线旁边的空白处，避开K线、其他标签和箭头；总结框放到最空的区域（记录模型原位置） |
| `llm_max_output_tokens = 20000` | 推理型模型（gpt-5.x / gpt-6）的"思考"也算输出，太小会被截断 |
| `review_refine_passes = 1` | 自我纠错轮数；`0` 更快更便宜，但标注位置可能偏一些 |
| `web_port = 8000` | 网页端口 |
| `web_host = "127.0.0.1"` | 改成 `"0.0.0.0"` 后，同一 Wi-Fi 下的手机或平板也能访问（用电脑的局域网 IP） |
| `enable_news_alerts` | `false` = 关闭新闻提醒 |

**费用：** 每张复盘图大约 US$0.03–0.08（gpt-4o，含一轮纠错），以 OpenAI 账单为准。

**注意：** AI 读图会有误差，标注位置、价格和形态判断不一定完全准确。交易计划是分析参考：下单前在你的交易软件里自己核对价格和条件，严格按止损执行。

**常见问题：**

| 提示 | 解决办法 |
|---|---|
| `'python' 不是内部或外部命令` | 重新安装 Python，勾选 "Add python.exe to PATH" |
| `请在 .env 里设置 APP_PASSWORD` | 打开 `.env`，填写 `APP_PASSWORD=` 后面的密码 |
| `Incorrect API key` / `401` | 检查 `.env` 里的 `OPENAI_API_KEY` |
| `insufficient_quota` / `429` | 去 platform.openai.com → Billing 充值 |
| 浏览器没有自动打开 | 手动打开 http://localhost:8000 |
| 端口被占用 | 把 `config.toml` 里的 `web_port` 改成 8001 之类 |

不打开网页也能用命令行（PowerShell）：
```powershell
.venv\Scripts\python agent_cli.py review 我的图.png --note "MYM 5分钟"
.venv\Scripts\python agent_cli.py explain "为什么 #3 是 H2？"
.venv\Scripts\python agent_cli.py ask "我觉得右边已经是交易区间了"
.venv\Scripts\python agent_cli.py chat      # 连续对话，直接回车退出
.venv\Scripts\python agent_cli.py study "请解释 H2 和两段式回调"
.venv\Scripts\python agent_cli.py trade 我的图.png "MES 5分钟 这里能不能做？"   # 交易助手（decide 也还能用）
.venv\Scripts\python agent_cli.py monitor --csv 导出的K线.csv --symbol MES --timeframe 5m   # 实时监控
.venv\Scripts\python agent_cli.py memory list
```
旧命令 `python chart_review.py 我的图.png --note "..."` 仍然可用。运行全部测试：`.venv\Scripts\python -m unittest discover -s tests -t .`

---

## 📡 实时监控（下一阶段：接口已经留好）

`monitor/` 里已经有完整的接口和两个能直接用的数据源，网页暂时不会自动启动它：
- **数据源 `MarketFeed`**：`CsvFileFeed`（交易软件持续导出/追加的K线 CSV）、`ScreenshotFolderFeed`（定时截图存到一个文件夹）、`BrokerFeed`（券商/行情 API 的占位，接 IBKR、Tradovate、TradingView webhook 等时实现它）
- **触发检查 `check_plan`**（纯代码）：新K线是否触发入场（突破单/限价单/市价）、入场前就破止损（计划取消）、到目标1（平一半、止损移到保本）、到目标2、止损，结果按 R 计算
- **提醒 `Notifier`**：Telegram / 终端，也可以自己加
- **循环 `Monitor`**：只在第一次、每 N 根新K线、计划结束后或新截图时调用模型；持仓中不重新判断；每小时调用次数有上限（控制费用）

试用：在 `config.toml` 的 `[monitor]` 里填 `csv_path`、`symbol`、`timeframe`，然后
```powershell
.venv\Scripts\python agent_cli.py monitor            # Ctrl+C 停止；配置了 Telegram 就推送到手机
.venv\Scripts\python agent_cli.py monitor --once     # 只检查一次
```

## 📱 Telegram（可选）

在 `.env` 里填上 `TELEGRAM_BOT_TOKEN`（从 @BotFather 获取，步骤见下方 "Create your Telegram bot"），并先给你的机器人发一句 `hi`。启动时会自动获取 chat ID，Telegram 机器人会和网页一起在后台运行：
- 截图的图片说明写 **`/trade MES 5分钟`**（或 `/decide`），收到画好入场/止损/目标的交易计划
- 在 Telegram 里发截图（不写 `/trade`），收到复盘图（和网页共用历史记录）
- `/study 问题`、`/explain #3 为什么是 H2`、`/ask 追问`、`/outcome 后来涨了 30 点`、`/memory`、`/approve 编号`、`/reject 编号`
- **直接回复教练的消息**就会接着讨论刚才那张图（不回复的普通文字仍然是新闻分析）
- 新闻提醒（见下方）
- 实时监控的入场 / 出场提醒也走这里

## 📰 新闻提醒 News alerts（可选）

This bot checks financial news every 2 minutes. An OpenAI model (GPT-4o) reads each new headline and decides whether it is **🟢 positive, 🔴 negative, ⚪ neutral, or 🟡 mixed** for stocks, gold, or both. Important news is sent to your **Telegram** on your phone.

You can also **paste any news into the Telegram chat** and get a verdict within seconds.

---

## What it costs

| Item | Cost |
|---|---|
| Telegram | **Free** |
| Telegram chat ID | **Free** (it's just a number that identifies your chat) |
| News feeds | **Free** |
| OpenAI API | **Pay per use.** A rough guess is US$1–3 per day with `gpt-4o` running all day, or a few cents per day with `gpt-4o-mini` |

⚠️ **A ChatGPT Plus subscription does NOT include API access.** The API is billed separately at platform.openai.com. You add credit there, for example $5 or $10, and usage is deducted from it.

---

## Step-by-step setup (about 20 minutes)

### Step 1: Install Python (once)
1. Go to https://www.python.org/downloads/ and download the latest version.
2. **Windows:** during install, tick **"Add python.exe to PATH"** at the bottom of the first screen.
3. Unzip `news_alert_bot.zip` somewhere easy to find, such as your Desktop.

### Step 2: Get your OpenAI API key
1. Go to https://platform.openai.com and log in. Your ChatGPT account works.
2. Click **Settings → Billing** and add credit (for example $5).
3. Go to **API keys** (https://platform.openai.com/api-keys) and click **Create new secret key**.
4. Copy the key. It starts with `sk-`. **Save it somewhere, because OpenAI only shows it once.**

### Step 3: Create your Telegram bot (free)
1. Open Telegram, search for **@BotFather** (it has a blue check mark), and open the chat.
2. Send: `/newbot`
3. BotFather asks for a **name**. Type anything, for example `My News Alerts`.
4. BotFather asks for a **username**. It must end in `bot`, for example `mengyuan_news_bot`.
5. BotFather replies with a **token** that looks like `7712345678:AAHk3x...`. Copy it.
6. Tap the link BotFather gives you (`t.me/your_bot_name`), press **Start**, and send your bot the message `hi`.
   *(This step matters: the bot can only find your chat after you message it.)*

### Step 4: Add the token and start
Put `TELEGRAM_BOT_TOKEN=...` in `.env` (next to `APP_PASSWORD` and `OPENAI_API_KEY`), send your bot `hi`, and run `start.bat` / `./start.sh`.
The start script finds your chat ID automatically. Your bot sends **"✅ Connected!"** and then **"🤖 Bot started"**, and it runs in the background together with the web UI.

---

## How to use it (in Telegram)

| Send this | What happens |
|---|---|
| Any news text, headline, or tweet | Instant verdict for your market |
| `/market equity` | Judge news for stocks |
| `/market gold` | Judge news for gold |
| `/market both` | Stocks and gold side by side |
| `/market NVDA` | Any ticker |
| `/threshold 4` | Only alert on bigger news (impact 1–5, default 3) |
| `/pause` / `/resume` | Stop or restart automatic alerts |
| `/summary` | Overall bias over the last 4 hours |
| `/status` | Show current settings |
| `/help` | Show all commands |

### Example alert
```
📰 US CPI rises 3.6% vs 3.2% expected
Inflation came in well above forecasts.
Source: CNBC Economy (Tier 2)

🔴 NEGATIVE for EQUITY  |  Impact 4/5  |  Confidence HIGH
⏱ immediate  |  Priced in: no
• Hot CPI → fewer Fed cuts → higher yields → pressure on stocks
Could be wrong if: core CPI in line
👀 10Y yield, DXY, Fed speakers
```

---

## Common problems

| Message | Fix |
|---|---|
| `'python' is not recognized` | Reinstall Python and tick **"Add python.exe to PATH"** |
| `No messages found` (get_chat_id) | Send your bot `hi` in Telegram, then run it again |
| `Telegram rejected the token` | Copy the token from @BotFather again; it includes the part before the colon |
| `Incorrect API key` / `401` | Check `OPENAI_API_KEY` in `.env` (no spaces, starts with `sk-`) |
| `insufficient_quota` / `429` | Add credit at platform.openai.com → Billing |
| `model not found` | Change `model` in `config.toml` to a model your account has, for example `gpt-4o-mini` |
| `Feed failed: ...` | That news site changed its address. It's safe to ignore, or remove it from `config.toml` |

---

## Settings (`config.toml`, optional)

Open it with Notepad or TextEdit:
- `markets = ["EQUITY"]`: the starting market (`["GOLD"]`, `["EQUITY", "GOLD"]`, or a ticker)
- `model = "gpt-4o"`: change to `"gpt-4o-mini"` to spend much less
- `min_impact_to_alert = 3`: raise it for fewer alerts
- `poll_seconds = 120`: how often to check the news
- `[[feeds]]`: news sources; add or remove RSS feeds here

---

## Important
- The bot gives **analysis, not trading advice**. It can be wrong.
- Free news feeds are seconds to minutes slower than professional terminals.
- Never share your `.env` file. It contains your keys.
- Every verdict is saved in `news_alerts.db`. After a few weeks, you can compare its calls with real price moves to see how far to trust it.
