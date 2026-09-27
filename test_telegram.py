import requests

TELEGRAM_BOT_TOKEN = "8825840797:AAEZ-O3KUH8duOm5JRsRwl5NZjgl_qAOH40"
TELEGRAM_CHAT_ID = "727756988"

url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
data = {
    "chat_id": TELEGRAM_CHAT_ID,
    "text": "🧪 <b>Prueba de conexión Biwenger Bot</b>\nSi lees esto, ¡GitHub Actions se conecta perfectamente a tu Telegram!",
    "parse_mode": "HTML"
}

res = requests.post(url, data=data)
print("Respuesta de Telegram:", res.status_code, res.text)
