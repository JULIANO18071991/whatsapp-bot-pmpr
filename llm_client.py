import os
import openai
from typing import List, Dict, Any

openai.api_key = os.environ.get("OPENAI_API_KEY", "")

def _format_results(results: List[Dict[str, Any]]) -> str:
    lines = []
    for r in results:
        lines.append("- Portaria {n} ({ano}) — {lvl} art. {art}  |  Trecho: {snip}\n  Arquivo: {arq}".format(
            n=r.get("numero_portaria",""),
            ano=r.get("ano",""),
            lvl=r.get("parent_level",""),
            art=r.get("artigo_numero",""),
            snip=r.get("snippet",""),
            arq=r.get("arquivo",""),
        ))
    return "\n".join(lines) if lines else "(nenhuma)"

def gerar_resposta(pergunta: str, contexto: str, resultados: List[Dict[str, Any]]) -> str:
    if not openai.api_key:
        corpo = _format_results(resultados)
        return f"(Sem LLM configurado) Você perguntou: {pergunta}\n\nTrechos encontrados:\n{corpo}"

    trechos = "\n".join([
        f"[Portaria {r.get('numero_portaria','')} ({r.get('ano','')}) — {r.get('parent_level','')} art.{r.get('artigo_numero','')}] {r.get('snippet','')}"
        for r in resultados
    ]) or "(nenhum trecho relevante encontrado)"

    system = (
        "Você é um assistente especializado nas Portarias da PMPR. "
        "Responda de forma objetiva, cite a portaria e (quando possível) artigo/parágrafo/inciso. "
        "Se faltar base, admita e aponte caminhos. Evite opinião; foque em trechos normativos."
    )
    user_prompt = f"""Pergunta do usuário: {pergunta}

Histórico recente:
{contexto or '(vazio)'}

Trechos de referência:
{trechos}

Monte uma resposta final objetiva, citando portaria e artigo quando possível. Caso haja múltiplos trechos, sintetize.
"""

    resp = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        temperature=0.3,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ],
    )
    return resp.choices[0].message["content"]
