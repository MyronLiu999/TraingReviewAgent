# Trading Review Agent 交易复盘助手

## 📊 功能一：图表复盘（Chart Review）

**在 Telegram 里把行情截图发给机器人，它会回给你一张标注好的复盘图**，风格类似 Al Brooks 的价格行为标注：
- 用色块标出市场阶段：**Always In Long**（绿）、**Always In Short**（红）、**交易区间**（蓝框）
- 用圆圈标出关键 K 线：高潮、反转点、双顶/双底、更高低点/更低高点
- 用带箭头的标签说明每个形态（英文术语 + 中文解释）
- 右上角是复盘总结；聊天里还会附一段文字分析（当时好的交易是什么、常见错误）

**用法：**
1. 截一张行情图（**建议带上右侧价格轴**）。
2. 在 Telegram 里发给你的机器人。最好以"文件"方式发送，画质更清楚；直接发照片也可以。
3. 可以加一句说明（caption），例如：`MYM 5分钟，我在 52,250 做空，止损被打`。
4. 等 30–90 秒，机器人回复复盘图和分析。

**你的学习资料：** 把资料放进 `docs/` 文件夹（支持 `.md`、`.txt`、`.pdf`）。每次复盘，机器人都会读这些资料，并优先使用资料里的术语和规则。

**不用 Telegram 也能测试：**
```
python chart_review.py 我的图.png --note "MYM 5分钟"
```
结果保存在 `reviews/` 文件夹。

**费用：** 每张图大约 US$0.03–0.08（使用 `gpt-4o`，含一轮自我纠错）。可以在 `config.toml` 里调整：
- `review_refine_passes = 0`：跳过纠错，更快、更便宜，但标注位置可能偏一些
- `enable_news_alerts = false`：只做复盘，不推送新闻

**注意：** AI 读图会有误差，标注位置和形态判断不一定完全准确，请把它当作复盘参考，不是交易建议。

## 📰 功能二：新闻提醒（News Alerts）

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

### Step 4: Run the start script the first time
- **Windows:** double-click `start.bat`.
- **Mac:** open Terminal, type `cd ` (with a space), drag the bot folder into the window, press Enter, then run `./start.sh`.

It installs everything (about a minute) and then creates a file named **`.env`** in the folder. On Windows, Notepad opens it automatically; on a Mac, open it with TextEdit.

### Step 5: Put your two keys into `.env`
Fill in the first two lines and **leave `TELEGRAM_CHAT_ID` empty**:
```
OPENAI_API_KEY=sk-...your key...
TELEGRAM_BOT_TOKEN=7712345678:AAHk3x...your token...
TELEGRAM_CHAT_ID=
```
Save the file.

### Step 6: Run the start script again
Double-click `start.bat` (Windows) or run `./start.sh` (Mac) again. It:
1. **Finds your Telegram chat ID automatically** and saves it. Your bot sends you **"✅ Connected!"**. (If it says "No messages found", send your bot `hi` in Telegram and run the script again.)
2. Tests OpenAI on a sample headline.
3. Starts the bot. You get **"🤖 News alert bot started"** in Telegram.

From now on, just double-click `start.bat` (or run `./start.sh`) whenever you want the bot running. **Keep the window open** while you trade; close it to stop.

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
