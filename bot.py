# bot.py
# -*- coding: utf-8 -*-

import os
import io
import re
import json
import base64
import unicodedata
import logging
import tempfile
import requests
import pathlib
import importlib.util
import threading
import time
from contextlib import redirect_stdout
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

from topk_client import buscar_topk_multi
from llm_client import gerar_resposta
from dedup import Dedup
from synonyms import expand_query

# ========= GOOGLE DRIVE =========
# Requer:
#   google-api-python-client google-auth google-auth-httplib2
try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
except Exception:
    service_account = None
    build = None
    MediaIoBaseDownload = None

DEBUG = os.getenv("DEBUG", "0") == "1"

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

# Silenciar logs MUITO verbosos de parsing de PDF
logging.getLogger("pdfminer").setLevel(logging.WARNING)
logging.getLogger("pdfplumber").setLevel(logging.WARNING)
logging.getLogger("googleapiclient").setLevel(logging.WARNING)
logging.getLogger("google.auth").setLevel(logging.WARNING)

log = logging.getLogger("bot")

app = Flask(__name__)

# Deduplicador global (TTL em segundos)
dedup = Dedup(ttl=600)

# =========================
# LIMITES WhatsApp
# =========================
WA_MAX = 4096          # limite duro da Cloud API
WA_SAFE = 3900         # margem de segurança pra evitar erro por variações

# =========================
# CONTROLE DE JOBS (evita duplicar geração)
# =========================
_jobs_lock = threading.Lock()
_jobs_running = {}  # key=wa_id, value=timestamp

def _job_start(key: str, ttl: int = 300) -> bool:
    """True se conseguiu iniciar; False se já tem job recente rodando."""
    now = time.time()
    with _jobs_lock:
        ts = _jobs_running.get(key)
        if ts and (now - ts) < ttl:
            return False
        _jobs_running[key] = now
        return True

def _job_end(key: str):
    with _jobs_lock:
        _jobs_running.pop(key, None)

# =========================
# HELPERS: WhatsApp envio
# =========================
def _wa_post(phone_id: str, payload: dict):
    """POST no endpoint /messages com log do retorno."""
    token = os.getenv("WHATSAPP_TOKEN")
    api_version = os.getenv("WHATSAPP_API_VERSION", "v22.0")
    url = f"https://graph.facebook.com/{api_version}/{phone_id}/messages"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    r = requests.post(url, headers=headers, json=payload, timeout=20)

    try:
        log.info(f"[WA] status={r.status_code} resp={r.json()}")
    except Exception:
        log.info(f"[WA] status={r.status_code} resp_text={r.text}")

    return r


def enviar_whatsapp_texto(phone_id: str, to: str, text: str):
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text}
    }
    return _wa_post(phone_id, payload)


def enviar_whatsapp_template(phone_id: str, to: str):
    template_name = os.getenv("WHATSAPP_TEMPLATE_NAME", "hello_world")
    template_lang = os.getenv("WHATSAPP_TEMPLATE_LANG", "en_US")

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": template_lang}
        }
    }
    return _wa_post(phone_id, payload)


def enviar_whatsapp(phone_id: str, to: str, text: str):
    """
    Tenta enviar texto.
    Se der erro comum de janela/reativação, tenta template como fallback.
    """
    r = enviar_whatsapp_texto(phone_id, to, text)

    if r.ok:
        return

    try:
        data = r.json()
        msg = (data.get("error") or {}).get("message", "")
    except Exception:
        msg = r.text or ""

    lowered = msg.lower()
    needs_template = any(
        k in lowered for k in [
            "template", "outside", "24", "re-engagement", "reengagement",
            "not allowed", "message type"
        ]
    )

    if needs_template:
        log.warning("[WA] Texto falhou; tentando TEMPLATE (provável janela 24h).")
        enviar_whatsapp_template(phone_id, to)


# =========================
# HELPERS: comando
# =========================
def _strip_accents(s: str) -> str:
    s = s or ""
    return "".join(
        ch for ch in unicodedata.normalize("NFD", s)
        if unicodedata.category(ch) != "Mn"
    )


def _norm_cmd(s: str) -> str:
    s = _strip_accents((s or "").strip()).lower()
    # remove pontuação pra aceitar "relatório cavalaria!" etc.
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# =========================
# HELPERS: split relatório
# =========================
def split_relatorios_por_dia(texto: str) -> list[str]:
    """
    Divide a saída do extrator em blocos (1 por dia).
    Cada bloco começa com '*RESUMO OPERACIONAL*' no início da linha.
    """
    texto = (texto or "").strip()
    if not texto:
        return []

    partes = re.split(r"(?m)(?=^\*RESUMO OPERACIONAL\*)", texto)
    partes = [p.strip() for p in partes if p and p.strip()]
    return partes


def chunk_text_max(texto: str, max_len: int = WA_SAFE) -> list[str]:
    """
    Quebra texto em pedaços <= max_len, preferindo cortar em quebras de linha.
    Fallback: corte bruto se existir linha muito longa.
    """
    texto = (texto or "").strip()
    if len(texto) <= max_len:
        return [texto]

    linhas = texto.splitlines(True)  # mantém \n
    chunks = []
    buf = ""

    for ln in linhas:
        if len(buf) + len(ln) > max_len:
            if buf.strip():
                chunks.append(buf.strip())
            buf = ln
        else:
            buf += ln

    if buf.strip():
        chunks.append(buf.strip())

    # fallback: caso venha uma linha gigante sem \n
    final = []
    for c in chunks:
        if len(c) <= max_len:
            final.append(c)
        else:
            for i in range(0, len(c), max_len):
                part = c[i:i+max_len].strip()
                if part:
                    final.append(part)

    return [x for x in final if x]


def enviar_relatorios_por_dia_whatsapp(phone_id: str, to: str, texto: str):
    """
    Envia 1 mensagem por dia.
    Se algum dia exceder o limite do WhatsApp, quebra apenas aquele dia em partes.
    """
    blocos = split_relatorios_por_dia(texto)
    if not blocos:
        enviar_whatsapp(phone_id, to, "⚠️ Não encontrei relatórios no boletim.")
        return

    log.info(f"[RELATORIO] dias={len(blocos)} total_chars={len(texto)}")

    for i, bloco in enumerate(blocos, start=1):
        log.info(f"[RELATORIO] dia#{i} chars={len(bloco)}")

        if len(bloco) <= WA_MAX:
            enviar_whatsapp(phone_id, to, bloco)
            continue

        partes = chunk_text_max(bloco, max_len=WA_SAFE)
        for idx, p in enumerate(partes, start=1):
            sufixo = f"\n\n_(continua {idx}/{len(partes)})_" if len(partes) > 1 else ""
            enviar_whatsapp(phone_id, to, p + sufixo)


# =========================
# EXTRATOR: carregar teste_v21.py com mensagens claras
# =========================
_EXTRATOR_FN = None
_EXTRATOR_ERR = None

def _carregar_extrator():
    """
    Tenta importar gerar_relatorios_por_dia de teste_v21.py.
    Se falhar, tenta carregar via caminho do arquivo.
    Guarda o erro detalhado em _EXTRATOR_ERR.
    """
    global _EXTRATOR_FN, _EXTRATOR_ERR
    if _EXTRATOR_FN is not None:
        return

    # 1) import normal
    try:
        from teste_v21 import gerar_relatorios_por_dia  # noqa
        _EXTRATOR_FN = gerar_relatorios_por_dia
        _EXTRATOR_ERR = None
        return
    except Exception as e:
        err1 = repr(e)

    # 2) import via arquivo local
    try:
        path = pathlib.Path(__file__).with_name("teste_v21.py")
        if not path.exists():
            _EXTRATOR_FN = None
            _EXTRATOR_ERR = (
                f"teste_v21.py não encontrado no deploy (esperado em {path}). "
                f"Erro do import padrão: {err1}"
            )
            return

        spec = importlib.util.spec_from_file_location("teste_v21", str(path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore

        if not hasattr(mod, "gerar_relatorios_por_dia"):
            _EXTRATOR_FN = None
            _EXTRATOR_ERR = "teste_v21.py carregou, mas não tem a função gerar_relatorios_por_dia()."
            return

        _EXTRATOR_FN = getattr(mod, "gerar_relatorios_por_dia")
        _EXTRATOR_ERR = None
        return

    except Exception as e2:
        _EXTRATOR_FN = None
        _EXTRATOR_ERR = f"Falha ao carregar teste_v21.py. Import padrão: {err1} | Loader: {repr(e2)}"


# =========================
# GOOGLE DRIVE: pegar mês+pdf mais recentes
# =========================
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
MESES = {
    "janeiro": 1,
    "fevereiro": 2,
    "marco": 3,
    "março": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}

def _parse_month_year_from_name(name: str):
    if not name:
        return None
    t = _strip_accents(name).lower()

    my = re.search(r"(20\d{2})", t)
    if not my:
        return None
    year = int(my.group(1))

    for mn, mv in MESES.items():
        if mn in t:
            return (year, mv)

    m1 = re.search(r"\b(0?[1-9]|1[0-2])\b\s*[-_./ ]\s*(20\d{2})\b", t)
    if m1:
        return (int(m1.group(2)), int(m1.group(1)))
    m2 = re.search(r"\b(20\d{2})\b\s*[-_./ ]\s*\b(0?[1-9]|1[0-2])\b", t)
    if m2:
        return (int(m2.group(1)), int(m2.group(2)))

    return None


def _get_service_account_file() -> str:
    """
    Prioridade:
    1) GOOGLE_SERVICE_ACCOUNT_FILE (caminho)
    2) GOOGLE_SERVICE_ACCOUNT_JSON (conteúdo JSON cru OU base64)
    """
    log.info(f"[ENV] GOOGLE_SERVICE_ACCOUNT_JSON len={len(os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON') or '')}")
    sa_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if sa_file and os.path.exists(sa_file):
        return sa_file

    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        raise RuntimeError(
            "Faltou configurar a Service Account. "
            "Use GOOGLE_SERVICE_ACCOUNT_FILE (caminho) OU GOOGLE_SERVICE_ACCOUNT_JSON (conteúdo do JSON)."
        )

    # tenta JSON direto
    data = None
    try:
        data = json.loads(raw)
    except Exception:
        pass

    # tenta base64
    if data is None:
        try:
            decoded = base64.b64decode(raw).decode("utf-8")
            data = json.loads(decoded)
        except Exception as e:
            raise RuntimeError(
                "GOOGLE_SERVICE_ACCOUNT_JSON não é JSON válido nem base64 de JSON. "
                f"Detalhe: {repr(e)}"
            )

    tmp_path = os.path.join(tempfile.gettempdir(), "google_sa.json")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return tmp_path


def get_drive_service():
    if service_account is None or build is None:
        raise RuntimeError(
            "Dependências do Google Drive não instaladas. "
            "Instale: google-api-python-client google-auth google-auth-httplib2"
        )

    sa_file = _get_service_account_file()

    creds = service_account.Credentials.from_service_account_file(
        sa_file, scopes=DRIVE_SCOPES
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _list_folders(service, parent_folder_id: str):
    q = (
        f"'{parent_folder_id}' in parents and "
        "mimeType='application/vnd.google-apps.folder' and trashed=false"
    )
    resp = service.files().list(
        q=q,
        fields="files(id,name,modifiedTime,createdTime)",
        pageSize=200,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    return resp.get("files", [])


def _choose_latest_month_folder(folders):
    parsed = []
    fallback = []

    for f in folders:
        name = f.get("name", "")
        my = _parse_month_year_from_name(name)
        if my:
            parsed.append((my[0], my[1], f))
        else:
            mt = f.get("modifiedTime") or f.get("createdTime") or ""
            fallback.append((mt, f))

    if parsed:
        parsed.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return parsed[0][2]

    if fallback:
        fallback.sort(key=lambda x: x[0], reverse=True)
        return fallback[0][1]

    return None


def get_latest_pdf_in_folder(service, folder_id: str):
    q = f"'{folder_id}' in parents and trashed=false and mimeType='application/pdf'"
    resp = service.files().list(
        q=q,
        fields="files(id,name,modifiedTime)",
        orderBy="modifiedTime desc",
        pageSize=1,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    files = resp.get("files", [])
    return files[0] if files else None


def download_file(service, file_id: str, filename_hint: str = "boletim.pdf"):
    safe = re.sub(r"[^\w\-. ]+", "_", filename_hint).strip() or "boletim.pdf"
    out_path = os.path.join(tempfile.gettempdir(), safe)

    req = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    fh = io.FileIO(out_path, "wb")
    downloader = MediaIoBaseDownload(fh, req)

    done = False
    while not done:
        _, done = downloader.next_chunk()

    return out_path


def baixar_pdf_mais_recente_do_mes(parent_folder_id: str):
    service = get_drive_service()

    pastas = _list_folders(service, parent_folder_id)
    pasta_mes = _choose_latest_month_folder(pastas)

    if not pasta_mes:
        raise RuntimeError("Não encontrei pastas dentro da pasta raiz do Drive.")

    log.info(f"[DRIVE] Pasta do mês escolhida: {pasta_mes.get('name')} ({pasta_mes.get('id')})")

    pdf = get_latest_pdf_in_folder(service, pasta_mes["id"])
    if not pdf:
        raise RuntimeError(
            f"Não encontrei PDF na pasta do mês mais recente: {pasta_mes.get('name')}"
        )

    log.info(f"[DRIVE] PDF mais recente: {pdf.get('name')} ({pdf.get('id')}) mod={pdf.get('modifiedTime')}")

    local_path = download_file(service, pdf["id"], pdf.get("name", "boletim.pdf"))
    log.info(f"[DRIVE] PDF baixado em: {local_path}")

    return {
        "pasta_mes": pasta_mes,
        "pdf": pdf,
        "local_path": local_path,
    }

# =========================
# RELATÓRIO CAVALARIA
# =========================
def gerar_relatorio_cavalaria_texto() -> str:
    _carregar_extrator()
    if _EXTRATOR_FN is None:
        raise RuntimeError(
            "Não consegui carregar o extrator (teste_v21.py). "
            f"Detalhe: {_EXTRATOR_ERR}\n"
            "Dica: verifique se teste_v21.py está no GitHub (mesmo diretório do bot.py) "
            "e se as dependências estão no requirements (pdfplumber, pypdf)."
        )

    parent_folder_id = os.getenv("DRIVE_PARENT_FOLDER_ID", "1QXGtE5ApdNXFG5UnrZodcrhDOHpNDK1b")
    link_escalas = os.getenv(
        "DRIVE_PUBLIC_LINK",
        "https://drive.google.com/drive/folders/1QXGtE5ApdNXFG5UnrZodcrhDOHpNDK1b",
    )

    info = baixar_pdf_mais_recente_do_mes(parent_folder_id)
    pdf_local = info["local_path"]

    buf = io.StringIO()
    with redirect_stdout(buf):
        _EXTRATOR_FN(pdf_local, link_escalas)

    texto = buf.getvalue().strip()
    if not texto:
        raise RuntimeError("O extrator rodou, mas não gerou saída (texto vazio).")

    # IMPORTANTE: retorna SÓ o texto do extrator.
    # O envio por dia já está no enviar_relatorios_por_dia_whatsapp()
    return texto


# =========================
# JOB em background (evita timeout do webhook/gunicorn)
# =========================
def _rodar_e_enviar_relatorio_cavalaria(phone_id: str, to: str):
    try:
        relatorio = gerar_relatorio_cavalaria_texto()
        enviar_relatorios_por_dia_whatsapp(phone_id, to, relatorio)
    except Exception as e:
        log.error(f"[RELATORIO_CAVALARIA] Erro no job: {e}", exc_info=True)
        enviar_whatsapp(phone_id, to, f"❌ Não consegui gerar o relatório: {e}")
    finally:
        _job_end(to)


# =========================
# WEBHOOK PRINCIPAL
# =========================
@app.post("/webhook")
def webhook():
    data = request.get_json(force=True)

    try:
        value = data["entry"][0]["changes"][0]["value"]

        if "messages" not in value:
            return jsonify({"ignored": True, "reason": "no_messages"}), 200

        msg = value["messages"][0]
        phone_id = value["metadata"]["phone_number_id"]
        from_ = msg["from"]
        text = msg.get("text", {}).get("body", "")

        message_id = msg.get("id")
        if not message_id:
            log.warning("Mensagem sem ID, ignorando por segurança.")
            return jsonify({"ok": True}), 200

        if not text:
            log.info("[MSG] Recebida mensagem sem texto (talvez mídia).")
            return jsonify({"ok": True}), 200

    except Exception as e:
        log.debug(f"Webhook ignorado: {e}")
        return jsonify({"ignored": True}), 200

    if dedup.seen(message_id):
        log.info(f"[DEDUP] Mensagem duplicada ignorada: {message_id}")
        return jsonify({"ok": True}), 200

    log.info(f"[MSG NOVA] {from_}: {text}")

    # ============================
    # COMANDO DIRETO: RELATÓRIO CAVALARIA (rodar fora da request)
    # ============================
    cmd = _norm_cmd(text)
    if "relatorio" in cmd and "cavalaria" in cmd:
        # evita disparar 2 vezes se o usuário mandar de novo
        if not _job_start(from_, ttl=300):
            enviar_whatsapp(phone_id, from_, "⏳ Já estou gerando seu relatório. Assim que terminar eu envio.")
            return jsonify({"ok": True, "handled": "relatorio_cavalaria_already_running"}), 200

        enviar_whatsapp(phone_id, from_, "⏳ Gerando relatório cavalaria...")

        t = threading.Thread(
            target=_rodar_e_enviar_relatorio_cavalaria,
            args=(phone_id, from_),
            daemon=True
        )
        t.start()

        # responde rápido pro webhook não dar timeout
        return jsonify({"ok": True, "handled": "relatorio_cavalaria_started"}), 200

    # ============================
    # FLUXO NORMAL (base normativa + LLM)
    # ============================
    query = expand_query(text)
    resultados = buscar_topk_multi(query, k=5)

    if not resultados:
        enviar_whatsapp(phone_id, from_, "Não encontrei base normativa para responder sua pergunta.")
        return jsonify({"ok": True}), 200

    resposta = gerar_resposta(text, resultados)
    enviar_whatsapp(phone_id, from_, resposta)

    return jsonify({"ok": True}), 200


# =========================
# SEND MESSAGE
# =========================
@app.post("/send-message")
def send_message():
    try:
        admin_token = os.getenv("ADMIN_TOKEN")
        if admin_token:
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {admin_token}":
                log.warning("[SEND-MESSAGE] Authorization inválido")
                return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True)

        if not data.get("to"):
            return jsonify({"error": "Campo 'to' é obrigatório"}), 400
        if not data.get("message"):
            return jsonify({"error": "Campo 'message' é obrigatório"}), 400

        to = data["to"]
        message = data["message"]

        phone_id = os.getenv("WHATSAPP_PHONE_ID")
        if not phone_id:
            return jsonify({"error": "WHATSAPP_PHONE_ID não configurado"}), 500

        log.info(f"[SEND-MESSAGE] Enviando para {to}: {message[:60]}...")
        enviar_whatsapp(phone_id, to, message)

        return jsonify({"success": True, "to": to, "message_length": len(message)}), 200

    except Exception as e:
        log.error(f"[SEND-MESSAGE] Erro: {e}")
        return jsonify({"error": str(e)}), 500


# =========================
# SIMULATE MESSAGE
# =========================
@app.post("/simulate-message")
def simulate_message():
    try:
        admin_token = os.getenv("ADMIN_TOKEN")
        if admin_token:
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return jsonify({"error": "Authorization header inválido"}), 401
            token = auth_header.replace("Bearer ", "").strip()
            if token != admin_token:
                log.warning("[SIMULATE-MESSAGE] Authorization inválido")
                return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True)
        from_ = data.get("from")
        text = data.get("text", "")
        response = data.get("response")

        if not from_:
            return jsonify({"error": "Campo 'from' obrigatório"}), 400

        phone_id = os.getenv("WHATSAPP_PHONE_ID")
        if not phone_id:
            return jsonify({"error": "WHATSAPP_PHONE_ID não configurado"}), 500

        log.info(f"[SIMULATE-MESSAGE] Simulando mensagem de {from_}: {text[:50]}...")

        if response:
            enviar_whatsapp(phone_id, from_, response)
            return jsonify({"success": True, "from": from_, "response_sent": True, "response_length": len(response)}), 200

        if not text:
            return jsonify({"error": "Campo 'text' ou 'response' obrigatório"}), 400

        cmd = _norm_cmd(text)
        if "relatorio" in cmd and "cavalaria" in cmd:
            if not _job_start(from_, ttl=300):
                enviar_whatsapp(phone_id, from_, "⏳ Já estou gerando seu relatório. Assim que terminar eu envio.")
                return jsonify({"success": True, "from": from_, "handled": "relatorio_cavalaria_already_running"}), 200

            enviar_whatsapp(phone_id, from_, "⏳ Gerando relatório cavalaria...")

            t = threading.Thread(
                target=_rodar_e_enviar_relatorio_cavalaria,
                args=(phone_id, from_),
                daemon=True
            )
            t.start()

            return jsonify({"success": True, "from": from_, "handled": "relatorio_cavalaria_started"}), 200

        query = expand_query(text)
        resultados = buscar_topk_multi(query, k=5)

        if not resultados:
            enviar_whatsapp(phone_id, from_, "Não encontrei base normativa para responder sua pergunta.")
            return jsonify({"success": True, "from": from_, "no_results": True}), 200

        resposta = gerar_resposta(text, resultados)
        enviar_whatsapp(phone_id, from_, resposta)

        return jsonify({"success": True, "from": from_, "response_sent": True, "response_length": len(resposta)}), 200

    except Exception as e:
        log.error(f"[SIMULATE-MESSAGE] Erro: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=DEBUG)

