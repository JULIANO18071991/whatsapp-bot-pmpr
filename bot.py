# bot.py
# -*- coding: utf-8 -*-

import os
import io
import re
import json
import unicodedata
import logging
import tempfile
import requests
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
#   pip install google-api-python-client google-auth google-auth-httplib2
try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
except Exception:
    service_account = None
    build = None
    MediaIoBaseDownload = None

# ========= EXTRATOR =========
# Garanta que teste_v21.py está no mesmo diretório do bot.py
# e que expõe a função gerar_relatorios_por_dia(caminho_pdf, link_escalas)
try:
    from teste_v21 import gerar_relatorios_por_dia
except Exception:
    gerar_relatorios_por_dia = None

DEBUG = os.getenv("DEBUG", "0") == "1"

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("bot")

app = Flask(__name__)

# Deduplicador global (TTL em segundos)
dedup = Dedup(ttl=600)


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

    # Loga sempre a resposta
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

    # Se OK, encerra
    if r.ok:
        return

    # Tenta entender se é erro de janela 24h / precisa template
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
    s = re.sub(r"\s+", " ", s)
    return s


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
    """
    Tenta extrair (ano, mes) do nome da pasta.
    Ex.: "02 - Fevereiro 2026", "Fevereiro_2026", "2026-02", etc.
    """
    if not name:
        return None

    t = _strip_accents(name).lower()

    # ano
    my = re.search(r"(20\d{2})", t)
    if not my:
        return None
    year = int(my.group(1))

    # mês por nome
    for mn, mv in MESES.items():
        if mn in t:
            return (year, mv)

    # mês por número próximo do ano (ex.: 02/2026, 2026-02, 02-2026)
    m1 = re.search(r"\b(0?[1-9]|1[0-2])\b\s*[-_./ ]\s*(20\d{2})\b", t)
    if m1:
        return (int(m1.group(2)), int(m1.group(1)))
    m2 = re.search(r"\b(20\d{2})\b\s*[-_./ ]\s*\b(0?[1-9]|1[0-2])\b", t)
    if m2:
        return (int(m2.group(1)), int(m2.group(2)))

    return None


def get_drive_service():
    """
    Railway: usa GOOGLE_SERVICE_ACCOUNT_JSON (conteúdo inteiro do JSON).
    Local: pode usar GOOGLE_SERVICE_ACCOUNT_FILE (caminho do arquivo).
    """
    if service_account is None or build is None:
        raise RuntimeError(
            "Dependências do Google Drive não instaladas. "
            "Instale: google-api-python-client google-auth google-auth-httplib2"
        )

    # 1) Railway: JSON na env
    sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if sa_json:
        try:
            info = json.loads(sa_json)
            creds = service_account.Credentials.from_service_account_info(
                info, scopes=DRIVE_SCOPES
            )
            return build("drive", "v3", credentials=creds, cache_discovery=False)
        except Exception as e:
            raise RuntimeError(f"GOOGLE_SERVICE_ACCOUNT_JSON inválido: {e}")

    # 2) Fallback: arquivo local
    sa_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not sa_file:
        raise RuntimeError(
            "Defina GOOGLE_SERVICE_ACCOUNT_JSON (Railway) ou "
            "GOOGLE_SERVICE_ACCOUNT_FILE (caminho para o JSON)."
        )

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
    """
    Prioriza (ano, mes) extraído do NOME; se não conseguir, usa modifiedTime/createdTime.
    """
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

    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    fh = io.FileIO(out_path, "wb")
    downloader = MediaIoBaseDownload(fh, request)

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

    pdf = get_latest_pdf_in_folder(service, pasta_mes["id"])
    if not pdf:
        raise RuntimeError(
            f"Não encontrei PDF na pasta do mês mais recente: {pasta_mes.get('name')}"
        )

    local_path = download_file(service, pdf["id"], pdf.get("name", "boletim.pdf"))
    return {
        "pasta_mes": pasta_mes,
        "pdf": pdf,
        "local_path": local_path,
    }


# =========================
# RELATÓRIO CAVALARIA: baixa + extrai + retorna texto
# =========================
def gerar_relatorio_cavalaria_texto() -> str:
    if gerar_relatorios_por_dia is None:
        raise RuntimeError(
            "Não consegui importar gerar_relatorios_por_dia de teste_v21.py. "
            "Verifique se teste_v21.py está no mesmo diretório e sem erros."
        )

    parent_folder_id = os.getenv(
        "DRIVE_PARENT_FOLDER_ID", "1QXGtE5ApdNXFG5UnrZodcrhDOHpNDK1b"
    )
    link_escalas = os.getenv(
        "DRIVE_PUBLIC_LINK",
        "https://drive.google.com/drive/folders/1QXGtE5ApdNXFG5UnrZodcrhDOHpNDK1b",
    )

    info = baixar_pdf_mais_recente_do_mes(parent_folder_id)
    pdf_local = info["local_path"]
    pasta_nome = info["pasta_mes"].get("name", "Pasta do mês")
    pdf_nome = info["pdf"].get("name", "PDF")

    buf = io.StringIO()
    with redirect_stdout(buf):
        gerar_relatorios_por_dia(pdf_local, link_escalas)

    texto = buf.getvalue().strip()
    if not texto:
        raise RuntimeError("O extrator rodou, mas não gerou saída (texto vazio).")

    header = f"📄 Relatório Cavalaria\n📁 Pasta: {pasta_nome}\n🗂️ PDF: {pdf_nome}\n\n"
    return header + texto


# =========================
# WEBHOOK PRINCIPAL
# =========================
@app.post("/webhook")
def webhook():
    data = request.get_json(force=True)

    try:
        value = data["entry"][0]["changes"][0]["value"]

        # Ignora eventos que não são mensagens (ex: statuses)
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

    # DEDUPLICAÇÃO
    if dedup.seen(message_id):
        log.info(f"[DEDUP] Mensagem duplicada ignorada: {message_id}")
        return jsonify({"ok": True}), 200

    log.info(f"[MSG NOVA] {from_}: {text}")

    # ============================
    # COMANDO DIRETO: RELATÓRIO CAVALARIA (1 mensagem só)
    # ============================
    cmd = _norm_cmd(text)
    if cmd == "relatorio cavalaria":
        try:
            enviar_whatsapp(
                phone_id,
                from_,
                "⏳ Gerando relatório cavalaria (Drive + PDF + extração)..."
            )

            relatorio = gerar_relatorio_cavalaria_texto()

            # UMA ÚNICA MENSAGEM (SEM SPLIT)
            enviar_whatsapp(phone_id, from_, relatorio)

        except Exception as e:
            log.error(f"[RELATORIO_CAVALARIA] Erro: {e}", exc_info=True)
            enviar_whatsapp(phone_id, from_, f"❌ Não consegui gerar o relatório: {e}")

        return jsonify({"ok": True, "handled": "relatorio_cavalaria"}), 200

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
    """
    Endpoint para enviar mensagens via WhatsApp sob demanda (ideal pro Manus)

    AUTH (recomendado): header
      Authorization: Bearer <ADMIN_TOKEN>

    Payload esperado:
    {
      "to": "5541997815018",
      "message": "Texto da mensagem"
    }
    """
    try:
        admin_token = os.getenv("ADMIN_TOKEN")
        if admin_token:
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {admin_token}":
                log.warning("[SEND-MESSAGE] Authorization inválida")
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

        return jsonify({
            "success": True,
            "to": to,
            "message_length": len(message)
        }), 200

    except Exception as e:
        log.error(f"[SEND-MESSAGE] Erro: {e}")
        return jsonify({"error": str(e)}), 500


# =========================
# SIMULATE MESSAGE
# =========================
@app.post("/simulate-message")
def simulate_message():
    """
    Simula uma mensagem recebida do WhatsApp, fazendo o bot processar
    e responder como se fosse uma mensagem real do usuário.

    AUTH: Authorization: Bearer <ADMIN_TOKEN>

    Payload:
    {
        "from": "5541997815018",
        "text": "RELATORIO_DIARIO",
        "response": "Texto da resposta que o bot deve enviar"
    }

    Se "response" for fornecido, o bot envia diretamente sem processar LLM.
    Caso contrário, processa normalmente (busca + LLM).
    """
    try:
        admin_token = os.getenv("ADMIN_TOKEN")
        if admin_token:
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return jsonify({"error": "Authorization header inválido"}), 401
            token = auth_header.replace("Bearer ", "").strip()
            if token != admin_token:
                log.warning(f"[SIMULATE-MESSAGE] Authorization inválido")
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

        # Resposta direta (sem LLM)
        if response:
            log.info(f"[SIMULATE-MESSAGE] Enviando resposta direta (sem LLM)")
            enviar_whatsapp(phone_id, from_, response)
            return jsonify({
                "success": True,
                "from": from_,
                "response_sent": True,
                "response_length": len(response)
            }), 200

        if not text:
            return jsonify({"error": "Campo 'text' ou 'response' obrigatório"}), 400

        # Comando direto também no simulate (pra testar)
        cmd = _norm_cmd(text)
        if cmd == "relatorio cavalaria":
            try:
                enviar_whatsapp(
                    phone_id,
                    from_,
                    "⏳ Gerando relatório cavalaria (Drive + PDF + extração)..."
                )

                relatorio = gerar_relatorio_cavalaria_texto()

                # UMA ÚNICA MENSAGEM (SEM SPLIT)
                enviar_whatsapp(phone_id, from_, relatorio)

                return jsonify({
                    "success": True,
                    "from": from_,
                    "handled": "relatorio_cavalaria"
                }), 200
            except Exception as e:
                log.error(f"[SIMULATE RELATORIO_CAVALARIA] Erro: {e}", exc_info=True)
                enviar_whatsapp(phone_id, from_, f"❌ Não consegui gerar o relatório: {e}")
                return jsonify({"error": str(e)}), 500

        # Fluxo normal
        query = expand_query(text)
        resultados = buscar_topk_multi(query, k=5)

        if not resultados:
            enviar_whatsapp(phone_id, from_, "Não encontrei base normativa para responder sua pergunta.")
            return jsonify({"success": True, "from": from_, "no_results": True}), 200

        resposta = gerar_resposta(text, resultados)
        enviar_whatsapp(phone_id, from_, resposta)

        return jsonify({
            "success": True,
            "from": from_,
            "response_sent": True,
            "response_length": len(resposta)
        }), 200

    except Exception as e:
        log.error(f"[SIMULATE-MESSAGE] Erro: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=DEBUG)
