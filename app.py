import os
import io
import json
import logging
import threading
import re
import sqlite3
from typing import List, Dict

from flask import Flask, request, Response
import requests
from openai import OpenAI

# === R2 (Cloudflare) ===
import boto3
from botocore.config import Config

# === PDF / Vetores ===
from pypdf import PdfReader
import numpy as np

app = Flask(__name__)

# ---------------------------
# Logs
# ---------------------------
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = app.logger

# ---------------------------
# Env vars
# ---------------------------
VERIFY_TOKEN     = os.getenv("VERIFY_TOKEN")
WHATSAPP_TOKEN   = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID  = os.getenv("PHONE_NUMBER_ID")

OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL      = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", "350"))  # limite curto de saída

# RAG
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
# use /data/rag.db se você criou um Volume no Railway montado em /data
DB_PATH     = os.getenv("RAG_DB", "/data/rag.db")
# limiar de similaridade cosseno para aceitar trechos (0–1)
RAG_MIN_SIM = float(os.getenv("RAG_MIN_SIM", "0.28"))
# 1 = NUNCA cair no fallback geral (impede alucinação); 0 = pode cair
RAG_STRICT  = os.getenv("RAG_STRICT", "1") == "1"

# Proteção do endpoint admin
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")

client = OpenAI(api_key=OPENAI_API_KEY)

# ---------------------------
# R2 (Cloudflare S3-compatible)
# ---------------------------
R2_ENDPOINT           = os.getenv("R2_ENDPOINT")  # ex.: https://<ACCOUNT_ID>.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID      = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY  = os.getenv("R2_SECRET_ACCESS_KEY")
R2_BUCKET             = os.getenv("R2_BUCKET")
R2_REGION             = os.getenv("R2_REGION", "auto")

_s3 = None
if R2_ENDPOINT and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY and R2_BUCKET:
    _s3 = boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name=R2_REGION,
        config=Config(signature_version="s3v4"),
    )

def r2_list(prefix: str = "") -> List[str]:
    """Lista objetos no bucket com paginação."""
    if not _s3:
        raise RuntimeError("R2 não configurado (verifique env vars).")
    keys = []
    token = None
    while True:
        kwargs = dict(Bucket=R2_BUCKET, Prefix=prefix, MaxKeys=1000)
        if token:
            kwargs["ContinuationToken"] = token
        resp = _s3.list_objects_v2(**kwargs)
        for it in resp.get("Contents", []):
            keys.append(it["Key"])
        if resp.get("IsTruncated"):
            token = resp.get("NextContinuationToken")
        else:
            break
    return keys

def r2_get_bytes(key: str) -> bytes:
    if not _s3:
        raise RuntimeError("R2 não configurado (verifique env vars).")
    obj = _s3.get_object(Bucket=R2_BUCKET, Key=key)
    return obj["Body"].read()

# ---------------------------
# Graph API (WhatsApp)
# ---------------------------
GRAPH_VERSION = "v21.0"
GRAPH_BASE    = f"https://graph.facebook.com/{GRAPH_VERSION}"

def _headers():
    return {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}

def send_mark_read(message_id: str):
    """Marca a mensagem como lida (best-effort)."""
    url = f"{GRAPH_BASE}/{PHONE_NUMBER_ID}/messages"
    payload = {"messaging_product": "whatsapp", "status": "read", "message_id": message_id}
    try:
        r = requests.post(url, headers=_headers(), json=payload, timeout=10)
        if r.status_code >= 400:
            logger.warning("mark_read falhou: %s - %s", r.status_code, r.text)
        return r.json() if r.ok else None
    except Exception as e:
        logger.warning("mark_read exception: %s", e)
        return None

def send_text(to: str, body: str):
    """Envia texto simples (um único bloco)."""
    url = f"{GRAPH_BASE}/{PHONE_NUMBER_ID}/messages"
    payload = {"messaging_product": "whatsapp", "to": to, "text": {"body": body}}
    r = requests.post(url, headers=_headers(), json=payload, timeout=20)
    r.raise_for_status()
    return r.json()

def send_text_chunks(to: str, body: str, chunk_size: int = 1200):
    """Quebra respostas longas em blocos seguros para o WhatsApp."""
    body = body or ""
    for i in range(0, len(body), chunk_size):
        part = body[i:i+chunk_size]
        send_text(to, part)

# ---------------------------
# Estilo conciso/operacional
# ---------------------------
SYSTEM_PROMPT = (
    "Você é o BotPMPR, assistente para policiais militares no WhatsApp. "
    "Responda SEM rodeios, em português do Brasil, no tom operacional.\n\n"
    "REGRAS DE ESTILO:\n"
    "1) No máximo 6 linhas (ou 5 passos numerados).\n"
    "2) Frases curtas, voz ativa, sem desculpas.\n"
    "3) Use *negrito* só para termos-chave.\n"
    "4) Quando útil, liste no máximo 3 pontos (•). Nada de parágrafos longos.\n"
    "5) Faça 1 pergunta de esclarecimento apenas se faltar algo ESSENCIAL.\n"
    "6) Se citar norma/procedimento, cite sigla/ato e artigo quando disponível no contexto (ex.: [1]).\n"
    "7) Se NÃO houver base nos trechos fornecidos, diga claramente que não encontrou nos documentos e peça o termo/nº do ato. NÃO invente.\n"
)

def compact_whatsapp(text: str, hard_limit: int = 900) -> str:
    """Compacta para caber bem no WhatsApp: remove excesso e limita linhas/tamanho."""
    if not text:
        return text
    t = text.strip()
    t = re.sub(r'\n{3,}', '\n\n', t)
    lines = t.splitlines()
    if len(lines) > 8:
        lines = lines[:8] + ["…"]
    t = "\n".join(lines)
    if len(t) > hard_limit:
        t = t[:hard_limit-1].rstrip() + "…"
    return t

# ---------------------------
# OpenAI (sem RAG) - fallback
# ---------------------------
def ask_ai(user_text: str) -> str:
    if not OPENAI_API_KEY:
        return "A IA não está configurada (OPENAI_API_KEY ausente)."
    try:
        completion = client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=0.1,
            max_tokens=OPENAI_MAX_TOKENS,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_text},
            ],
        )
        return (completion.choices[0].message.content or "").strip()
    except Exception as e:
        logger.error("Erro OpenAI: %s", getattr(e, "message", str(e)))
        return "Não consegui consultar a IA no momento. Tente novamente em instantes."

# ---------------------------
# RAG: SQLite + Embeddings + Busca
# ---------------------------
def db_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chunks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            ord INTEGER,
            content TEXT,
            embedding TEXT
        )
    """)
    return conn

def clear_index():
    with db_conn() as conn:
        conn.execute("DELETE FROM chunks")
        conn.commit()

def chunk_text(text: str, chunk_chars: int = 1200, overlap: int = 200) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    chunks = []
    i = 0
    n = len(text)
    while i < n:
        end = i + chunk_chars
        chunk = text[i:end]
        chunks.append(chunk)
        i = end - overlap
        if i < 0:
            i = 0
    return [c.strip() for c in chunks if c.strip()]

def pdf_bytes_to_text(b: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(b))
        parts = []
        for p in reader.pages:
            try:
                parts.append(p.extract_text() or "")
            except Exception:
                parts.append("")
        return "\n".join(parts)
    except Exception as e:
        logger.warning("Falha ao ler PDF: %s", e)
        return ""

def embed_texts(texts: List[str]) -> List[List[float]]:
    if not texts:
        return []
    embs = client.embeddings.create(model=EMBED_MODEL, input=texts)
    return [e.embedding for e in embs.data]

def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    denom = (np.linalg.norm(a) * np.linalg.norm(b)) + 1e-8
    return float(np.dot(a, b) / denom)

def index_object(key: str, data: bytes) -> int:
    """Indexa 1 objeto do R2 (PDF/TXT). Retorna nº de chunks adicionados."""
    key_lower = key.lower()
    if key_lower.endswith(".pdf"):
        text = pdf_bytes_to_text(data)
    elif key_lower.endswith(".txt"):
        try:
            text = data.decode("utf-8", errors="ignore")
        except Exception:
            text = ""
    else:
        return 0

    parts = chunk_text(text)
    if not parts:
        return 0

    vectors = embed_texts(parts)
    with db_conn() as conn:
        for i, (c, v) in enumerate(zip(parts, vectors)):
            conn.execute(
                "INSERT INTO chunks(source, ord, content, embedding) VALUES (?,?,?,?)",
                (key, i, c, json.dumps(v))
            )
        conn.commit()
    return len(parts)

def list_index_count() -> int:
    with db_conn() as conn:
        cur = conn.execute("SELECT COUNT(*) FROM chunks")
        return int(cur.fetchone()[0])

def retrieve(query: str, k: int = 5) -> List[Dict]:
    """Busca semântica simples em SQLite (carrega embeddings e rankeia por cosseno)."""
    with db_conn() as conn:
        rows = conn.execute("SELECT id, source, ord, content, embedding FROM chunks").fetchall()
    if not rows:
        return []

    q_vec = np.array(embed_texts([query])[0], dtype=np.float32)
    scored = []
    for rid, src, ord_, content, emb_json in rows:
        try:
            v = np.array(json.loads(emb_json), dtype=np.float32)
            sim = cosine_sim(q_vec, v)
            scored.append({"id": rid, "source": src, "ord": ord_, "content": content, "score": sim})
        except Exception:
            continue
    scored.sort(key=lambda x: x["score"], reverse=True)

    # Filtra por limiar de confiança e devolve no máximo k trechos
    filtered = [s for s in scored[: max(k, 10)] if s["score"] >= RAG_MIN_SIM]
    return filtered[:k]

def build_context_snippets(items: List[Dict], max_chars: int = 600) -> str:
    lines = []
    for i, it in enumerate(items, start=1):
        content = it["content"]
        if len(content) > max_chars:
            content = content[:max_chars].rstrip() + "…"
        lines.append(f"[{i}] {it['source']} :: {content}")
    return "\n\n".join(lines)

def build_sources_footer(items: List[Dict]) -> str:
    if not items:
        return ""
    parts = []
    for i, it in enumerate(items, start=1):
        parts.append(f"[{i}] {it['source']}")
    return "\n\nFontes: " + "; ".join(parts)

def ask_ai_with_context(user_text: str) -> str:
    """Consulta a OpenAI usando contexto recuperado do índice (RAG)."""
    if not OPENAI_API_KEY:
        return "A IA não está configurada (OPENAI_API_KEY ausente)."

    snippets = retrieve(user_text, k=5)
    # Se não há evidência suficiente nos documentos, não responda “de cabeça”.
    if not snippets:
        if RAG_STRICT:
            return ("Não localizei essa informação nos documentos disponíveis. "
                    "Envie o termo/nº do ato (ex.: portaria, artigo) ou detalhe melhor para eu procurar.")
        # modo não-estrito: permitir fallback geral
        return ask_ai(user_text)

    context = build_context_snippets(snippets)
    system = SYSTEM_PROMPT + (
        "\n\nUse os trechos das fontes a seguir como contexto quando relevante. "
        "Responda SOMENTE com base neles. Se algo não estiver nas fontes, diga que não encontrou. "
        "Sempre que citar, referencie o número do trecho entre colchetes (ex.: [1], [2]).\n\n"
        f"{context}"
    )
    try:
        completion = client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=0.1,
            max_tokens=OPENAI_MAX_TOKENS,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user_text},
            ],
        )
        answer = (completion.choices[0].message.content or "").strip()
        # Anexa rodapé de fontes para transparência
        answer = answer + build_sources_footer(snippets)
        return answer
    except Exception as e:
        logger.error("Erro OpenAI (RAG): %s", getattr(e, "message", str(e)))
        return "Não consegui consultar a IA agora. Tente novamente em instantes."

# ---------------------------
# Health / Admin / Testes
# ---------------------------
@app.get("/health")
def health():
    return {
        "ok": True,
        "phone_number_id": PHONE_NUMBER_ID,
        "graph_version": GRAPH_VERSION,
        "openai_model": OPENAI_MODEL,
        "openai_on": bool(OPENAI_API_KEY),
        "max_tokens": OPENAI_MAX_TOKENS,
        "r2_on": bool(_s3 and R2_BUCKET),
        "r2_bucket": R2_BUCKET,
        "rag_db": DB_PATH,
        "chunks": list_index_count(),
    }

@app.get("/r2test")
def r2test():
    """Lista até 20 objetos do bucket (para validar as credenciais do R2)."""
    try:
        keys = r2_list("")
        return {"bucket": R2_BUCKET, "count": len(keys), "objects": keys[:20]}
    except Exception as e:
        logger.exception("R2 test error: %s", e)
        return {"error": str(e)}, 500

@app.post("/admin/reindex")
def admin_reindex():
    token = request.headers.get("X-Admin-Token", "")
    if token != (ADMIN_TOKEN or ""):
        return Response("forbidden", status=403)
    prefix = request.args.get("prefix", "")  # opcional: limitar a uma subpasta do bucket
    result = reindex_from_r2(prefix=prefix)
    result["chunks_after"] = list_index_count()
    return result, 200

# ---------------------------
# Webhook
# ---------------------------
@app.get("/webhook")
def verify():
    """Verificação do webhook (Meta chama GET com hub.challenge)."""
    mode      = request.args.get("hub.mode")
    token     = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return Response(challenge or "", status=200, mimetype="text/plain")
    return Response(status=403)

def handle_incoming_message(msg: dict):
    """Processa mensagens fora do ciclo do webhook (<=10s)."""
    try:
        msg_id   = msg.get("id")
        from_    = msg.get("from")   # ex.: "55419..."
        msg_type = msg.get("type")

        if msg_id:
            send_mark_read(msg_id)

        if msg_type == "text":
            user_text = (msg.get("text") or {}).get("body", "").strip()
            if not user_text:
                send_text(from_, "Mensagem vazia. Pode repetir?")
                return

            # === Usa RAG por padrão ===
            ai_answer = ask_ai_with_context(user_text)
            ai_answer = compact_whatsapp(ai_answer)
            send_text_chunks(from_, ai_answer, chunk_size=1200)

        elif msg_type == "interactive":
            btn = (msg.get("interactive") or {}).get("button_reply") or {}
            title = btn.get("title") or "opção"
            send_text(from_, f"Você selecionou: {title}. Descreva sua dúvida em texto, por favor.")

        else:
            send_text(from_, "Recebi seu conteúdo. Pode escrever sua dúvida em texto?")
    except Exception as e:
        logger.exception("Erro em handle_incoming_message: %s", e)

@app.post("/webhook")
def webhook():
    """Recebe eventos do WhatsApp (mensagens e status)."""
    try:
        data = request.get_json(force=True, silent=True) or {}
        logger.info("Incoming payload: %s", json.dumps(data, ensure_ascii=False))

        changes = (data.get("entry") or [{}])[0].get("changes") or []
        if not changes:
            return Response(status=200)

        value    = changes[0].get("value") or {}
        messages = value.get("messages") or []
        statuses = value.get("statuses") or []

        if messages:
            for msg in messages:
                threading.Thread(target=handle_incoming_message, args=(msg,), daemon=True).start()

        if statuses:
            st = statuses[0]
            logger.info("Status: %s  msgId: %s  dest: %s",
                        st.get("status"), st.get("id"), st.get("recipient_id"))

        return Response(status=200)
    except Exception as e:
        logger.exception("Erro no webhook: %s", e)
        return Response(status=200)
