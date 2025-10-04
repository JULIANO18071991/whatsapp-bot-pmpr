# topk_client.py - Versão Final Compatível

import os
import re
from typing import List, Dict, Any
from topk_sdk import Client
from topk_sdk.query import select, field, fn, match

TOPK_API_KEY = os.environ["TOPK_API_KEY"]
TOPK_REGION = os.environ["TOPK_REGION"]
TOPK_COLLECTION = os.environ.get("TOPK_COLLECTION", "pmpr_portarias")

try:
    _client = Client(api_key=TOPK_API_KEY, region=TOPK_REGION)
except Exception as e:
    print(f"[ERRO TOPK_CLIENT] Falha ao inicializar o cliente: {e}")
    _client = None

def _snippet(txt: str, term: str, window: int = 160) -> str:
    if not txt or not term: return (txt or "")[:window].replace("\n", " ")
    safe_term = re.escape(term.split()[0][:10])
    pat = re.compile(rf"(.{{0,{window}}}{safe_term}.{{0,{window}}})", re.IGNORECASE | re.DOTALL)
    m = pat.search(txt)
    found_text = m.group(1) if m else txt[:window*2]
    return found_text.replace("\n", " ").strip()

def buscar_topk(termo: str, k: int = 5) -> List[Dict[str, Any]]:
    """
    Busca SEMÂNTICA PURA. A mais confiável para encontrar os melhores resultados.
    """
    if not _client:
        print("[ERRO TOPK_CLIENT] Cliente não inicializado.")
        return []

    try:
        # --- CORREÇÃO FINAL ---
        # Removido o .filter() que estava causando o erro.
        # A busca semântica pura é a mais eficaz aqui.
        q = (
            select("_id", "doc_id", "numero_portaria", "ano", "parent_level", "artigo_numero", "texto", "arquivo",
                   sem=fn.semantic_similarity("texto", termo))
            .topk(field("sem"), k)
        )
        
        res_bruta = _client.collection(TOPK_COLLECTION).query(q)

        # A camada de higienização continua sendo uma boa prática.
        resultados_unicos = {}
        for r in res_bruta:
            if not isinstance(r, dict): continue
            doc_id = r.get("_id")
            if doc_id and doc_id not in resultados_unicos:
                r["snippet"] = _snippet(r.get("texto", ""), termo)
                resultados_unicos[doc_id] = r
        
        return list(resultados_unicos.values())[:k]

    except Exception as e:
        print(f"[ERRO em buscar_topk]: {e}")
        import traceback
        traceback.print_exc()
        return []
