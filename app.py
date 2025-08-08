
from flask import Flask, request
import requests, os, json

app = Flask(__name__)

VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "meu_token_secreto")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
GRAPH_URL = f"https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/messages"

@app.route("/", methods=["GET"])
def home():
    return "OK", 200

# Verificação do webhook (GET)
@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("Webhook verificado ✅")
        return challenge, 200
    print("Falha na verificação do webhook ❌")
    return "Erro de verificação", 403

# Recebimento de mensagens (POST)
@app.route("/webhook", methods=["POST"])
def receive_message():
    data = request.get_json()
    print("==== Payload recebido ====")
    print(json.dumps(data, ensure_ascii=False, indent=2))

    try:
        value = data.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {})
        messages = value.get("messages", [])
        if not messages:
            return "EVENT_RECEIVED", 200

        msg = messages[0]
        from_number = msg.get("from")
        text = ""
        if msg.get("type") == "text":
            text = msg.get("text", {}).get("body", "")

        print(f"Mensagem recebida de {from_number}: {text!r}")
        if from_number:
            reply = f"Recebi sua mensagem: {text or '(sem texto)'}"
            send_text(from_number, reply)
    except Exception as e:
        print("Erro ao processar:", e)

    return "EVENT_RECEIVED", 200

def send_text(to, text):
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text}
    }
    print("==== Enviando mensagem ====")
    print(json.dumps(payload, ensure_ascii=False))
    r = requests.post(GRAPH_URL, headers=headers, json=payload, timeout=20)
    print("Graph API status:", r.status_code)
    try:
        print("Graph API body:", r.json())
    except Exception:
        print("Graph API body (raw):", r.text)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
    
