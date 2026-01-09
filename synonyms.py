# synonyms.py
# -*- coding: utf-8 -*-

import re
from typing import Dict, List, Set

# =========================
# Configurações
# =========================
# Limita quantas expansões serão adicionadas por termo (para evitar "inflar" a query)
MAX_EXPANSIONS_PER_TERM = 2

# Se True, inclui também a própria sigla na expansão (em geral é redundante, porque ela já está na query)
INCLUDE_SELF_IN_EXPANSIONS = False


# =========================
# Base de sinônimos (RAG-aware)
# =========================
# Estrutura:
# - patterns:
#    - "sigla": regex que detecta a sigla
#    - "texto": regex que detecta a forma por extenso
# - expansions:
#    - "from_sigla": termos a acrescentar quando a sigla aparece
#    - "from_texto": termos a acrescentar quando o texto por extenso aparece
#
# Observação importante:
# - Removi o conflito CPO/CPP: "Comissão de Promoção de Praças" fica apenas em CPP.
# - Se na sua instituição CPO significar outra coisa, ajuste o texto correspondente.
SYNONYMS: Dict[str, Dict[str, Dict[str, List[str]]]] = {
    "CPP": {
        "patterns": {
            "sigla": [
                r"\bCPP\b",
            ],
            "texto": [
                r"Comissão\s+de\s+Promoção\s+de\s+Praça[s]?",
            ],
        },
        "expansions": {
            "from_sigla": [
                "Comissão de Promoção de Praças",
                "Comissão de Promoção de Praça",
            ],
            "from_texto": [
                "CPP",
            ],
        },
    },
    "CPO": {
        "patterns": {
            "sigla": [
                r"\bCPO\b",
            ],
            "texto": [
                # ⚠️ Ajuste aqui conforme o uso real na sua corporação.
                # Coloquei "Oficiais" para não colidir com "Praças".
                r"Comissão\s+de\s+Promoção\s+de\s+Oficial(is)?",
            ],
        },
        "expansions": {
            "from_sigla": [
                "Comissão de Promoção de Oficiais",
            ],
            "from_texto": [
                "CPO",
            ],
        },
    },
    "BOU": {
        "patterns": {
            "sigla": [
                r"\bBOU\b",
            ],
            "texto": [
                r"Boletim\s+de\s+Ocorrência\s+Unificado",
            ],
        },
        "expansions": {
            "from_sigla": [
                "Boletim de Ocorrência Unificado",
            ],
            "from_texto": [
                "BOU",
            ],
        },
    },
    "TCIP": {
        "patterns": {
            "sigla": [
                r"\bTCIP\b",
            ],
            "texto": [
                r"Termo\s+Circunstanciado(\s+de\s+Infração\s+Penal)?",
            ],
        },
        "expansions": {
            "from_sigla": [
                "Termo Circunstanciado de Infração Penal",
                # Evitei adicionar "Termo Circunstanciado" aqui para reduzir ruído.
            ],
            "from_texto": [
                "TCIP",
            ],
        },
    },
    "CICCM": {
        "patterns": {
            "sigla": [
                r"\bCICCM\b",
            ],
            "texto": [
                r"Centro\s+Integrado\s+de\s+Comando\s+e\s+Controle\s+Móvel",
            ],
        },
        "expansions": {
            "from_sigla": [
                "Centro Integrado de Comando e Controle Móvel",
            ],
            "from_texto": [
                "CICCM",
            ],
        },
    },
}


def _dedup_keep_order(items: List[str]) -> List[str]:
    """Remove duplicatas preservando ordem (case-insensitive)."""
    seen: Set[str] = set()
    out: List[str] = []
    for t in items:
        key = t.lower().strip()
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(t.strip())
    return out


def expand_query(query: str) -> str:
    """
    Expande a query adicionando sinônimos/variações quando detecta siglas
    ou termos por extenso.

    Exemplo:
      "prazo TCIP" -> 'prazo TCIP "Termo Circunstanciado de Infração Penal"'
      "Boletim de Ocorrência Unificado" -> 'Boletim... "BOU"'
    """
    if not query or not query.strip():
        return query

    q = query.strip()
    extras: List[str] = []

    for key, entry in SYNONYMS.items():
        sigla_patterns = entry["patterns"].get("sigla", [])
        texto_patterns = entry["patterns"].get("texto", [])

        matched_sigla = any(re.search(p, q, flags=re.IGNORECASE) for p in sigla_patterns)
        matched_texto = any(re.search(p, q, flags=re.IGNORECASE) for p in texto_patterns)

        if matched_sigla:
            expansions = entry["expansions"].get("from_sigla", [])
            if INCLUDE_SELF_IN_EXPANSIONS:
                expansions = [key] + expansions
            extras.extend(expansions[:MAX_EXPANSIONS_PER_TERM])

        if matched_texto:
            expansions = entry["expansions"].get("from_texto", [])
            if INCLUDE_SELF_IN_EXPANSIONS:
                expansions = [key] + expansions
            extras.extend(expansions[:MAX_EXPANSIONS_PER_TERM])

    extras = _dedup_keep_order(extras)

    if not extras:
        return q

    # Adiciona como frases para favorecer phrase match quando suportado
    return q + " " + " ".join(f"\"{t}\"" for t in extras)
