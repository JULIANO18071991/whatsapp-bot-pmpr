# topk_client.py - Versão Corrigida e Robusta

import os
import re
from typing import List, Dict, Any
from topk_sdk import Client
from topk_sdk.query import select, field, fn, match

# --- Configuração do Cliente (sem alterações) ---
TOPK_API_KEY = os.environ["TOPK_API_KEY"]
TOPK_REGION = os.environ["TOPK_REGION"]
TOPK_COLLECTION = os.environ.get("TOPK_COLLECTION", "pmpr_portarias")

try:
    _client = Client(api_key=TOPK_API_KEY, region=TOPK_REGION)
except Exception as e:
    print(f"[ERRO TOPK_CLIENT] Falha ao inicializar o cliente: {e}")
    _client = None

# --- Funções Auxiliares (sem alterações) ---
def _snippet(txt: str, term: str, window: int = 160) -> str:
    """Cria um snippet de texto ao redor do termo buscado."""
    if not txt or not term:
        return (txt or "")[:window].replace("\n", " ")
    
    # Escapa o termo para uso seguro em regex e pega os primeiros caracteres
    safe_term = re.escape(term.split()[0][:10])
    
    # Tenta encontrar o termo no texto
    pat = re.compile(rf"(.{{0,{window}}}{safe_term}.{{0,{window}}})", re.IGNORECASE | re.DOTALL)
    m = pat.search(txt)
    
    # Se encontrar, retorna a janela. Senão, retorna o início do texto.
    found_text = m.group(1) if m else txt[:window*2]
    return found_text.replace("\n", " ").strip()

# --- Função de Busca (MODIFICADA) ---
def buscar_topk(termo: str, k: int = 5) -> List[Dict[str, Any]]:
    """
    Busca híbrida robusta: semântica (70%) + BM25 (30%), com desduplicação.
    """
    if not _client:
        print("[ERRO TOPK_CLIENT] Cliente não inicializado. Retornando lista vazia.")
        return []

    try:
        # A query continua a mesma
        q = (
            select("_id", "doc_id", "numero_portaria", "ano", "parent_level", "artigo_numero", "texto", "arquivo",
                   sem=fn.semantic_similarity("texto", termo),
                   bm25=fn.bm25_score())
            .filter(match(termo, field="texto"))
            .topk(field("sem") * 0.7 + field("bm25") * 0.3, k * 2) # Pede mais resultados para garantir k únicos
        )
        
        res_bruta = _client.collection(TOPK_COLLECTION).query(q)

        # --- CAMADA DE HIGIENIZAÇÃO E DESDUPLICAÇÃO ---
        # Esta é a correção principal. Garante que cada documento seja único.
        resultados_unicos = {}
        for r in res_bruta:
            # Garante que 'r' seja um dicionário antes de prosseguir
            if not isinstance(r, dict):
                continue
            
            doc_id = r.get("_id")
            if doc_id and doc_id not in resultados_unicos:
                # Adiciona o snippet ao dicionário limpo
                r["snippet"] = _snippet(r.get("texto", ""), termo)
                resultados_unicos[doc_id] = r
        
        # Converte o dicionário de resultados únicos de volta para uma lista
        lista_final = list(resultados_unicos.values())
        
        # Retorna apenas os 'k' melhores resultados únicos
        return lista_final[:k]

    except Exception as e:
        # Captura qualquer erro durante a busca e retorna uma lista vazia
        print(f"[ERRO em buscar_topk]: {e}")
        import traceback
        traceback.print_exc()
        return []

