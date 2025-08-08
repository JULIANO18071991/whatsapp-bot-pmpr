
from flask import Flask, request
import requests
import os

app = Flask(__name__)

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

@app.route("/", methods=["GET"])
def home():
    return "OK v4"

@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "Forbidden", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    try:
        if "entry" in data and len(data["entry"]) > 0:
            changes = data["entry"][0].get("changes", [])
            if changes and "value" in changes[0]:
                value = changes[0]["value"]
                messages = value.get("messages", [])
                if messages:
                    for message in messages:
                        from_number = message.get("from")
                        text_body = message.get("text", {}).get("body", "")

                        print(f"Mensagem recebida de {from_number}: {text_body}")

                        # Ajusta número brasileiro se necessário
                        if from_number.startswith("+55") and len(from_number) == 13:
                            from_number = from_number[:5] + "9" + from_number[5:]

                        send_whatsapp_message(from_number, f"Você disse: {text_body}")
    except Exception as e:
        print(f"Erro ao processar: {e}")

    return "EVENT_RECEIVED", 200

def send_whatsapp_message(to, message):
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "text": {"body": message}
    }
    response = requests.post(url, headers=headers, json=payload)
    print("==== Graph API Response ====")
    print("Status:", response.status_code)
    print("Body:", response.text)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
