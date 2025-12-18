# bot.py – versão corrigida para multi-coleções e LLM aprimorado
# -*- coding: utf-8 -*-

import os, json, time, logging, requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv
import redis

load_dotenv()

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

# =========================================================
# MEMÓRIA / DEDUP
# =========================================================
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

# =========================================================
# REDIS — LOG DOS ATENDIMENTOS
# =========================================================
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
    except Exception as e:
        log.error("Erro salvando log Redis: %s", e)


# =========================================================
# FLASK
# =========================================================
app = Flask(__name__)


def _wa_url(phone_id):
    return f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{phone_id}/messages"


def enviar_whatsapp(phone_id, to, text):
    """Divide respostas longas automaticamente (limite WhatsApp ≈ 4096 chars)."""
    if not text:
        text = "Não consegui gerar resposta."

    partes = []
    while len(text) > 4096:
        partes.append(text[:4096])
        text = text[4096:]
    partes.append(text)

    ok = True
    for p in partes:
        url = _wa_url(phone_id)
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }
        data = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": p}
        }
        try:
            r = requests.post(url, headers=headers, json=data, timeout=15)
            ok = ok and (r.status_code == 200)
        except Exception:
            ok = False
    return ok


def _as_list(x):
    return x if isinstance(x, list) else []


def _as_dict(x):
    return x if isinstance(x, dict) else {}


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

    # texto
    text = None
    if "text" in msg and "body" in msg["text"]:
        text = msg["text"]["body"]

    return phone_id, from_, text, msg_id


def _tem_base(trechos):
    return any((t.get("trecho") or "").strip() for t in trechos)


# =========================================================
# WEBHOOK PRINCIPAL
# =========================================================
@app.post("/webhook")
def webhook():
    try:
        payload = request.get_json(silent=True, force=True) or {}

        phone_id, from_, text, msg_id = _extract_wa(payload)

        # mensagem que não é texto (áudio, imagem, botão, etc.)
        if not text:
            if phone_id and from_:
                enviar_whatsapp(phone_id, from_, "Envie sua pergunta em texto para que eu possa analisar.")
            return jsonify({"ignored": True}), 200

        salvar_log(from_, text, msg_id)

        # DEDUP
        if dedup:
            if msg_id and dedup.seen(msg_id):
                return jsonify({"dedup": True}), 200
        else:
            if '_seen_local' in globals() and _seen_local(msg_id):
                return jsonify({"dedup": True}), 200

        # CONTEXTO
        contexto = memoria.get_context(from_) if hasattr(memoria, "get_context") else []

        # MULTI COLEÇÕES
        resultados_por_colecao = buscar_topk_multi(text, k=5) or {}

        # LISTA PLANA
        trechos_flat = [
            item
            for lista in resultados_por_colecao.values()
            for item in lista
        ]

        if not _tem_base(trechos_flat):
            enviar_whatsapp(phone_id, from_, "Não encontrei base normativa para responder sua pergunta.")
            return jsonify({"ok": True}), 200

        # GERA RESPOSTA
        resposta = gerar_resposta(text, trechos_flat, contexto) or "Não consegui gerar resposta."

        enviar_whatsapp(phone_id, from_, resposta)

        # MEMÓRIA
        if hasattr(memoria, "add_user_msg"):
            memoria.add_user_msg(from_, text)
        if hasattr(memoria, "add_assistant_msg"):
            memoria.add_assistant_msg(from_, resposta)

        return jsonify({"ok": True}), 200

    except Exception as e:
        log.error(f"Webhook error: {e}")
        return jsonify({"ok": False}), 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=DEBUG)
