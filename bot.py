import os
from flask import Flask, request, jsonify
from dotenv import load_dotenv
import requests

from memory import Memory
from topk_client import buscar_topk
from llm_client import gerar_resposta

load_dotenv()

app = Flask(__name__)

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID = os.environ.get("WHATSAPP_PHONE_ID", "")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "verify_token_padrao")

memoria = Memory(max_msgs=3)

@app.get("/webhook")
def verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "forbidden", 403

@app.post("/webhook")
def webhook():
    data = request.get_json(force=True, silent=True) or {}
    try:
        entry = data.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {})

        messages = value.get("messages", [])
        if not messages:
            return jsonify({"status": "ok (no messages)"}), 200

        msg = messages[0]
        from_ = msg.get("from")
        text = msg.get("text", {}).get("body", "").strip()

        if not text:
            enviar_whatsapp(from_, "Não entendi sua mensagem. Envie um texto, por favor.")
            return jsonify({"status": "ok"}), 200

        contexto = memoria.get_context(from_)
        resultados = buscar_topk(text)
        resposta = gerar_resposta(text, contexto, resultados)
        memoria.add_msg(from_, text)
        enviar_whatsapp(from_, resposta)

    except Exception as e:
        print("[ERRO webhook]", e)
    return jsonify({"status": "ok"}), 200

def enviar_whatsapp(to: str, body: str):
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_ID:
        print("[WARN] Variáveis do WhatsApp ausentes; resposta não enviada.")
        return
    url = f"https://graph.facebook.com/v17.0/{WHATSAPP_PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body[:4096]}
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        if r.status_code >= 300:
            print("[ERRO WA]", r.status_code, r.text[:400])
    except Exception as e:
        print("[ERRO WA req]", e)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
