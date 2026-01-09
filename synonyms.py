# synonyms.py
import re
from typing import Dict, List

SYNONYMS: Dict[str, Dict[str, List[str]]] = {
    "CPO": {
        "patterns": [
            r"\bCPO\b",
            r"Comissão\s+de\s+Promoção\s+de\s+Praça[s]?",
        ],
        "expansions": [
            "CPO",
            "Comissão de Promoção de Praça",
            "Comissão de Promoção de Praças",
        ],
    },
    "CPP": {
        "patterns": [
            r"\bCPP\b",
            r"Comissão\s+de\s+Promoção\s+de\s+Praça[s]?",
        ],
        "expansions": [
            "CPP",
            "Comissão de Promoção de Praça",
            "Comissão de Promoção de Praças",
        ],
    },
    "BOU": {
        "patterns": [
            r"\bBOU\b",
            r"Boletim\s+de\s+Ocorrência\s+Unificado",
        ],
        "expansions": [
            "BOU",
            "Boletim de Ocorrência Unificado",
        ],
    },
    "TCIP": {
        "patterns": [
            r"\bTCIP\b",
            r"Termo\s+Circunstanciado(\s+de\s+Infração\s+Penal)?",
        ],
        "expansions": [
            "TCIP",
            "Termo Circunstanciado de Infração Penal",
            "Termo Circunstanciado",
        ],
    },
    "CICCM": {
        "patterns": [
            r"\bCICCM\b",
            r"Centro\s+Integrado\s+de\s+Comando\s+e\s+Controle\s+Móvel",
        ],
        "expansions": [
            "CICCM",
            "Centro Integrado de Comando e Controle Móvel",
        ],
    },
}

def expand_query(query: str) -> str:
    if not query or not query.strip():
        return query

    extras: List[str] = []
    q = query.strip()

    for entry in SYNONYMS.values():
        for pattern in entry["patterns"]:
            if re.search(pattern, q, flags=re.IGNORECASE):
                extras.extend(entry["expansions"])
                break  # evita duplicar pelo mesmo grupo

    # deduplicação
    seen = set()
    extras_unique = []
    for t in extras:
        key = t.lower().strip()
        if key not in seen:
            seen.add(key)
            extras_unique.append(t)

    if not extras_unique:
        return query

    return q + " " + " ".join(f"\"{t}\"" for t in extras_unique)
