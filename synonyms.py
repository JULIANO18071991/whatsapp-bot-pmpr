# synonyms.py
import re
from typing import Dict, List

SYNONYMS: Dict[str, List[str]] = {
    r"\bCPO\b": [
        "Comissão de Promoção de Praça",
        "Comissão de Promoção de Praças",
    ],
    r"\bCPP\b": [
        "Comissão de Promoção de Praça",
        "Comissão de Promoção de Praças",
    ],
    r"\bBOU\b": [
        "Boletim de Ocorrência Unificado",
    ],
    r"\bTCIP\b": [
        "Termo Circunstanciado de Infração Penal",
        "Termo Circunstanciado",
    ],
    r"\bCICCM\b": [
        "Centro Integrado de Comando e Controle Móvel",
    ],
}

def expand_query(query: str) -> str:
    if not query or not query.strip():
        return query

    extras: List[str] = []

    for pattern, expansions in SYNONYMS.items():
        if re.search(pattern, query, flags=re.IGNORECASE):
            extras.extend(expansions)

    extras_unique = []
    seen = set()
    for t in extras:
        key = t.lower().strip()
        if key not in seen:
            seen.add(key)
            extras_unique.append(t)

    if not extras_unique:
        return query

    return query + " " + " ".join(f"\"{t}\"" for t in extras_unique)
