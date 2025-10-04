# bot.py
# -*- coding: utf-8 -*-
"""
Webhook do WhatsApp (Meta) + TopK + OpenAI.
Compatível com Flask/Gunicorn (Railway/Render/etc.).

Principais recursos:
- /webhook (GET): verificação do token do WhatsApp
- /webhook (POST): recepção de mensagens e resposta automática
- /health (GET): healthcheck simples
- Deduplicação de mensagens por message_id (evita reprocessar)
- Fragmentação de respostas longas
- Requisições ao Graph API com retry/backoff para 429/5xx
- Integração com TopK e OpenAI via módulos topk_client.py e llm_client.py
"""

import os
import json
import time
import logging
from collections import deque
from typing import Any, Dict, List, Optional

import requests
from flask import Flask, request, jsonify

from topk_client import search_topk
from llm_client import gerar_resposta

# ======================== CONFIG & LOG ========================

DEBUG = os.getenv("DEBUG", "0") == "1"

WABA_TOKEN = os.getenv("WABA_TOKEN")  # token da Meta/WhatsApp Business
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "verify_token_dev")  # usado na verificação GET
WABA_API_VERSION = os.getenv("WABA_API_VERSION", "v20.0")  # ex.: v20.0

# logging estruturado
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)
logger = logging.getLogger("bot")

# ======================== MEMÓRIA (ADAPTER) ========================

try:
    from memory import Memory  # sua classe de memória
    _mem_instance = Memory(max_msgs=6)  # se a nova memória existir, ótimo
except Exception as e:
    logger.warning("Falha ao importar memory.Memory; usando memória dummy. Erro: %s", e)
    _mem_instance = None

class MemoryAdapter:
    """Adapta diferentes implementações de memória para a LLM (lista de mensagens)."""

    def __init__(self, mem_obj):
        self.mem = mem_obj

    def add_user(self, user: str, text: str):
        if not self.mem:
            return
        # nova API
        if hasattr(self.mem, "add_user_msg"):
            self.mem.add_user_msg(user, text)
            return
        # fallback
        if hasattr(self.mem, "add"):
            self.mem.add(user, text)

    def add_assistant(self, user: str, text: str):
        if not self.mem:
            return
        # nova API
        if hasattr(self.mem, "add_assistant_msg"):
            self.mem.add_assistant_msg(user, text)
            return
        # sem suporte: ignora (ou armazena com sufixo)
        if hasattr(self.mem, "add"):
            self.mem.add(f"{user}__assistant", text)

    def get_messages(self, user: str) -> List[Dict[str, str]]:
        """Retorna lista de mensagens {role, content} (user/assistant)."""
        msgs: List[Dict[str, str]] = []
        if not self.mem:
            return msgs

        raw = None
        if hasattr(self.mem, "get_context"):
            raw = self.mem.get_context(user)
        elif hasattr(self.mem, "get"):
            raw = self.mem.get(user)

        if not raw:
            return msgs

        # Se já estiver no formato {role, content}, retorna direto
        if isinstance(raw, list) and raw and isinstance(raw[0], dict) and "role" in raw[0]:
            return raw  # type: ignore

        # Caso sejam strings antigas, embala como mensagens do usuário
        if isinstance(raw, list):
            for s in raw:
                try:
                    msgs.append({"role": "user", "content": str(s)})
                except Exception:
                    continue
        return msgs

memoria = MemoryAdapter(_mem_instance)

# ======================== DEDUP (message_id) ========================

# guarda últimos N message_ids processados (idempotência simples)
_MAX_IDS = 200
_recent_ids_q = deque(maxlen=_MAX_IDS)
_recent_ids_set = set()

def _seen(msg_id: Optional[str]) -> bool:
    if not msg_id:
        return False
    if msg_id in _recent_ids_set:
        return True
    _recent_ids_set.add(msg_id)
    _recent_ids_q.append(msg_id)
    # remove excedente do set quando o deque descarta
    if len(_recent_ids_set) > len(_recent_ids_q):
        # rebuild set em casos raros
        _recent_ids_set.clear()
        _recent_ids_set.update(_recent_ids_q)
    return False

# ======================== WHATSAPP HELPERS ========================

def _extract_wa(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Extrai (phone_id, from, text, msg_id) do payload do WhatsApp.
    Retorna None se não encontrar mensagem de texto.
    """
    try:
        entry = payload.get("entry", [])[0]
        change = entry.get("changes", [])[0]
        value = change.get("value", {})
        phone_id = value.get("metadata", {}).get("phone_number_id")

        messages = value.get("messages", [])
        if not messages:
            return None

        msg = messages[0]
        msg_id = msg.get("id")
        from_ = msg.get("from")
        text = None
        if "text" in msg and "body" in msg["text"]:
            text = msg["text"]["body"]
        elif msg.get("type") == "interactive":
            # botão/lista
            inter = msg.get("interactive", {})
            if "button_reply" in inter:
                text = inter["button_reply"].get("title")
            elif "list_reply" in inter:
                text = inter["list_reply"].get("title")

        if not (phone_id and from_ and text):
            return None

        return {"phone_id": phone_id, "from": from_, "text": text.strip(), "msg_id": msg_id}
    except Exception as e:
        logger.exception("Falha ao extrair dados do payload: %s", e)
        return None


def _wa_url(phone_id: str) -> str:
    return f"https://graph.facebook.com/{WABA_API_VERSION}/{phone_id}/messages"


def enviar_whatsapp(phone_id: str, to: str, text: str, max_retries: int = 3) -> bool:
    """Envia mensagem de texto ao WhatsApp com retry/backoff básico."""
    if not (WABA_TOKEN and phone_id and to and text is not None):
        logger.error("Faltam parâmetros para enviar WhatsApp.")
        return False

    url = _wa_url(phone_id)
    headers = {
        "Authorization": f"Bearer {WABA_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text}
    }

    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(url, headers=headers, json=data, timeout=20)
            if 200 <= resp.status_code < 300:
                return True

            # 429/5xx: retry com backoff
            if resp.status_code in (429, 500, 502, 503, 504):
                wait = min(2 ** attempt, 10)
                logger.warning("WhatsApp status=%s; retry em %ss; body=%s",
                               resp.status_code, wait, resp.text[:500])
                time.sleep(wait)
                continue

            logger.error("Falha ao enviar WhatsApp: status=%s body=%s",
                         resp.status_code, resp.text[:1000])
            return False
        except requests.RequestException as e:
            wait = min(2 ** attempt, 10)
            logger.warning("Erro de rede ao enviar WhatsApp: %s; retry em %ss", e, wait)
            time.sleep(wait)

    logger.error("Excedeu tentativas ao enviar WhatsApp.")
    return False

# ======================== FLASK APP ========================

app = Flask(__name__)

@app.get("/")
def root():
    return jsonify({"ok": True, "service": "whatsapp-bot"}), 200

@app.get("/health")
def health():
    return jsonify({"ok": True}), 200

@app.get("/webhook")
def verify():
    """
    Verificação do token pelo WhatsApp (apenas em setup).
    Meta chama com: hub.mode, hub.verify_token, hub.challenge
    """
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
            # Atenção: não logue conteúdo sensível em produção
            logger.debug("[DEBUG payload preview] %s", json.dumps(payload, ensure_ascii=False)[:1500])

        parsed = _extract_wa(payload)
        if not parsed:
            return jsonify({"ignored": True}), 200

        phone_id = parsed["phone_id"]
        from_ = parsed["from"]
        text = parsed["text"]
        msg_id = parsed.get("msg_id")

        # idempotência simples
        if _seen(msg_id):
            logger.info("Mensagem %s já processada. Ignorando.", msg_id)
            return jsonify({"dedup": True}), 200

        # 1) recupera memória
        contexto_msgs = memoria.get_messages(from_)  # List[{role, content}]

        # 2) busca TopK
        resultados = search_topk(text, k=5) or []

        if DEBUG:
            logger.debug("[DEBUG topk resultados tipo/len] %s %s",
                         type(resultados).__name__,
                         len(resultados) if hasattr(resultados, "__len__") else "-")

        # 3) chama LLM (ORDEM CORRETA: pergunta, trechos, memoria)
        resposta = gerar_resposta(text, resultados, contexto_msgs)

        # 4) guarda histórico
        memoria.add_user(from_, text)
        memoria.add_assistant(from_, resposta)

        # 5) envia resposta (fragmenta para evitar corte)
        if not resposta:
            resposta = "Não consegui gerar uma resposta agora. Pode tentar reformular a pergunta?"

        chunk = 3800  # margem de segurança para 4096
        ok_all = True
        for i in range(0, len(resposta), chunk):
            part = resposta[i:i+chunk]
            ok = enviar_whatsapp(phone_id, from_, part)
            ok_all = ok_all and ok

        return jsonify({"ok": ok_all}), 200

    except Exception as e:
        logger.exception("Erro no webhook: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    # Execução local: FLASK_ENV=development FLASK_APP=bot.py flask run --port 8000
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=DEBUG)
