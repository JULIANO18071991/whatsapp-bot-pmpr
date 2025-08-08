
from flask import Flask, request
import requests, os, json

app = Flask(__name__)

print("==== RUNNING VERSION v5 (normalize + BR 9) ====")

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "meu_token_secreto")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")

GRAPH_URL = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"

def normalize_msisdn(wa_id: str) -> str:
    """
    Garante formato E.164 (+) e insere o 9 se vier numero movel BR sem o 9.
    Exemplos:
      '554197815018'  -> '+5541997815018'
      '+554197815018' -> '+5541997815018'
      '41997815018'   -> '+5541997815018'
      '+5541997815018'-> '+5541997815018' (sem mudanca)
    """
    if not wa_id:
        return wa_id
    s = str(wa_id).strip()
    # remove + para manipular
    if s.startswith('+'):
        s = s[1:]
    # adiciona 55 se veio so com DDD/local
    if len(s) in (10, 11) and not s.startswith('55'):
        s = '55' + s
    # se é BR e tem 12 digitos (sem o 9), coloca o 9
    if s.startswith('55') and len(s) == 12:
        ddd = s[2:4]
        local = s[4:]
        if not local.startswith('9'):
            s = f'55{ddd}9{local}'
    # volta com +
    return '+' + s

@app.route("/", methods=["GET"])
def root():
    return "OK v5", 200

@app.route("/webhook", methods=["GET"])
def verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("Webhook verificado com sucesso.")
        return challenge, 200
    return "Forbidden", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True) or {}
    print("==== Incoming Payload ====")
    try:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception:
        print(data)
    print("==========================")

    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {}) or {}
                messages = value.get("messages", [])
                if not messages:
                    print("Evento sem 'messages' (status/template/etc). Ignorando.")
                    continue

                for msg in messages:
                    from_number = msg.get("from")
                    to = normalize_msisdn(from_number)
                    text = (msg.get("text") or {}).get("body", "").strip() if msg.get("type") == "text" else ""

                    print(f"Mensagem recebida de {from_number} -> normalizado {to}: {text or '(sem texto)'}")
                    send_text(to, f"Recebi sua mensagem: {text or '(sem texto)'}")

    except Exception as e:
        print("Erro ao processar payload:", e)

    return "EVENT_RECEIVED", 200

def send_text(to: str, text: str):
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
    r = requests.post(GRAPH_URL, headers=headers, json=payload, timeout=20)
    print("==== Graph API Response ====")
    print("Destinatario:", to)
    print("Status:", r.status_code)
    try:
        print("Body:", r.json())
    except Exception:
        print("Body (raw):", r.text)
    print("============================")

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
