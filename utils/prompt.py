from typing import List, Dict, Optional
import re

# ---------------------------
# Helpers
# ---------------------------

def _normalize_date(raw: Optional[str]) -> str:
    """
    Tenta normalizar datas comuns para DD/MM/AAAA.
    Aceita formatos: '2025-06-26', '2025/06/26', '2025 06 26', '26-06-2025', etc.
    Se não reconhecer, retorna o texto original.
    """
    if not raw:
        return "s/ data"
    s = raw.strip()

    # AAAA-MM-DD / AAAA/MM/DD / AAAA MM DD
    m = re.match(r"^(\d{4})[-/ ]?(\d{2})[-/ ]?(\d{2})$", s)
    if m:
        a, mm, d = m.groups()
        return f"{d}/{mm}/{a}"

    # DD-MM-AAAA / DD/MM/AAAA / DD MM AAAA
    m = re.match(r"^(\d{2})[-/ ]?(\d{2})[-/ ]?(\d{4})$", s)
    if m:
        d, mm, a = m.groups()
        return f"{d}/{mm}/{a}"

    # '# 2025 06 26 - Portaria ...' (caso comum em PDFs)
    m = re.search(r"(\d{4})[^\d]?(\d{2})[^\d]?(\d{2})", s)
    if m:
        a, mm, d = m.groups()
        return f"{d}/{mm}/{a}"

    return s


def _format_passage(p: Dict) -> str:
    """
    Converte um dicionário de passagem em um bloco de contexto legível pelo LLM.
    Inclui metadados essenciais para permitir a citação correta no final.
    """
    meta = p.get("meta", {}) or {}
    title   = meta.get("title") or meta.get("doc_title") or "Documento"
    number  = meta.get("number") or meta.get("doc_number") or "s/ nº"
    subject = meta.get("subject") or meta.get("assunto") or "assunto não informado"
    date    = _normalize_date(meta.get("date") or meta.get("data"))
    src     = p.get("source_uri", "") or meta.get("source_uri", "") or meta.get("src", "")
    score   = p.get("score", None)
    score_s = f"{score:.3f}" if isinstance(score, (int, float)) else "s/score"

    snippet = (p.get("snippet", "") or "").strip()

    return (
        f"[TITLE:{title} | NUM:{number} | ASSUNTO:{subject} | DATA:{date} | SCORE:{score_s} | SRC:{src}]\n"
        f"{snippet}\n"
    )


def _format_history(history: List[str]) -> str:
    """
    Recebe uma lista de trechos do histórico (strings) e compacta
    em até 5 linhas curtas, para não inflar o prompt.
    """
    if not history:
        return "Sem histórico relevante."
    lines = [f"- {h.strip()}" for h in history if h and h.strip()]
    return "\n".join(lines[:5])


# ---------------------------
# Prompt builder
# ---------------------------

def build_prompt(user_query: str, passages: List[Dict], history: Optional[List[str]] = None) -> str:
    """
    Monta o prompt final com:
      - Instruções de ESTILO fixas para saída padronizada (modo parecer explicativo, sem cabeçalho 'Resposta')
      - Histórico relevante (opcional)
      - Trechos recuperados do AutoRAG
      - Pergunta do usuário

    Padrão de saída exigido:
    - Iniciar diretamente citando o documento normativo (sem introduções genéricas).
    - Texto corrido, linguagem formal e objetiva (até ~12 linhas).
    - Citar apenas documentos presentes no contexto.
    - Encerrar com UMA ÚNICA citação final: Nome do documento, nº XXX, assunto, DD/MM/AAAA.
    """
    # Bloco de contexto com os trechos recuperados
    if not passages:
        context_block = "NENHUM TRECHO ENCONTRADO."
    else:
        context_block = "\n---\n".join(_format_passage(p) for p in passages)

    rules = (
        "Instruções de saída (padrão PMPR):\n"
        "- Inicie a resposta diretamente com o documento normativo, sem introduções genéricas.\n"
        "- Texto corrido, tom formal, claro e explicativo, até 12 linhas.\n"
        "- Baseie-se SOMENTE nos trechos do contexto — não invente artigos, incisos, números ou datas.\n"
        "- Se houver conflito entre trechos, priorize o documento MAIS RECENTE.\n"
        "- Se a informação não estiver nos trechos, diga objetivamente que não foi localizada.\n"
        "- A citação final deve ser ÚNICA e no formato: Nome do documento, nº XXX, assunto, DD/MM/AAAA.\n"
        "- Nunca cite documento fora do contexto.\n"
        "- Não utilize bullets, listas ou títulos adicionais.\n"
    )

    hist_block = _format_history(history or [])

    prompt = (
        f"{rules}\n\n"
        f"Histórico relevante (últimas interações do usuário):\n{hist_block}\n\n"
        f"Contexto (trechos recuperados):\n{context_block}\n\n"
        f"Pergunta do usuário:\n{user_query}\n"
    )
    return prompt
