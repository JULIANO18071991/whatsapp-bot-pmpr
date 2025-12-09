# bot.py
# -*- coding: utf-8 -*-
import os, json, time, logging, requests, traceback
from collections import deque
from flask import Flask, request, jsonify
from dotenv import load_dotenv
import redis

load_dotenv()

from topk_client import buscar_topk, topk_status
from llm_client import gerar_resposta

DEBUG = os.getenv("DEBUG", "0") == "1"
logging.basicConfig(level=logging.DEBUG if DEBUG else logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bot")

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "verify_token_padrao")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v20.0")
DEFAULT_PHONE_ID = os.getenv("WHATSAPP_PHONE_ID")

if not WHATSAPP_TOKEN:
    log.warning("WHATSAPP_TOKEN ausente! Envio de mensagens falhará.")
if not DEFAULT_PHONE_ID:
    log.warning("WHATSAPP_PHONE_ID ausente! Usando phone_id do payload.")

# ---- memória (Redis se houver; fallback local) ----
memoria = None
dedup = None
try:
    from memory_redis import RedisMemory, Dedup
    memoria = RedisMemory()
    dedup = Dedup(ttl=3600)
    log.info("Memória: RedisMemory ativa.")
except Exception as e:
    log.warning("RedisMemory indisponível (%s). Tentando memória local…", e)
    try:
        from memory import Memory
        memoria = Memory(max_msgs=6)
        _recent_ids_q = deque(maxlen=200)
        _recent_ids_set = set()

        def _seen_local(msg_id: str | None) -> bool:
            if not msg_id:
                return False
            if msg_id in _recent_ids_set:
                return True
            _recent_ids_set.add(msg_id)
            _recent_ids_q.append(msg_id)
            return False
    except Exception:
        class _NullMem:
            def add_user_msg(self, u, m):
                pass

            def add_assistant_msg(self, u, m):
                pass

            def get_context(self, u):
                return []

        memoria = _NullMem()
        _recent_ids_q = deque(maxlen=200)
        _recent_ids_set = set()

        def _seen_local(msg_id: str | None) -> bool:
            return False

# ===============================
# LOG CIENTÍFICO EM REDIS ✅
# ===============================
REDIS_URL = os.getenv("REDIS_URL")

redis_log_client = None
if REDIS_URL:
    try:
        redis_log_client = redis.from_url(REDIS_URL, decode_responses=True)
        redis_log_client.ping()
        log.info("Redis conectado para LOG científico.")
    except Exception as e:
        log.error("Falha ao conectar Redis (LOG): %s", e)
        redis_log_client = None
else:
    log.warning("REDIS_URL não definida. LOG científico desativado.")


def salvar_log(numero: str, mensagem: str, msg_id: str | None):
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
        registro_json = json.dumps(registro, ensure_ascii=False)
        redis_log_client.rpush("logs:global", registro_json)
        redis_log_client.rpush(f"logs:usuario:{numero}", registro_json)
    except Exception as e:
        log.error("ERRO AO SALVAR LOG CIENTÍFICO: %s", e)


app = Flask(__name__)


def _wa_url(phone_id: str) -> str:
    return f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{phone_id}/messages"


def enviar_whatsapp(phone_id: str, to: str, text: str, max_retries: int = 3) -> bool:
    if not (WHATSAPP_TOKEN and phone_id and to and text is not None):
        return False

    url = _wa_url(phone_id)
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}}

    for _ in range(max_retries):
        try:
            r = requests.post(url, headers=headers, json=data, timeout=20)
            if 200 <= r.status_code < 300:
                return True
        except Exception:
            time.sleep(2)

    return False


def _as_list(x, default):
    return x if isinstance(x, list) else default


def _as_dict(x, default):
    return x if isinstance(x, dict) else default


def _extract_wa(payload: dict):
    entry = _as_list(payload.get("entry"), [])
    if not entry:
        return None, None, None, None

    changes = _as_list(_as_dict(entry[0], {}).get("changes"), [])
    value = _as_dict(changes[0], {}).get("value", {}) if changes else {}

    phone_id = _as_dict(value.get("metadata"), {}).get("phone_number_id") or DEFAULT_PHONE_ID
    messages = _as_list(value.get("messages"), [])

    if not messages:
        return phone_id, None, None, None

    msg = _as_dict(messages[0], {})
    msg_id = msg.get("id")
    from_ = msg.get("from")

    text = None
    if "text" in msg and "body" in msg["text"]:
        text = msg["text"]["body"]

    return phone_id, from_, text, msg_id


def _tem_base(trechos: list[dict]) -> bool:
    return any((t.get("trecho") or "").strip() for t in trechos)


@app.post("/webhook")
def webhook():
    try:
        payload = request.get_json(silent=True, force=True) or {}

        phone_id, from_, text, msg_id = _extract_wa(payload)
        if not (phone_id and from_ and text):
            return jsonify({"ignored": True}), 200

        # ✅ LOG CIENTÍFICO
        salvar_log(from_, text, msg_id)

        if dedup:
            if dedup.seen(msg_id):
                return jsonify({"dedup": True}), 200
        else:
            if '_seen_local' in globals() and _seen_local(msg_id):
                return jsonify({"dedup": True}), 200

        contexto = memoria.get_context(from_)
        trechos = buscar_topk(text, k=5) or []

        if not _tem_base(trechos):
            msg = "Não encontrei base para responder sua pergunta."
            enviar_whatsapp(phone_id, from_, msg)
            return jsonify({"ok": True}), 200

        resposta = gerar_resposta(text, trechos, contexto) or "Não consegui gerar resposta."

        enviar_whatsapp(phone_id, from_, resposta)

        memoria.add_user_msg(from_, text)
        memoria.add_assistant_msg(from_, resposta)

        return jsonify({"ok": True}), 200

    except Exception as e:
        log.error("webhook error: %s", e)
        return jsonify({"ok": False}), 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=DEBUG)
