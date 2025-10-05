# bot.py
# -*- coding: utf-8 -*-
import os, json, time, logging, requests, traceback
from collections import deque
from flask import Flask, request, jsonify
from dotenv import load_dotenv

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
    from memory_redis import RedisMemory, Dedup  # type: ignore
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
            if not msg_id: return False
            if msg_id in _recent_ids_set: return True
            _recent_ids_set.add(msg_id); _recent_ids_q.append(msg_id)
            if len(_recent_ids_set) > len(_recent_ids_q):
                _recent_ids_set.clear(); _recent_ids_set.update(_recent_ids_q)
            return False
    except Exception as e2:
        log.error("Nenhum backend de memória pôde ser carregado (%s). Usando memória nula.", e2)
        class _NullMem:
            def add_user_msg(self, u, m): pass
            def add_assistant_msg(self, u, m): pass
            def get_context(self, u): return []
        memoria = _NullMem()
        _recent_ids_q = deque(maxlen=200)
        _recent_ids_set = set()
        def _seen_local(msg_id: str | None) -> bool:
            if not msg_id: return False
            if msg_id in _recent_ids_set: return True
            _recent_ids_set.add(msg_id); _recent_ids_q.append(msg_id)
            if len(_recent_ids_set) > len(_recent_ids_q):
                _recent_ids_set.clear(); _recent_ids_set.update(_recent_ids_q)
            return False

app = Flask(__name__)

def _wa_url(phone_id: str) -> str:
    return f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{phone_id}/messages"

def enviar_whatsapp(phone_id: str, to: str, text: str, max_retries: int = 3) -> bool:
    if not (WHATSAPP_TOKEN and phone_id and to and text is not None):
        log.error("Parâmetros faltando para enviar WhatsApp. token=%s phone_id=%s to=%s",
                  bool(WHATSAPP_TOKEN), phone_id, to)
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
                wait = min(2 ** attempt, 10)
                log.warning("WA %s; retry em %ss; body=%s", r.status_code, wait, r.text[:400])
                time.sleep(wait)
                continue
            log.error("Falha WA %s: %s", r.status_code, r.text[:800])
            return False
        except requests.RequestException as e:
            wait = min(2 ** attempt, 10)
            log.warning("Erro rede WA: %s; retry em %ss", e, wait)
            time.sleep(wait)
    return False

def _as_list(x, default): return x if isinstance(x, list) else default
def _as_dict(x, default): return x if isinstance(x, dict) else default

def _extract_wa(payload: dict):
    entry = _as_list(payload.get("entry"), [])
    if not entry: return None, None, None, None
    changes = _as_list(_as_dict(entry[0], {}).get("changes"), [])
    value = _as_dict(changes[0], {}).get("value", {}) if changes else {}
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

def _tem_base(trechos: list[dict]) -> bool:
    if not trechos:
        return False
    return any((t.get("trecho") or "").strip() for t in trechos)

# -------- rotas --------
@app.get("/")
def root():
    return jsonify({"ok": True, "service": "whatsapp-bot"}), 200

@app.get("/health")
def health():
    return jsonify({"ok": True}), 200

@app.get("/status")
def status():
    return jsonify({
        "ok": True,
        "debug": DEBUG,
        "whatsapp": {
            "token_set": bool(WHATSAPP_TOKEN),
            "default_phone_id_set": bool(DEFAULT_PHONE_ID),
            "api_version": WHATSAPP_API_VERSION
        },
        "topk": topk_status()
    }), 200

# Endpoint de diagnóstico de busca
@app.get("/diag/topk")
def diag_topk():
    q = (request.args.get("q") or "").strip()
    res = buscar_topk(q, k=5) if q else []
    return jsonify({"q": q, "n": len(res), "items": res[:3]}), 200

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
            log.debug("payload: %s", json.dumps(payload, ensure_ascii=False)[:1500])

        phone_id, from_, text, msg_id = _extract_wa(payload)
        if not (phone_id and from_ and text):
            return jsonify({"ignored": True}), 200

        # idempotência
        if dedup:
            if dedup.seen(msg_id):
                log.info("Mensagem %s já processada. Ignorando.", msg_id)
                return jsonify({"dedup": True}), 200
        else:
            if '_seen_local' in globals() and _seen_local(msg_id):
                log.info("Mensagem %s já processada (local).", msg_id)
                return jsonify({"dedup": True}), 200

        contexto = memoria.get_context(from_) if hasattr(memoria, "get_context") else []
        trechos = buscar_topk(text, k=5) or []
        log.info("TopK retornou %d itens.", len(trechos))

        if not _tem_base(trechos):
            log.warning("[BOT] Sem base do TopK. Status: %s", topk_status())
            msg = (
                "Não encontrei base nos documentos do TopK para responder sua pergunta.\n"
                "Você pode reformular a questão (citando Portaria/tema) ou enviar o documento correspondente."
            )
            enviar_whatsapp(phone_id, from_, msg)
            return jsonify({"ok": True, "rag_only": True, "base": False}), 200

        resposta = gerar_resposta(text, trechos, contexto) or "Não consegui gerar uma resposta agora."

        chunk = 3800
        ok_all = True
        for i in range(0, len(resposta), chunk):
            part = resposta[i:i+chunk]
            if part.strip():
                ok_all &= enviar_whatsapp(phone_id, from_, part)

        try:
            if hasattr(memoria, "add_user_msg"):
                memoria.add_user_msg(from_, text)
            elif hasattr(memoria, "add"):
                memoria.add(from_, text)
            if hasattr(memoria, "add_assistant_msg"):
                memoria.add_assistant_msg(from_, resposta)
        except Exception as e:
            log.warning("Falha ao salvar memória: %s", e)

        return jsonify({"ok": ok_all}), 200

    except Exception as e:
        log.error("webhook error: %s", e)
        if DEBUG:
            log.debug(traceback.format_exc())
        return jsonify({"ok": False, "error": str(e)}), 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=DEBUG)
