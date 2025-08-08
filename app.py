
from flask import Flask, request
import os, json, requests

app = Flask(__name__)

VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "meu_token_secreto")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
GRAPH_URL = f"https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/messages"

@app.route("/", methods=["GET"])
def home():
    return "OK", 200

@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("Webhook verificado com sucesso.")
        return challenge, 200
    print("Falha na verificação do webhook.")
    return "Erro de verificação", 403

@app.route("/webhook", methods=["POST"])
def receive_message():
    data = request.get_json(silent=True) or {}
    print("==== Incoming Payload ====")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    print("==========================")

    # O payload do WhatsApp pode vir com eventos sem "messages" (status, templates, etc.)
    # Por isso tratamos com .get e iteramos com segurança.
    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                # Se não houver mensagens, apenas registra e continua
                if "messages" not in value:
                    print("Evento sem 'messages' (provavelmente status). Ignorando.")
                    continue

                for msg in value.get("messages", []):
                    from_number = msg.get("from")
                    text = ""

                    mtype = msg.get("type")
                    if mtype == "text":
                        text = (msg.get("text") or {}).get("body", "").strip()
                    elif mtype == "button":
                        text = (msg.get("button") or {}).get("text", "").strip()
                    elif mtype == "interactive":
                        inter = msg.get("interactive") or {}
                        if inter.get("type") == "button_reply":
                            text = (inter.get("button_reply") or {}).get("title", "").strip()
                        elif inter.get("type") == "list_reply":
                            text = (inter.get("list_reply") or {}).get("title", "").strip()

                    print(f"Mensagem recebida de {from_number}: {text}")

                    if not from_number:
                        continue

                    if not text:
                        send_text(from_number, "Recebi sua mensagem. Envie um *texto* para que eu possa ajudar.")
                    else:
                        send_text(from_number, f"Recebi sua mensagem: {text}")

    except Exception as e:
        print("Erro ao processar payload:", e)

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
    try:
        r = requests.post(GRAPH_URL, headers=headers, json=payload, timeout=15)
        print("==== Graph API Response ====")
        print("Status:", r.status_code)
        try:
            print("Body:", r.json())
        except Exception:
            print("Body (raw):", r.text)
        print("============================")
    except Exception as e:
        print("Erro ao enviar mensagem:", e)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
