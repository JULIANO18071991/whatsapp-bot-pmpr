import os
import logging
from flask import Flask, request, jsonify, Response

from utils.config import Settings
from utils.whatsapp import WhatsAppClient
from utils.autorag import AutoRAGClient
from utils.openai_client import LLMClient
from utils.memory import MemoryStore  # memória vetorial

app = Flask(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = app.logger

settings = Settings.from_env()
wa = WhatsAppClient(settings)
rag = AutoRAGClient(settings)

# Mantemos o LLMClient apenas para embeddings da memória (não usamos llm.chat)
llm = LLMClient(settings)
memory = MemoryStore(settings.MEMORY_DB_PATH, embed_fn=llm.embed)


@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "service": "pm-whatsapp-bot",
        "env": settings.ENV
    })


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
        if msg.get("type") != "text":
            return None, None, None
        from_id = msg.get("from")
        message_id = msg.get("id")
        text = (msg.get("text") or {}).get("body", "").strip()
        if not text:
            return None, None, None
        return from_id, message_id, text
    except Exception:
        return None, None, None


@app.post("/webhook")
def webhook_receive():
    body = request.get_json(silent=True) or {}
    from_id, message_id, text = _extract_text_entry(body)
    if not all([from_id, message_id, text]):
        return jsonify({"ignored": True}), 200

    # Ignora status callbacks do próprio envio
    if wa.is_own_message(body):
        return jsonify({"ignored": "own_message"}), 200

    # (1) marca como lida (best-effort)
    try:
        wa.mark_read(message_id)
    except Exception as e:
        log.warning(f"mark_read falhou: {e}")

    # (2) memória: salva a mensagem do usuário
    try:
        memory.save(user_id=from_id, role="user", text=text)
    except Exception as e:
        log.warning(f"memory.save(user) falhou: {e}")

    # (3) memória: busca histórico relevante (opcional)
    try:
        history = memory.search(user_id=from_id, query=text, top_k=4)
    except Exception as e:
        log.warning(f"memory.search falhou: {e}")
        history = []

    # (4) AI Search direto (com fallback e log de debug)
    try:
        ai = rag.ai_search(text)  # apenas {"query": "..."}
        answer = (ai.get("response") or "").strip()

        # DEBUG: loga tamanho da resposta e top scores (remova se quiser)
        try:
            raw = ai.get("raw", {}) or {}
            res = (raw.get("result") or {})
            scores = []
            for it in (res.get("data") or [])[:5]:
                s = it.get("score")
                if s is not None:
                    scores.append(str(s))
            log.info(f"[AI-SEARCH] resp_len={len(answer)} top_scores={','.join(scores) or 'none'}")
        except Exception as dbg_e:
            log.warning(f"[AI-SEARCH] debug parse falhou: {dbg_e}")

        # Fallback: se o endpoint não retornar 'response', usa um snippet da 1ª fonte
        if not answer:
            sources = ai.get("sources") or []
            if sources:
                answer = (sources[0].get("snippet") or "").strip()
                if not answer:
                    answer = "Não encontrei resultados."
            else:
                answer = "Não encontrei resultados."
    except Exception:
        log.exception("AutoRAG ai-search falhou")
        answer = "Não consegui gerar uma resposta agora. Tente novamente em instantes."

    # (5) enviar pelo WhatsApp
    try:
        wa.send_text(to=from_id, text=answer)
    except Exception:
        log.exception("Falha ao enviar resposta no WhatsApp")
        return jsonify({"error": "send_failed"}), 500

    # (6) memória: salva a resposta do assistente
    try:
        memory.save(user_id=from_id, role="assistant", text=answer)
    except Exception as e:
        log.warning(f"memory.save(assistant) falhou: {e}")

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
