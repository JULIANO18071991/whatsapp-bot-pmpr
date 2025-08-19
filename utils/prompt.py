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


def _needs_deepening(user_query: str) -> bool:
    """
    Detecta intenção de aprofundamento (ex.: 'fale mais', 'discorra', 'explique', etc.).
    """
    q = (user_query or "").lower()
    gatilhos = [
        "fale mais", "discorra", "explique melhor", "explique", "detalhe",
        "aprofund", "quero mais detalhes", "contexto", "por quê", "porque?"
    ]
    return any(g in q for g in gatilhos)


# ---------------------------
# Prompt builder
# ---------------------------

def build_prompt(user_query: str, passages: List[Dict], history: Optional[List[str]] = None) -> str:
    """
    Monta o prompt final com:
      - Instruções de estilo e citação (curtas ou estendidas conforme intenção)
      - Histórico relevante (opcional)
      - Trechos recuperados do AutoRAG
      - Pergunta do usuário
    """
    # Bloco de contexto com os trechos recuperados
    if not passages:
        context_block = "NENHUM TRECHO ENCONTRADO."
    else:
        context_block = "\n---\n".join(_format_passage(p) for p in passages)

    # Regras: versão curta (3 linhas) ou expandida (quando o usuário pede para discorrer)
    expand = _needs_deepening(user_query)

    if expand:
        rules = (
            "Instruções (modo EXPLICAR/DETALHAR):\n"
            "1) Explique com clareza, podendo usar até 10 linhas.\n"
            "2) Baseie-se SOMENTE nos trechos do contexto — não invente artigos, incisos, números ou datas.\n"
            "3) Se houver conflito entre trechos, priorize o documento MAIS RECENTE pela data.\n"
            "4) Se a resposta não estiver nos trechos, diga que não localizou no acervo atual.\n"
            "5) Inclua exemplos práticos, contexto/objetivo da norma e possíveis consequências administrativas quando aplicável.\n"
            "6) Ao final, inclua UMA ÚNICA citação neste formato exato: Nome do documento, nº XXX, assunto, DD/MM/AAAA.\n"
            "7) Nunca cite documento que NÃO esteja listado no bloco de contexto.\n"
        )
    else:
        rules = (
            "Instruções (modo OBJETIVO):\n"
            "1) Responda em no máximo 3 linhas, direto ao ponto.\n"
            "2) Baseie-se SOMENTE nos trechos do contexto — não invente artigos, incisos, números ou datas.\n"
            "3) Se houver conflito entre trechos, priorize o documento MAIS RECENTE pela data.\n"
            "4) Se a resposta não estiver nos trechos, diga que não localizou no acervo atual.\n"
            "5) Ao final, inclua UMA ÚNICA citação neste formato exato: Nome do documento, nº XXX, assunto, DD/MM/AAAA.\n"
            "6) Nunca cite documento que NÃO esteja listado no bloco de contexto.\n"
        )

    hist_block = _format_history(history or [])

    prompt = (
        f"{rules}\n"
        f"Histórico relevante (últimas interações do usuário):\n{hist_block}\n\n"
        f"Contexto (trechos recuperados):\n{context_block}\n\n"
        f"Pergunta do usuário:\n{user_query}\n"
    )
    return prompt
