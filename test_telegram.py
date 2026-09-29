import os
import requests

token = os.environ["TELEGRAM_BOT_TOKEN"]

url = f"https://api.telegram.org/bot{token}/getUpdates"

response = requests.get(url, timeout=30)
print("HTTP status:", response.status_code)

data = response.json()
print("Telegram response:", data)

if not data.get("ok"):
    raise RuntimeError(data)

for update in data.get("result", []):
    message = update.get("message", {})
    chat = message.get("chat", {})

    print(
        "FOUND CHAT:",
        "id =", chat.get("id"),
        "type =", chat.get("type"),
        "name =", chat.get("first_name", "")
    )