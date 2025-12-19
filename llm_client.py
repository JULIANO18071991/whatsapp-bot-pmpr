# llm_client.py
# -*- coding: utf-8 -*-
import os
from collections import Counter, defaultdict
from typing import Any, Dict, List, Tuple
from openai import OpenAI

# -------- OpenAI --------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY não definido.")

client = OpenAI(
    api_key=OPENAI_API_KEY,
    base_url=os.getenv("OPENAI_BASE_URL") or None,
)

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
OPENAI_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))
OPENAI_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", "1536"))

# -------- helpers --------
def _as_str(x: Any) -> str:
    return "" if x is None else str(x)

def _coerce_mem(mem: Any) -> List[Dict[str, str]]:
    """Aceita lista de {role,content} OU string antiga da Memory e converte."""
    if isinstance(mem, list) and mem and isinstance(mem[0], dict) and "role" in mem[0]:
        return mem  # já no formato certo
    if isinstance(mem, str) and mem.strip():
        return [{"role": "user", "content": mem.strip()}]
    return []

def _pick(v: Dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in v and v[k]:
            return v[k]
    raw = v.get("_raw") or {}
    for k in keys:
        if k in raw and raw[k]:
            return raw[k]
    return None

def _fmt_portaria(num: Any, ano: Any) -> str:
    num_s = _as_str(num).strip()
    ano_s = _as_str(ano).strip()
    if num_s and ano_s:
        return f"{num_s}/{ano_s}"
    return num_s or ano_s or "-"

def _format_trechos(trechos: List[Dict[str, Any]], max_chars: int = 6500) -> str:
    """
    Bloco legível com metadados: Portaria/ano, artigo, título e o excerto.
    Isso facilita a LLM citar corretamente.
    """
    linhas: List[str] = []
    for i, t in enumerate(trechos, 1):
        doc_id   = _pick(t, "doc_id", "id", "_id") or "-"
        artigo   = _pick(t, "artigo_numero", "artigo", "section") or "-"
        titulo   = _pick(t, "titulo", "title", "document_title") or "-"
        excerto  = _pick(t, "trecho", "texto", "caput", "ementa") or ""
        score    = _pick(t, "score", "_score", "similarity", "text_score", "sim")
        numero   = _pick(t, "numero_portaria", "num")
        ano      = _pick(t, "ano")

        meta_score = f" (score {float(score):.3f})" if isinstance(score, (int, float)) else ""
        pstr = _fmt_portaria(numero, ano)
        header = f"[{i}] portaria={pstr} | artigo={artigo} | título={titulo} | doc_id={doc_id}{meta_score}"
        linhas.append(header + "\n→ " + _as_str(excerto).strip())
    bloco = "\n\n".join(linhas)
    return bloco if len(bloco) <= max_chars else bloco[:max_chars] + "\n…(trechos truncados)…"

def _extract_meta(trechos: List[Dict[str, Any]]) -> Tuple[Dict[str, List[str]], str]:
    """
    Retorna:
      - mapa { '641/2020': ['4','5','6',...], ... }
      - portaria_majoritaria (ex: '641/2020' ou '641')
    """
    por_map: Dict[str, set] = defaultdict(set)
    contagem: Counter = Counter()
    for t in trechos:
        num = _as_str(_pick(t, "numero_portaria", "num")).strip()
        ano = _as_str(_pick(t, "ano")).strip()
        artigo = _as_str(_pick(t, "artigo_numero", "artigo", "section")).strip()
        chave = _fmt_portaria(num, ano)
        if chave != "-":
            por_map[chave].add(artigo or "-")
            contagem[chave] += 1

    # majoritária
    majoritaria = contagem.most_common(1)[0][0] if contagem else ""
    # normaliza sets -> list ordenada
    por_map_ord: Dict[str, List[str]] = {k: sorted(list(v), key=lambda x: (x == "-", x)) for k, v in por_map.items()}
    return por_map_ord, majoritaria

def _build_messages(pergunta: str, trechos: List[Dict[str, Any]], memoria: Any) -> List[Dict[str, str]]:
    msgs: List[Dict[str, str]] = []

    system_rules = (
        "Você é um assistente jurídico da PMPR que responde de forma objetiva, confiável e didática.\n"
        "Sempre baseie sua resposta APENAS nos TRECHOS RECUPERADOS. Se faltar base, diga exatamente o que falta.\n"
        "Quando a pergunta envolver normas, CITE explicitamente o Documento e o Artigo usados.\n"
        "• Se o número do Documento não aparecer no texto do trecho, use os METADADOS fornecidos (portaria/ano/artigo).\n"
        "  predominante(s) nos trechos e, se possível, indique os artigos onde o tema aparece.\n"
        "Formato de citação sugerido: 'Fonte: Nome do Documento nº Numero do documento/Ano — art. '.\n"
        "Responda em português do Brasil; em respostas longas, finalize com um resumo de 1–2 linhas."
    )
    msgs.append({"role": "system", "content": system_rules})

    # memória (se houver)
    msgs += _coerce_mem(memoria)

    # Bloco de trechos + metadados para ajudar a LLM
    bloco_trechos = _format_trechos(trechos)
    mapa, major = _extract_meta(trechos)
    meta_lines = []
    if mapa:
        parts = []
        for por, arts in mapa.items():
            arts_fmt = ", ".join([a for a in arts if a and a != "-"]) or "s/ artigo indicado"
            parts.append(f"{por} (arts: {arts_fmt})")
        meta_lines.append("PORTARIAS DETECTADAS: " + " | ".join(parts))
    if major:
        meta_lines.append(f"PORTARIA MAJORITÁRIA: {major}")
    meta_text = "\n".join(meta_lines) if meta_lines else "PORTARIAS DETECTADAS: (nenhuma identificada nos metadados)."

    # Passamos os trechos e a meta como mensagem de sistema para que seja tratada como contexto
    msgs.append({"role": "system", "content": "TRECHOS RECUPERADOS:\n" + bloco_trechos})
    msgs.append({"role": "system", "content": "METADADOS:\n" + meta_text})

    # Pergunta do usuário
    msgs.append({"role": "user", "content": pergunta.strip()})

    return msgs

# -------- API pública --------
def gerar_resposta(pergunta: str, trechos: List[Dict[str, Any]], memoria: Any) -> str:
    try:
        messages = _build_messages(pergunta, trechos, memoria)
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            temperature=OPENAI_TEMPERATURE,
            max_tokens=OPENAI_MAX_TOKENS,
        )
        return (resp.choices[0].message.content or "").strip() or "Não consegui gerar uma resposta agora."
    except Exception as e:
        print(f"[ERRO gerar_resposta] {e}")
        return "Desculpe, ocorreu um erro interno ao processar sua solicitação."
