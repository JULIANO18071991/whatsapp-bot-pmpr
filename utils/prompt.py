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
      - Instruções de ESTILO fixas para saída padronizada (sempre em modo parecer explicativo)
      - Histórico relevante (opcional)
      - Trechos recuperados do AutoRAG
      - Pergunta do usuário

    Padrão de saída exigido:
    - Primeira linha deve ser exatamente: "Resposta"
    - Em seguida, um único bloco textual corrido (parágrafo(s) curtos), linguagem formal e objetiva
    - Citar apenas documentos presentes no contexto
    - Uma ÚNICA citação final, no formato: Nome do documento, nº XXX, assunto, DD/MM/AAAA
    """
    # Bloco de contexto com os trechos recuperados
    if not passages:
        context_block = "NENHUM TRECHO ENCONTRADO."
    else:
        context_block = "\n---\n".join(_format_passage(p) for p in passages)

    rules = (
        "Instruções de saída (padrão PMPR):\n"
        "1) Formato obrigatório:\n"
        "   - Linha 1: \"Resposta\"\n"
        "   - Linhas seguintes: texto corrido, tom formal, claro e explicativo, podendo usar até 12 linhas ao todo.\n"
        "2) Baseie-se SOMENTE nos trechos do contexto abaixo — não invente artigos, incisos, números, datas ou nomes de documentos.\n"
        "3) Se houver conflito entre trechos, priorize o documento MAIS RECENTE pela data (considere DD/MM/AAAA após normalização).\n"
        "4) Se a informação solicitada não estiver nos trechos, diga objetivamente que não foi localizada no acervo atual.\n"
        "5) A citação final deve ser ÚNICA e EXATAMENTE neste formato (sem variações):\n"
        "   Nome do documento, nº XXX, assunto, DD/MM/AAAA.\n"
        "   Ex.: \"Memorando nº 001 - ordem ao militar de folga - 25/01/2025\".\n"
        "6) Nunca cite documento que NÃO esteja listado no bloco de contexto.\n"
        "7) Evite bullets, listas e títulos adicionais. Use apenas o cabeçalho \"Resposta\" e parágrafo(s) curtos.\n"
        "8) Quando pertinente, explique sucintamente o procedimento (fluxo/responsáveis) conforme os trechos.\n"
        "9) Não inclua links ou referências externas; não inclua 'Fonte:' ou similares.\n"
        "10) Opcional, se couber: encerre com uma frase curta de apoio, por ex.:\n"
        "    \"Se precisar, posso detalhar os passos do procedimento.\""
    )

    hist_block = _format_history(history or [])

    prompt = (
        f"{rules}\n\n"
        f"Histórico relevante (últimas interações do usuário):\n{hist_block}\n\n"
        f"Contexto (trechos recuperados):\n{context_block}\n\n"
        f"Pergunta do usuário:\n{user_query}\n"
    )
    return prompt
