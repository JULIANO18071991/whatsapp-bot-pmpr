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

    # -----------------------------
    # Viagem para fora / exterior
    # -----------------------------
    r"\bexterior\b": [
        "viagem internacional",
        "viagem ao exterior",
        "viagem para o exterior",
        "viagem ao estrangeiro",
        "viagem para fora do país",
        "saída do país",
        "saída do território nacional",
        "fora do país",
        "fora do território nacional",
        "afastamento do país",
        "missão no exterior",
    ],
    r"\binternacional\b": [
        "viagem internacional",
        "viagem ao exterior",
        "viagem para o exterior",
        "viagem ao estrangeiro",
        "fora do país",
        "saída do país",
        "saída do território nacional",
    ],
    r"\bfora\s+do\s+pa[ií]s\b": [
        "exterior",
        "viagem internacional",
        "viagem ao exterior",
        "saída do país",
        "saída do território nacional",
        "afastamento do país",
    ],
    r"\bsa[ií]da\s+do\s+pa[ií]s\b": [
        "exterior",
        "viagem internacional",
        "viagem ao exterior",
        "fora do país",
        "saída do território nacional",
    ],
    r"\bterrit[oó]rio\s+nacional\b": [
        "fora do território nacional",
        "saída do território nacional",
        "exterior",
        "viagem internacional",
    ],
    r"\bafastamento\b": [
        "afastamento do país",
        "saída do país",
        "saída do território nacional",
        "viagem ao exterior",
        "viagem internacional",
    ],
    r"\bpassaporte\b": [
        "exterior",
        "viagem internacional",
        "viagem ao exterior",
        "saída do país",
    ],
    r"\bvisto\b": [
        "exterior",
        "viagem internacional",
        "viagem ao exterior",
        "saída do país",
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
