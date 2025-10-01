import os
from typing import List, Dict, Any
from topk_sdk import Client
from topk_sdk.query import select, field, fn, match

TOPK_API_KEY = os.environ["TOPK_API_KEY"]
TOPK_REGION = os.environ["TOPK_REGION"]
TOPK_COLLECTION = os.environ.get("TOPK_COLLECTION", "pmpr_portarias")

_client = Client(api_key=TOPK_API_KEY, region=TOPK_REGION)

def _snippet(txt: str, term: str, window: int = 160) -> str:
    if not txt:
        return ""
    import re
    pat = re.compile(rf"(.{{0,{window}}}{re.escape(term[:4])}.{{0,{window}}})", re.I)
    m = pat.search(txt)
    return (m.group(1) if m else txt[:2*window]).replace("\n"," ")

def buscar_topk(termo: str, k: int = 5) -> List[Dict[str, Any]]:
    """Busca híbrida: semântica (70%) + BM25 (30%)."""
    q = (
        select("doc_id","numero_portaria","ano","parent_level","artigo_numero","texto","arquivo",
               sem=fn.semantic_similarity("texto", termo),
               bm25=fn.bm25_score())
        .filter(match(termo, field="texto"))
        .topk(field("sem")*0.7 + field("bm25")*0.3, k)
    )
    res = _client.collection(TOPK_COLLECTION).query(q)
    for r in res:
        r["snippet"] = _snippet(r.get("texto",""), termo)
    return res
