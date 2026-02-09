# bot.py
# -*- coding: utf-8 -*-

import os
import logging
import requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

from topk_client import buscar_topk_multi
from llm_client import gerar_resposta
from dedup import Dedup
from synonyms import expand_query

DEBUG = os.getenv("DEBUG", "0") == "1"

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("bot")

app = Flask(__name__)

# Deduplicador global (TTL em segundos)
dedup = Dedup(ttl=600)


def _wa_post(phone_id: str, payload: dict):
    """POST no endpoint /messages com log do retorno."""
    token = os.getenv("WHATSAPP_TOKEN")
    api_version = os.getenv("WHATSAPP_API_VERSION", "v22.0")
    url = f"https://graph.facebook.com/{api_version}/{phone_id}/messages"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    r = requests.post(url, headers=headers, json=payload, timeout=20)

    # Loga sempre a resposta
    try:
        log.info(f"[WA] status={r.status_code} resp={r.json()}")
    except Exception:
        log.info(f"[WA] status={r.status_code} resp_text={r.text}")

    return r


def enviar_whatsapp_texto(phone_id: str, to: str, text: str):
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text}
    }
    return _wa_post(phone_id, payload)


def enviar_whatsapp_template(phone_id: str, to: str):
    template_name = os.getenv("WHATSAPP_TEMPLATE_NAME", "hello_world")
    template_lang = os.getenv("WHATSAPP_TEMPLATE_LANG", "en_US")

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": template_lang}
        }
    }
    return _wa_post(phone_id, payload)


def enviar_whatsapp(phone_id: str, to: str, text: str):
    """
    Tenta enviar texto.
    Se der erro comum de janela/reativação, tenta template como fallback.
    """
    r = enviar_whatsapp_texto(phone_id, to, text)

    # Se OK, encerra
    if r.ok:
        return

    # Tenta entender se é erro de janela 24h / precisa template
    try:
        data = r.json()
        msg = (data.get("error") or {}).get("message", "")
    except Exception:
        msg = r.text or ""

    lowered = msg.lower()
    needs_template = any(
        k in lowered for k in [
            "template", "outside", "24", "re-engagement", "reengagement",
            "not allowed", "message type"
        ]
    )

    if needs_template:
        log.warning("[WA] Texto falhou; tentando TEMPLATE (provável janela 24h).")
        enviar_whatsapp_template(phone_id, to)


@app.post("/webhook")
def webhook():
    data = request.get_json(force=True)

    try:
        value = data["entry"][0]["changes"][0]["value"]

        # Ignora eventos que não são mensagens (ex: statuses)
        if "messages" not in value:
            return jsonify({"ignored": True, "reason": "no_messages"}), 200

        msg = value["messages"][0]
        phone_id = value["metadata"]["phone_number_id"]
        from_ = msg["from"]
        text = msg.get("text", {}).get("body", "")

        message_id = msg.get("id")
        if not message_id:
            log.warning("Mensagem sem ID, ignorando por segurança.")
            return jsonify({"ok": True}), 200

        if not text:
            log.info("[MSG] Recebida mensagem sem texto (talvez mídia).")
            return jsonify({"ok": True}), 200

    except Exception as e:
        log.debug(f"Webhook ignorado: {e}")
        return jsonify({"ignored": True}), 200

    # DEDUPLICAÇÃO
    if dedup.seen(message_id):
        log.info(f"[DEDUP] Mensagem duplicada ignorada: {message_id}")
        return jsonify({"ok": True}), 200

    log.info(f"[MSG NOVA] {from_}: {text}")

    # EXPANSÃO DE SINÔNIMOS NA CONSULTA
    query = expand_query(text)

    # BUSCA MULTI-COLEÇÃO
    resultados = buscar_topk_multi(query, k=5)

    if not resultados:
        enviar_whatsapp(phone_id, from_, "Não encontrei base normativa para responder sua pergunta.")
        return jsonify({"ok": True}), 200

    # LLM — UMA ÚNICA CHAMADA
    resposta = gerar_resposta(text, resultados)
    enviar_whatsapp(phone_id, from_, resposta)

    return jsonify({"ok": True}), 200


@app.post("/send-message")
def send_message():
    """
    Endpoint para enviar mensagens via WhatsApp sob demanda (ideal pro Manus)

    AUTH (recomendado): header
      Authorization: Bearer <ADMIN_TOKEN>

    Payload esperado:
    {
      "to": "5541997815018",
      "message": "Texto da mensagem"
    }
    """
    try:
        # Auth via header (melhor que token no body)
        admin_token = os.getenv("ADMIN_TOKEN")
        if admin_token:
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {admin_token}":
                log.warning("[SEND-MESSAGE] Authorization inválida")
                return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True)

        if not data.get("to"):
            return jsonify({"error": "Campo 'to' é obrigatório"}), 400
        if not data.get("message"):
            return jsonify({"error": "Campo 'message' é obrigatório"}), 400

        to = data["to"]
        message = data["message"]

        phone_id = os.getenv("WHATSAPP_PHONE_ID")
        if not phone_id:
            return jsonify({"error": "WHATSAPP_PHONE_ID não configurado"}), 500

        log.info(f"[SEND-MESSAGE] Enviando para {to}: {message[:60]}...")
        enviar_whatsapp(phone_id, to, message)

        return jsonify({
            "success": True,
            "to": to,
            "message_length": len(message)
        }), 200

    except Exception as e:
        log.error(f"[SEND-MESSAGE] Erro: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=DEBUG)
