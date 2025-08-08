
from flask import Flask, request
import requests
import os
import json

app = Flask(__name__)

print("==== RUNNING VERSION v3 ====")

VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "meu_token_verificacao")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "")

@app.route("/", methods=["GET"])
def home():
    return "OK v3", 200

@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        verify_token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if verify_token == VERIFY_TOKEN:
            return challenge
        return "Token inválido", 403

    if request.method == "POST":
        try:
            data = request.get_json()
            print("==== Incoming Payload ====")
            print(json.dumps(data, indent=2, ensure_ascii=False))

            entry = data.get("entry", [])[0] if "entry" in data else None
            if entry:
                changes = entry.get("changes", [])[0] if "changes" in entry else None
                if changes:
                    value = changes.get("value", {})
                    messages = value.get("messages", [])
                    if messages:
                        for message in messages:
                            from_number = message.get("from")
                            text_body = message.get("text", {}).get("body", "").lower()
                            print(f"Mensagem recebida de {from_number}: {text_body}")
                            send_whatsapp_message(from_number, f"Você disse: {text_body}")
                    else:
                        print("Evento recebido, mas sem mensagens.")
            return "EVENT_RECEIVED", 200
        except Exception as e:
            print(f"Erro ao processar: {e}")
            return "Erro interno", 500

def send_whatsapp_message(to, message):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message}
    }
    try:
        response = requests.post(url, headers=headers, json=payload)
        print("==== Graph API Response ====")
        print(f"Status: {response.status_code}")
        print(f"Body: {response.text}")
    except Exception as e:
        print(f"Erro ao enviar mensagem: {e}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
