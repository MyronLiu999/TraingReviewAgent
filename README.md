# News Alert Bot: stock and gold news impact, sent to Telegram

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
