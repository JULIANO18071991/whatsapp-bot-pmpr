# llm_client.py

# ... (imports e configuração do cliente permanecem os mesmos)

# ... (SYSTEM_PROMPT e _as_dict permanecem os mesmos)

def _build_messages(pergunta: str, trechos: List[Any], memoria: List[Dict]) -> list:
    context_lines = []
    for i, trecho_bruto in enumerate(trechos):
        t = _as_dict(trecho_bruto)
        ref = f"Portaria {t.get('numero_portaria', '?')}, art. {t.get('artigo_numero', '?')}"
        texto = (t.get("texto") or "").strip()
        context_lines.append(f"[Fonte {i+1}] ({ref})\nTrecho: \"{texto}\"")

    context_block = "\n\n".join(context_lines) if context_lines else "Nenhum trecho de contexto foi encontrado."

    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    
    for m in memoria[-3:]:
        role = "assistant" if m.get("role") == "assistant" else "user"
        content = m.get("content", "")
        if content: msgs.append({"role": role, "content": content})
            
    # --- AJUSTE FINAL NO PROMPT ---
    # Trocando a tarefa de "responder" para "resumir o que foi encontrado".
    msgs.append({
        "role": "user",
        "content": (
            f"Tópico da pesquisa: {pergunta}\n\n"
            f"Contexto encontrado:\n{context_block}\n\n"
            f"## Sua Tarefa:\n"
            "1. **Resuma** as informações encontradas no contexto sobre o tópico da pesquisa.\n"
            "2. Organize o resumo em tópicos para fácil leitura.\n"
            "3. **Sempre** cite a fonte de cada informação, como (Portaria 641, art. 4).\n"
            "4. Se o contexto estiver vazio, e somente nesse caso, responda: 'Não encontrei informações sobre \"{pergunta}\" nas portarias consultadas.'"
        )
    })
    return msgs

# ... (o resto do arquivo, incluindo gerar_resposta, permanece o mesmo)
