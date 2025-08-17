*** Begin Patch
*** Update File: app.py
@@
-OPENAI_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", "350"))  # limite curto de saída
+OPENAI_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", "350"))  # limite curto de saída
 
 # RAG
 EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
 # use /data/rag.db se você criou um Volume no Railway montado em /data
 DB_PATH     = os.getenv("RAG_DB", "/data/rag.db")
+# limiar mínimo de similaridade cosseno para aceitar um trecho (0–1).
+# 0.25–0.35 costuma funcionar bem; ajuste se necessário.
+RAG_MIN_SIM = float(os.getenv("RAG_MIN_SIM", "0.28"))
+# 1 = NUNCA cair no fallback “geral” (impede alucinação); 0 = pode cair.
+RAG_STRICT  = os.getenv("RAG_STRICT", "1") == "1"
@@
 SYSTEM_PROMPT = (
     "Você é o BotPMPR, assistente para policiais militares no WhatsApp. "
     "Responda SEM rodeios, em português do Brasil, no tom operacional.\n\n"
     "REGRAS DE ESTILO:\n"
     "1) No máximo 6 linhas (ou 5 passos numerados).\n"
     "2) Frases curtas, voz ativa, sem desculpas.\n"
     "3) Use *negrito* só para termos-chave.\n"
     "4) Quando útil, liste no máximo 3 pontos (•). Nada de parágrafos longos.\n"
-    "5) Faça 1 pergunta de esclarecimento apenas se faltar algo ESSENCIAL.\n"
-    "6) Se citar norma/procedimento, cite sigla/ato e artigo quando disponível no contexto (ex.: [1]).\n"
+    "5) Faça 1 pergunta de esclarecimento apenas se faltar algo ESSENCIAL.\n"
+    "6) Se citar norma/procedimento, cite sigla/ato e artigo quando disponível no contexto (ex.: [1]).\n"
+    "7) Se NÃO houver base nos trechos fornecidos, diga claramente que não encontrou nos documentos e peça o termo/nº do ato. NÃO invente.\n"
 )
@@
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
-    return scored[:k]
+    # Filtra por limiar de confiança
+    filtered = [s for s in scored[: max(k, 10)] if s["score"] >= RAG_MIN_SIM]
+    return filtered[:k]
 
 def build_context_snippets(items: List[Dict], max_chars: int = 600) -> str:
     lines = []
     for i, it in enumerate(items, start=1):
         content = it["content"]
         if len(content) > max_chars:
             content = content[:max_chars].rstrip() + "…"
         lines.append(f"[{i}] {it['source']} :: {content}")
     return "\n\n".join(lines)
 
+def build_sources_footer(items: List[Dict]) -> str:
+    if not items:
+        return ""
+    parts = []
+    for i, it in enumerate(items, start=1):
+        parts.append(f"[{i}] {it['source']}")
+    return "\n\nFontes: " + "; ".join(parts)
+
 def ask_ai_with_context(user_text: str) -> str:
     """Consulta a OpenAI usando contexto recuperado do índice (RAG)."""
     if not OPENAI_API_KEY:
         return "A IA não está configurada (OPENAI_API_KEY ausente)."
 
-    snippets = retrieve(user_text, k=5)
-    if not snippets:
-        # fallback sem RAG
-        return ask_ai(user_text)
+    snippets = retrieve(user_text, k=5)
+    # Se não há evidência suficiente nos documentos, não responda “de cabeça”.
+    if not snippets:
+        if RAG_STRICT:
+            return ("Não localizei essa informação nos documentos disponíveis. "
+                    "Envie o termo/nº do ato (ex.: portaria, artigo) ou detalhe melhor para eu procurar.")
+        # modo não-estrito (opcional): permitir fallback geral
+        return ask_ai(user_text)
 
     context = build_context_snippets(snippets)
     system = SYSTEM_PROMPT + (
         "\n\nUse os trechos das fontes a seguir como contexto quando relevante. "
-        "Se citar algo, referencie o número do trecho entre colchetes, ex.: [1], [2].\n\n"
+        "Responda SOMENTE com base neles. Se algo não estiver nas fontes, diga que não encontrou. "
+        "Sempre que citar, referencie o número do trecho entre colchetes (ex.: [1], [2]).\n\n"
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
-        return (completion.choices[0].message.content or "").strip()
+        answer = (completion.choices[0].message.content or "").strip()
+        # Anexa rodapé de fontes para o usuário ter transparência
+        answer = answer + build_sources_footer(snippets)
+        return answer
     except Exception as e:
         logger.error("Erro OpenAI (RAG): %s", getattr(e, "message", str(e)))
-        return "Não consegui consultar a IA agora. Tente novamente em instantes."
+        return "Não consegui consultar a IA agora. Tente novamente em instantes."
*** End Patch
