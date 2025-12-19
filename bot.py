# bot.py
# -*- coding: utf-8 -*-

import os, json, time, logging, requests
from collections import deque
from flask import Flask, request, jsonify
from dotenv import load_dotenv
import redis

load_dotenv()

# 🔁 IMPORT ALTERADO
from topk_client import buscar_topk_multi
from llm_client import gerar_resposta

DEBUG = os.getenv("DEBUG", "0") == "1"

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("bot")

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "verify_token_padrao")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v20.0")
DEFAULT_PHONE_ID = os.getenv("WHATSAPP_PHONE_ID")

# =========================
# MEMÓRIA / DEDUP
# =========================
memoria = None
dedup = None

try:
    from memory_redis import RedisMemory, Dedup
    memoria = RedisMemory()
    dedup = Dedup(ttl=3600)
    log.info("Memória Redis ativa.")
except Exception:
    from memory import Memory
    memoria = Memory(max_msgs=6)
    _recent_ids = set()

    def _seen_local(msg_id):
        if not msg_id:
            return False
        if msg_id in _recent_ids:
            return True
        _recent_ids.add(msg_id)
        return False


# ===============================
# LOG CIENTÍFICO NO REDIS
# ===============================
REDIS_URL = os.getenv("REDIS_URL")

redis_log_client = None
if REDIS_URL:
    try:
        redis_log_client = redis.from_url(REDIS_URL, decode_responses=True)
        redis_log_client.ping()
        log.info("Redis conectado para log científico.")
    except Exception as e:
        log.error("Erro ao conectar Redis: %s", e)


def salvar_log(numero, mensagem, msg_id):
    if not redis_log_client:
        return

    data_hora = time.strftime("%d/%m/%Y %H:%M:%S")

    registro = {
        "numero": numero,
        "mensagem": mensagem,
        "dataHora": data_hora,
        "msg_id": msg_id
    }

    try:
        redis_log_client.rpush("logs:global", json.dumps(registro, ensure_ascii=False))
        redis_log_client.rpush(f"logs:usuario:{numero}", json.dumps(registro, ensure_ascii=False))
        log.info("LOG SALVO: %s", registro)
    except Exception as e:
        log.error("Erro ao salvar log no Redis: %s", e)


# =========================
# FLASK
# =========================
app = Flask(__name__)


def _wa_url(phone_id):
    return f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{phone_id}/messages"


def enviar_whatsapp(phone_id, to, text):
    if not (WHATSAPP_TOKEN and phone_id and to and text):
        return False

    url = _wa_url(phone_id)
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text}
    }

    try:
        r = requests.post(url, headers=headers, json=data, timeout=15)
        return r.status_code == 200
    except Exception:
        return False


def _as_list(x, default=None):
    return x if isinstance(x, list) else (default or [])


def _as_dict(x, default=None):
    return x if isinstance(x, dict) else (default or {})


def _extract_wa(payload):
    entry = _as_list(payload.get("entry"))
    if not entry:
        return None, None, None, None

    changes = _as_list(_as_dict(entry[0]).get("changes"))
    value = _as_dict(changes[0]).get("value", {}) if changes else {}

    phone_id = _as_dict(value.get("metadata")).get("phone_number_id") or DEFAULT_PHONE_ID
    messages = _as_list(value.get("messages"))

    if not messages:
        return phone_id, None, None, None

    msg = _as_dict(messages[0])
    msg_id = msg.get("id")
    from_ = msg.get("from")

    text = None
    if "text" in msg and "body" in msg["text"]:
        text = msg["text"]["body"]

    return phone_id, from_, text, msg_id


def _tem_base(trechos):
    return any((t.get("trecho") or "").strip() for t in trechos)


# =========================
# WEBHOOK PRINCIPAL
# =========================
@app.post("/webhook")
def webhook():
    try:
        payload = request.get_json(silent=True, force=True) or {}

        phone_id, from_, text, msg_id = _extract_wa(payload)

        if not (phone_id and from_ and text):
            return jsonify({"ignored": True}), 200

        # LOG
        salvar_log(from_, text, msg_id)

        # DEDUP
        if dedup:
            if msg_id and dedup.seen(msg_id):
                return jsonify({"dedup": True}), 200
        else:
            if '_seen_local' in globals() and _seen_local(msg_id):
                return jsonify({"dedup": True}), 200

        # CONTEXTO DE CONVERSA
        contexto = memoria.get_context(from_) if hasattr(memoria, "get_context") else []

        # 🔥 BUSCA MULTI-COLEÇÃO
        resultados = buscar_topk_multi(text, k=5) or {}

        # MERGE DOS TRECHOS
        trechos = []
        for _, lista in resultados.items():
            trechos.extend(lista)

        if not _tem_base(trechos):
            enviar_whatsapp(
                phone_id,
                from_,
                "Não encontrei base normativa para responder sua pergunta."
            )
            return jsonify({"ok": True}), 200

        # LLM
        resposta = gerar_resposta(text, trechos, contexto) or "Não consegui gerar resposta."

        enviar_whatsapp(phone_id, from_, resposta)

        # MEMÓRIA
        if hasattr(memoria, "add_user_msg"):
            memoria.add_user_msg(from_, text)
        if hasattr(memoria, "add_assistant_msg"):
            memoria.add_assistant_msg(from_, resposta)

        return jsonify({"ok": True}), 200

    except Exception as e:
        log.error("Webhook error: %s", e)
        return jsonify({"ok": False}), 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=DEBUG)
