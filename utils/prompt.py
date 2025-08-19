from typing import List, Dict


def _format_passage(p: Dict) -> str:
    meta = p.get("meta", {}) or {}
    title = meta.get("title") or meta.get("doc_title") or "Documento"
    number = meta.get("number") or meta.get("doc_number") or "s/ nº"
    subject = meta.get("subject") or meta.get("assunto") or "assunto não informado"
    date = meta.get("date") or meta.get("data") or "s/ data"
    src = p.get("source_uri", "")
    snippet = p.get("snippet", "")
    return (
        f"[TITLE:{title} | NUM:{number} | ASSUNTO:{subject} | DATA:{date} | SRC:{src}]\n"
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


def build_prompt(user_query: str, passages: List[Dict], history: List[str] | None = None) -> str:
    """
    Monta o prompt final com:
      - Instruções de estilo e citação
      - Histórico relevante (opcional)
      - Trechos recuperados do AutoRAG
      - Pergunta do usuário
    """
    if not passages:
        context_block = "NENHUM TRECHO ENCONTRADO."
    else:
        context_block = "\n---\n".join(_format_passage(p) for p in passages)

    rules = (
        "Instruções:\n"
        "1) Responda em no máximo 3 linhas, direto ao ponto.\n"
        "2) Não invente artigos, incisos ou datas.\n"
        "3) Se a resposta não estiver nos trechos, diga que não localizou no acervo atual.\n"
        "4) Ao final, inclua uma única citação no formato: "
        "Nome do documento, nº XXX, assunto, DD/MM/AAAA.\n"
    )

    hist_block = _format_history(history or [])

    prompt = (
        f"{rules}\n"
        f"Histórico relevante (últimas interações do usuário):\n{hist_block}\n\n"
        f"Contexto (trechos recuperados):\n{context_block}\n\n"
        f"Pergunta do usuário:\n{user_query}\n"
    )
    return prompt
