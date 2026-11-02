# Trading Review Agent 交易复盘助手

把行情截图交给它，它会在图上标注出 **Al Brooks 价格行为**风格的复盘图：
- 用色块标出市场阶段：**Always In Long**（绿）、**Always In Short**（红）、**交易区间**（蓝框）
- 用圆圈标出关键 K 线：高潮、反转点、双顶/双底、更高低点/更低高点
- 用带箭头的标签说明每个形态（英文术语 + 中文解释）
- 附复盘总结和文字分析（当时好的交易是什么、常见错误）

主要通过**电脑上的网页界面**使用，截图后直接粘贴即可。Telegram 是可选功能（新闻提醒、以后的入场出场提醒）。

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

### 4. 使用
1. 输入你设的密码登录。
2. 在交易软件里截图（Windows：`Win+Shift+S`；Mac：`⌘+Shift+4`），回到网页按 **Ctrl+V**（Mac：**⌘+V**）粘贴。也可以把图片拖进来，或点击选择文件。
3. 可选：写一句说明，例如 `MYM 5分钟，我在高点做空，止损被打`。
4. 点 **开始复盘**。30–90 秒后显示复盘图、复盘分析和总结。
5. 左侧是**历史复盘**，点开随时回看。可以下载复盘图，也可以一键发送到 Telegram。

以后每次使用，只要双击 `start.bat` 就行。**使用期间保持那个黑色窗口开着**，关掉窗口就停止。

---

## 📚 知识库与规则

每次复盘，AI 都要**按决策框架逐步分析**：先判断市场背景，再找关键结构、交易机会和应避免的交易，最后给出当前状态。复盘图上的每个标签都会注明依据的规则编号，例如 `[PA-H-002]`。网页下方的"依据的规则"列出每条规则在这张图上是否成立（成立 / 部分成立 / 不成立），以及原因。

- `knowledge/`：起步版规则库，包含决策框架和 34 条 `Rule ID` 规则（市场结构、形态、出入场、心理）。
  ⚠️ 这套规则由 AI 根据 Al Brooks 体系的通用知识整理，**还没有对照你收集的原书核实，没有页码**。
- 你用原书建立的知识库完成后，在 `config.toml` 里改一行即可替换：
  `knowledge_base = "D:/Uchicago Courses/Trading/TradingAIKnowledgeBase"`
  （读取该文件夹里所有 `.md` 文件；`00_` 和 `07_` 开头的文件会放在最前面）
- `docs/`：其他补充资料（`.md`、`.txt`、`.pdf`），会附在知识库后面。

资料改了不用重启，下一次复盘就会生效。

## ⚙️ 设置（`config.toml`，可选）

| 设置 | 说明 |
|---|---|
| `review_model = "gpt-4o"` | 读图用的模型 |
| `review_refine_passes = 1` | 自我纠错轮数；`0` 更快更便宜，但标注位置可能偏一些 |
| `web_port = 8000` | 网页端口 |
| `web_host = "127.0.0.1"` | 改成 `"0.0.0.0"` 后，同一 Wi-Fi 下的手机或平板也能访问（用电脑的局域网 IP） |
| `enable_news_alerts` | `false` = 关闭新闻提醒 |

**费用：** 每张复盘图大约 US$0.10–0.20（gpt-4o，含知识库和一轮纠错），以 OpenAI 账单为准。知识库越大，费用越高。

**注意：** AI 读图会有误差，标注位置和形态判断不一定完全准确。请把它当作复盘参考，不是交易建议。

**常见问题：**

| 提示 | 解决办法 |
|---|---|
| `'python' 不是内部或外部命令` | 重新安装 Python，勾选 "Add python.exe to PATH" |
| `请在 .env 里设置 APP_PASSWORD` | 打开 `.env`，填写 `APP_PASSWORD=` 后面的密码 |
| `Incorrect API key` / `401` | 检查 `.env` 里的 `OPENAI_API_KEY` |
| `insufficient_quota` / `429` | 去 platform.openai.com → Billing 充值 |
| 浏览器没有自动打开 | 手动打开 http://localhost:8000 |
| 端口被占用 | 把 `config.toml` 里的 `web_port` 改成 8001 之类 |

不打开网页也能测试：`python chart_review.py 我的图.png --note "MYM 5分钟"`，结果保存在 `reviews/` 文件夹。

---

## 📱 Telegram（可选）

在 `.env` 里填上 `TELEGRAM_BOT_TOKEN`（从 @BotFather 获取，步骤见下方 "Create your Telegram bot"），并先给你的机器人发一句 `hi`。启动时会自动获取 chat ID，Telegram 机器人会和网页一起在后台运行：
- 在 Telegram 里发截图，也能收到复盘图（和网页共用历史记录）
- 新闻提醒（见下方）
- 以后的入场 / 出场提醒会走这里

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
