import os
import logging
from flask import Flask, request, jsonify, Response
from utils.config import Settings
from utils.whatsapp import WhatsAppClient
from utils.autorag import AutoRAGClient
from utils.openai_client import LLMClient
from utils.prompt import build_prompt

app = Flask(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = app.logger

settings = Settings.from_env()
wa = WhatsAppClient(settings)
rag = AutoRAGClient(settings)
llm = LLMClient(settings)


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "pm-whatsapp-bot", "env": settings.ENV})


@app.get("/webhook")
def webhook_verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == settings.VERIFY_TOKEN:
        return Response(challenge, status=200)
    return Response("forbidden", status=403)


def _extract_text_entry(body: dict):
    try:
        entry = body["entry"][0]
        change = entry["changes"][0]
        value = change["value"]
        messages = value.get("messages", [])
        if not messages:
            return None, None, None
        msg = messages[0]
        msg_type = msg.get("type")
        if msg_type != "text":
            return None, None, None
        from_id = msg.get("from")
        message_id = msg.get("id")
        text = msg["text"]["body"].strip()
        return from_id, message_id, text
    except Exception:
        return None, None, None


@app.post("/webhook")
def webhook_receive():
    body = request.get_json(silent=True) or {}
    from_id, message_id, text = _extract_text_entry(body)
    if not all([from_id, message_id, text]):
        return jsonify({"ignored": True}), 200

    if wa.is_own_message(body):
        return jsonify({"ignored": "own_message"}), 200

    try:
        wa.mark_read(message_id)
    except Exception as e:
        log.warning(f"mark_read falhou: {e}")

    try:
        passages = rag.retrieve(text)
    except Exception as e:
        log.exception("AutoRAG retrieve falhou")
        passages = []

    try:
        prompt = build_prompt(user_query=text, passages=passages)
        answer = llm.chat(prompt)
    except Exception as e:
        log.exception("LLM falhou")
        answer = ("Não consegui gerar uma resposta agora. "
                  "Tente novamente em instantes.")

    try:
        wa.send_text(to=from_id, text=answer)
    except Exception as e:
        log.exception("Falha ao enviar resposta no WhatsApp")
        return jsonify({"error": "send_failed"}), 500

    return jsonify({"ok": True})


@app.post("/admin/reindex")
def admin_reindex():
    auth = request.headers.get("X-Admin-Token")
    if auth != settings.ADMIN_TOKEN:
        return Response("forbidden", status=403)
    try:
        job = rag.reindex()
        return jsonify({"ok": True, "job": job})
    except Exception as e:
        log.exception("reindex falhou")
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
