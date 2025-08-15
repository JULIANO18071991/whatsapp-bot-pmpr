import os
import json
import logging
from flask import Flask, request, Response
import requests

app = Flask(__name__)

# Logs
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = app.logger

# Env vars
VERIFY_TOKEN     = os.getenv("VERIFY_TOKEN")
WHATSAPP_TOKEN   = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID  = os.getenv("PHONE_NUMBER_ID")

# Graph API
GRAPH_VERSION = "v21.0"
GRAPH_BASE    = f"https://graph.facebook.com/{GRAPH_VERSION}"

def _headers():
    return {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}

def send_mark_read(message_id: str):
    """Marca a mensagem como lida (best-effort)."""
    url = f"{GRAPH_BASE}/{PHONE_NUMBER_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id
    }
    r = requests.post(url, headers=_headers(), json=payload, timeout=10)
    if r.status_code >= 400:
        logger.warning("mark_read falhou: %s - %s", r.status_code, r.text)
    return r.json() if r.ok else None

def send_text(to: str, body: str):
    """Envia texto simples."""
    url = f"{GRAPH_BASE}/{PHONE_NUMBER_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to,  # use exatamente o 'from' do webhook
        "text": {"body": body}
    }
    r = requests.post(url, headers=_headers(), json=payload, timeout=10)
    r.raise_for_status()
    return r.json()

@app.get("/health")
def health():
    return {
        "ok": True,
        "phone_number_id": PHONE_NUMBER_ID,
        "graph_version": GRAPH_VERSION
    }

@app.get("/webhook")
def verify():
    """Verificação do webhook (Meta chama GET com hub.challenge)."""
    mode      = request.args.get("hub.mode")
    token     = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return Response(challenge or "", status=200, mimetype="text/plain")
    return Response(status=403)

@app.post("/webhook")
def webhook():
    """Recebe eventos do WhatsApp (mensagens e status)."""
    try:
        data = request.get_json(force=True, silent=True) or {}
        logger.info("Incoming payload: %s", json.dumps(data, ensure_ascii=False))

        changes = (data.get("entry") or [{}])[0].get("changes") or []
        if not changes:
            return Response(status=200)

        value = changes[0].get("value") or {}
        messages = value.get("messages") or []
        statuses = value.get("statuses") or []

        # 1) Mensagens recebidas
        if messages:
            msg    = messages[0]
            msg_id = msg.get("id")
            from_  = msg.get("from")  # ex.: "55419..."
            text   = (msg.get("text") or {}).get("body", "").strip()

            # marca como lida (best-effort)
            if msg_id:
                try:
                    send_mark_read(msg_id)
                except Exception as e:
                    logger.warning("mark_read exception: %s", e)

            # resposta simples (eco) — aqui depois entra sua lógica/IA
            reply = f"Você disse: {text}" if text else "Recebi sua mensagem."
            try:
                resp = send_text(from_, reply)
                logger.info("Mensagem enviada: %s", json.dumps(resp, ensure_ascii=False))
            except requests.HTTPError as e:
                status = getattr(e.response, "status_code", "NA")
                body   = getattr(e.response, "text", "")
                logger.error("Erro ao enviar (%s): %s", status, body)

        # 2) Status (sent/delivered/read/failed)
        elif statuses:
            st = statuses[0]
            logger.info("Status: %s  msgId: %s  dest: %s",
                        st.get("status"), st.get("id"), st.get("recipient_id"))

        return Response(status=200)
    except Exception as e:
        logger.exception("Erro no webhook: %s", e)
        # sempre 200 pra Meta não reenfileirar
        return Response(status=200)
