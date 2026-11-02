"""Finds your Telegram chat ID and saves it into .env.

1. Put TELEGRAM_BOT_TOKEN in .env
2. In Telegram, open your bot and send it any message (e.g. "hi")
3. Run:  python get_chat_id.py
"""

import os
import re
import sys

import requests

ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def read_token() -> str:
    if not os.path.exists(ENV):
        sys.exit("No .env file found. Copy .env.example to .env and add TELEGRAM_BOT_TOKEN first.")
    for line in open(ENV, encoding="utf-8"):
        if line.strip().startswith("TELEGRAM_BOT_TOKEN="):
            token = line.split("=", 1)[1].strip().strip('"').strip("'")
            if token and "..." not in token:
                return token
    sys.exit("TELEGRAM_BOT_TOKEN is missing in .env. Get it from @BotFather in Telegram.")


def main() -> None:
    token = read_token()
    r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15)
    data = r.json()
    if not data.get("ok"):
        sys.exit(f"Telegram rejected the token: {data.get('description')}. "
                 "Copy the token from @BotFather again.")

    chats = {}
    for upd in data.get("result", []):
        chat = (upd.get("message") or {}).get("chat")
        if chat:
            chats[chat["id"]] = chat.get("first_name") or chat.get("title") or ""
    if not chats:
        sys.exit("No messages found. Open your bot in Telegram, send it 'hi', then run this again.")

    chat_id, name = list(chats.items())[-1]  # most recent chat
    print(f"Found chat: {name} -> chat ID {chat_id}")

    text = open(ENV, encoding="utf-8").read()
    if re.search(r"^TELEGRAM_CHAT_ID=.*$", text, re.M):
        text = re.sub(r"^TELEGRAM_CHAT_ID=.*$", f"TELEGRAM_CHAT_ID={chat_id}", text, flags=re.M)
    else:
        text = text.rstrip("\n") + f"\nTELEGRAM_CHAT_ID={chat_id}\n"
    open(ENV, "w", encoding="utf-8").write(text)
    print("Saved TELEGRAM_CHAT_ID into .env")

    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=15,
                  json={"chat_id": chat_id, "text": "✅ Connected! News alerts will arrive in this chat."})
    print("Sent a test message to your Telegram. You're ready to run the bot.")


if __name__ == "__main__":
    main()
