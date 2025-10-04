# bot.py
# -*- coding: utf-8 -*-
import os, json, time, logging, requests, traceback
from flask import Flask, request, jsonify
from dotenv import load_dotenv

from memory import Memory
from topk_client import buscar_topk  # compat com seu código atual
from llm_client import gerar_resposta

load_dotenv()

app = Flask(__name__)

# ENV (mantendo nomes que você já usa)
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "verify_token_padrao")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")  # OBRIGATÓRIO
WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v20.0")
DEFAULT_PHONE_ID = os.getenv("WHATSAPP_PHONE_ID")  # opcional (pode vir no payload)

DEBUG = os.getenv("DEBUG", "0") == "1"
logging.basicConfig(level=logging.DEBUG if DEBUG else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bot")

memoria = Memory(max_msgs=6)

def _wa_url(phone_id: str) -> str:
    return f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{phone_id}/messages"

def enviar_whatsapp(phone_id: str, to: str, text: str, max_retries: int = 3) -> bool:
    if not (WHATSAPP_TOKEN and phone_id and to and text is not None):
        log.error("Parâmetros faltando para enviar WhatsApp.")
        return False
    url = _wa_url(phone_id)
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}}

    for attempt in range(1, max_retries + 1):
        try:
            r = requests.post(url, headers=headers, json=data, timeout=20)
            if 200 <= r.status_code < 300:
                return True
            if r.status_code in (429, 500, 502, 503, 504):
                wait = min(2 ** attempt, 8)
                log.warning("WA %s; retry em %ss; body=%s", r.status_code, wait, r.text[:500])
                time.sleep(wait)
                continue
            log.error("Falha WA %s: %s", r.status_code, r.text[:800])
            return False
        except requests.RequestException as e:
            wait = min(2 ** attempt, 8)
            log.warning("Erro rede WA: %s; retry em %ss", e, wait)
            time.sleep(wait)
    return False

def _as_list(x, default):
    return x if isinstance(x, list) else default

def _as_dict(x, default):
    return x if isinstance(x, dict) else default

def _extract_wa(payload: dict):
    """
    Retorna (phone_id, from_, text, msg_id) do payload do WhatsApp.
    """
    entry = _as_list(payload.get("entry"), [])
    if not entry: return None, None, None, None
    value = _as_dict(_as_list(_as_dict(entry[0], {}).get("changes"), [])[0], {}).get("value", {})
    phone_id = _as_dict(value.get("metadata"), {}).get("phone_number_id") or DEFAULT_PHONE_ID
    messages = _as_list(value.get("messages"), [])
    if not messages: return phone_id, None, None, None

    msg = _as_dict(messages[0], {})
    msg_id = msg.get("id")
    from_ = msg.get("from")
    text = None
    if "text" in msg and "body" in msg["text"]:
        text = msg["text"]["body"]
    elif msg.get("type") == "interactive":
        inter = _as_dict(msg.get("interactive"), {})
        if "button_reply" in inter:
            text = _as_dict(inter["button_reply"], {}).get("title")
        elif "list_reply" in inter:
            text = _as_dict(inter["list_reply"], {}).get("title")
    return phone_id, from_, (text or "").strip() if text else None, msg_id

# ---------- routes ----------
@app.get("/")
def root():
    return jsonify({"ok": True, "service": "whatsapp-bot"}), 200

@app.get("/health")
def health():
    return jsonify({"ok": True}), 200

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
    try:
        payload = request.get_json(silent=True, force=True) or {}
        if DEBUG:
            log.debug("payload: %s", json.dumps(payload, ensure_ascii=False)[:1200])

        phone_id, from_, text, msg_id = _extract_wa(payload)
        if not (phone_id and from_ and text):
            return jsonify({"ignored": True}), 200

        # contexto antigo: string única
        contexto = memoria.get_context(from_)  # string
        trechos = buscar_topk(text, k=5) or []

        resposta = gerar_resposta(text, trechos, contexto)
        memoria.add_msg(from_, text)

        # fragmenta envio
        chunk = 3800
        ok_all = True
        for i in range(0, len(resposta), chunk):
            ok = enviar_whatsapp(phone_id, from_, resposta[i:i+chunk])
            ok_all = ok_all and ok
        return jsonify({"ok": ok_all}), 200

    except Exception as e:
        log.error("webhook error: %s", e)
        log.debug(traceback.format_exc())
        return jsonify({"ok": False}), 200

# ---------- dev ----------
if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=DEBUG)
