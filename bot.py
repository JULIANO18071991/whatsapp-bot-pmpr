# bot.py
# -*- coding: utf-8 -*-

import os, json, logging, requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

from topk_client import buscar_topk_multi
from llm_client import gerar_resposta
from dedup import Dedup   # 🔹 DEDUP AQUI

DEBUG = os.getenv("DEBUG", "0") == "1"

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("bot")

app = Flask(__name__)

# 🔒 Deduplicador global (TTL em segundos)
dedup = Dedup(ttl=600)

def enviar_whatsapp(phone_id, to, text):
    token = os.getenv("WHATSAPP_TOKEN")
    api_version = os.getenv("WHATSAPP_API_VERSION", "v20.0")
    url = f"https://graph.facebook.com/{api_version}/{phone_id}/messages"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text}
    }

    requests.post(url, headers=headers, json=payload, timeout=15)

@app.post("/webhook")
def webhook():
    data = request.get_json(force=True)

    try:
        value = data["entry"][0]["changes"][0]["value"]
        msg = value["messages"][0]

        phone_id = value["metadata"]["phone_number_id"]
        from_ = msg["from"]
        text = msg["text"]["body"]

        # 🔑 ID ÚNICO DA MENSAGEM (OFICIAL DA META)
        message_id = msg.get("id")
        if not message_id:
            log.warning("Mensagem sem ID, ignorando por segurança.")
            return jsonify({"ok": True}), 200

    except Exception as e:
        log.debug(f"Webhook ignorado: {e}")
        return jsonify({"ignored": True}), 200

    # 🔒 DEDUPLICAÇÃO
    if dedup.seen(message_id):
        log.info(f"[DEDUP] Mensagem duplicada ignorada: {message_id}")
        return jsonify({"ok": True}), 200

    log.info(f"[MSG NOVA] {from_}: {text}")

    # 🔍 BUSCA MULTI-COLEÇÃO
    resultados = buscar_topk_multi(text, k=5)

    if not resultados:
        enviar_whatsapp(
            phone_id,
            from_,
            "Não encontrei base normativa para responder sua pergunta."
        )
        return jsonify({"ok": True}), 200

    # 🧠 LLM — UMA ÚNICA CHAMADA
    resposta = gerar_resposta(text, resultados)

    enviar_whatsapp(phone_id, from_, resposta)

    return jsonify({"ok": True}), 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=DEBUG)
