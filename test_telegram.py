import os
import requests

token = os.environ["TELEGRAM_BOT_TOKEN"]
chat_id = os.environ["TELEGRAM_CHAT_ID"]

message = (
    "GitHub Actions Telegram test\n\n"
    "The notification system is working."
)

url = f"https://api.telegram.org/bot{token}/sendMessage"
response = requests.post(
    url,
    json={"chat_id": chat_id, "text": message},
    timeout=30,
)
print("Telegram HTTP status:", response.status_code)
print("Telegram response:", response.text)

response.raise_for_status()

data = response.json()
if not data.get("ok"):
    raise RuntimeError(data)

print("Telegram message sent successfully.")
