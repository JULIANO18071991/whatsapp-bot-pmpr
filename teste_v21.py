# -*- coding: utf-8 -*-
"""
Extrator de Boletim (P/3) - RPMon
- Gera RESUMO OPERACIONAL a partir do PDF do boletim.
- Suporta boletim com 1 dia ou múltiplos dias (divide por "ESCALA DE SERVIÇO PARA O DIA:").

ATUALIZAÇÃO:
- Adicionado extrair_corp_escala(): extrai blocos "ESCALA CORP (COMPANHIA OPERACIONAL DE RECOBRIMENTO PREVENTIVO)"
  com evento na linha subsequente, períodos por "EQUIPE DO ... PERÍODO" e/ou "Data e hora prevista para a saída/retorno",
  calculando turno com (retorno - 15min) e escolhendo responsável como o policial mais antigo no período.
"""

import os
import re
import tempfile
import pdfplumber

# ============================================================
# UTILITÁRIOS
# ============================================================

def formatar_nome(nome: str) -> str:
    palavras = (nome or "").split()
    excecoes = {"da", "de", "do", "dos", "das"}
    nome_formatado = []
    for p in palavras:
        if p.lower() in excecoes:
            nome_formatado.append(p.lower())
        else:
            nome_formatado.append(p.capitalize())
    return " ".join(nome_formatado)

def normalizar_linha(s: str) -> str:
    # normaliza espaços, remove NBSP etc.
    s = (s or "").replace("\u00a0", " ").replace("\t", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


# ============================================================
# NORMALIZAÇÃO FORTE PARA DETECÇÃO DE MARCADORES (CORP/EXTRA/DIVERSAS)
# ============================================================

import unicodedata

def strip_accents(text: str) -> str:
    text = text or ""
    return "".join(
        ch for ch in unicodedata.normalize("NFD", text)
        if unicodedata.category(ch) != "Mn"
    )

def norm_up(linha: str) -> str:
    """Upper, sem acentos, 0->O, colapsa espaços."""
    s = normalizar_linha(linha)
    s = strip_accents(s).upper()
    s = s.replace("0", "O")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def eh_efetivo_operacional(linha: str) -> bool:
    """
    Detecta 'EFETIVO OPERACIONAL' mesmo com erros comuns de OCR/extração:
    - EETIVO (F omitido)
    - EFETIV0 (0 no lugar de O)
    - quebras/duplos espaços
    """
    s = norm_up(linha)
    if re.search(r"\bE[F]?\s*ETIVO\s+OPERACIONAL\b", s):
        return True
    s2 = re.sub(r"[^A-Z]", "", s)
    return ("EFETIVOOPERACIONAL" in s2) or ("EETIVOOPERACIONAL" in s2)

def eh_inicio_tabela_corp(linha: str) -> bool:
    """
    Gatilho de backup: às vezes o BI não traz 'EFETIVO OPERACIONAL' legível,
    mas a tabela começa com 'VTR ... POSTO/GRAD ... NOME ... RG ... TELEFONE'.
    """
    s = norm_up(linha)
    return (("POSTO/GRAD" in s) and ("VTR" in s)) or s.startswith("Oficial de dia")
# ============================================================
# EXTRAIR DATA
# ============================================================

def extrair_data(caminho_pdf):
    """
    Extrai a data do serviço do boletim.

    Coberturas:
    - "ESCALA DE SERVIÇO PARA O DIA: 06 Janeiro de 2026 (Terça-Feira)" (com/sem "de" após o dia)
    - "ESCALA DE SERVIÇO PARA O DIA: 17 de Fevereiro (Terça-Feira)" (sem ano -> usa ano do cabeçalho "Curitiba, ... 2026")
    - "ESCALA DE SERVIÇO PARA TERÇA-FEIRA 17 DE FEVEREIRO DE 2026" (sem "PARA O DIA:")
    """
    # Meses pt-BR (aceita sem acento também) — NÃO CAPTURAR para não bagunçar grupos
    meses_alt = r"(?:janeiro|fevereiro|mar[cç]o|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)"

    # Data completa (dia + mês + ano) — aceita: "06 Janeiro de 2026" e "06 de Janeiro de 2026" e "06 DE JANEIRO DE 2026"
    padrao_data_com_ano = re.compile(
        rf"\b(\d{{1,2}})\s*(?:de\s+)?({meses_alt})\s*(?:de\s+)?(20\d{{2}})\b",
        re.IGNORECASE
    )

    # Data sem ano (dia + mês) — aceita "17 de Fevereiro"
    padrao_data_sem_ano = re.compile(
        rf"\b(\d{{1,2}})\s*(?:de\s+)?({meses_alt})\b",
        re.IGNORECASE
    )

    # Ano do cabeçalho do boletim (ex.: "Curitiba, 05 Janeiro de 2026")
    padrao_ano_cab = re.compile(
        rf"\bCuritiba\s*,\s*\d{{1,2}}\s*(?:de\s+)?{meses_alt}\s*(?:de\s+)?(20\d{{2}})\b",
        re.IGNORECASE
    )

    def _cap_mes(mes_txt: str) -> str:
        mes_txt = (mes_txt or "").strip().lower()
        if mes_txt in ("marco", "março"):
            return "Março"
        return mes_txt.capitalize()

    ano_padrao = None

    with pdfplumber.open(caminho_pdf) as pdf:
        # 1) tenta achar o ano do cabeçalho (primeiras páginas)
        for pagina in pdf.pages[:min(3, len(pdf.pages))]:
            texto = pagina.extract_text() or ""
            for linha in texto.split("\n"):
                mm = padrao_ano_cab.search(linha)
                if mm:
                    ano_padrao = mm.group(1)
                    break
            if ano_padrao:
                break

        # 2) procura a data do dia do serviço
        for pagina in pdf.pages:
            texto = pagina.extract_text() or ""
            if not texto:
                continue

            for linha in texto.split("\n"):
                up = linha.upper()

                # A) linha padrão do boletim
                if "ESCALA" in up and "SERVI" in up and "PARA O DIA" in up:
                    trecho = linha.split(":", 1)[1].strip() if ":" in linha else linha

                    m1 = padrao_data_com_ano.search(trecho)
                    if m1:
                        dia, mes, ano = m1.group(1), _cap_mes(m1.group(2)), m1.group(3)
                        return f"{int(dia):02d} {mes} {ano}"

                    m2 = padrao_data_sem_ano.search(trecho)
                    if m2 and ano_padrao:
                        dia, mes = m2.group(1), _cap_mes(m2.group(2))
                        return f"{int(dia):02d} {mes} {ano_padrao}"

                # B) fallback: outras variações de "ESCALA DE SERVIÇO PARA ..."
                if "ESCALA" in up and "SERVI" in up:
                    m3 = padrao_data_com_ano.search(linha)
                    if m3:
                        dia, mes, ano = m3.group(1), _cap_mes(m3.group(2)), m3.group(3)
                        return f"{int(dia):02d} {mes} {ano}"

                    if ano_padrao:
                        m4 = padrao_data_sem_ano.search(linha)
                        if m4:
                            dia, mes = m4.group(1), _cap_mes(m4.group(2))
                            return f"{int(dia):02d} {mes} {ano_padrao}"

    return None

def extrair_cabecalho(caminho_pdf: str):
    resultado = []
    capturando = False

    with pdfplumber.open(caminho_pdf) as pdf:
        for pagina in pdf.pages:
            texto = pagina.extract_text()
            if not texto:
                continue

            linhas = texto.split("\n")
            for i, linha in enumerate(linhas):
                linha_limpa = linha.strip()

                # ✅ CORREÇÃO: detecta cabeçalho mesmo quebrado em linhas diferentes
                if not capturando:
                    prev = linhas[i - 1].strip() if i - 1 >= 0 else ""
                    nxt  = linhas[i + 1].strip() if i + 1 < len(linhas) else ""
                    bloco = f"{prev} {linha_limpa} {nxt}"
                    if ("Função" in bloco) and ("Posto/Grad" in bloco):
                        capturando = True
                        continue

                if not capturando:
                    continue

                if "1º EPM" in linha_limpa or "1° EPM" in linha_limpa:
                    return resultado

                if linha_limpa.startswith(("Oficial de Dia", "Adjunto", "Guarda", "Furriel")):
                    linha_limpa = re.sub(r"\d{1,2}h.*", "", linha_limpa)
                    linha_limpa = re.sub(r"\d{7,}", "", linha_limpa)
                    linha_limpa = linha_limpa.replace(" QP PM", "").replace(" QOEM PM", "")
                    linha_limpa = linha_limpa.replace("/", "")
                    linha_limpa = re.sub(r"\s+", " ", linha_limpa).strip()

                    partes = linha_limpa.split()

                    if linha_limpa.startswith("Oficial de Dia"):
                        funcao = "Oficial de Dia"
                        posto = partes[3]
                        nome_bruto = " ".join(partes[4:])
                    else:
                        funcao = partes[0]
                        posto = f"{partes[1]} {partes[2]}"
                        nome_bruto = " ".join(partes[4:])

                    nome = formatar_nome(nome_bruto.lower())
                    resultado.append(f"✅{funcao}: {posto} {nome}")

    return resultado

# ============================================================
# 1º EPM - NOVA LÓGICA REVISADA
# ============================================================

def extrair_1epm(caminho_pdf: str):
    """
    Extrai eventos do 1º EPM.

    Lógica:
    1. Isola o bloco entre 1º EPM e 2º EPM.
    2. Divide por EVENTO:.
    3. Interpreta cada evento individualmente.
    4. Evita evento vazio.
    5. Não deixa evento sem LOCAL engolir descrição, REF, efetivo e assinatura.
    """

    linhas_1epm = extrair_bloco_1epm(caminho_pdf)
    blocos_eventos = dividir_eventos_1epm(linhas_1epm)

    eventos = []

    for bloco in blocos_eventos:
        evento = interpretar_evento_1epm(bloco)

        if evento.get("evento", "").strip():
            eventos.append(evento)

    return eventos


def extrair_bloco_1epm(caminho_pdf: str):
    """
    Extrai somente as linhas do 1º EPM.
    Começa em '1º EPM' e termina em '2º EPM'.
    """

    linhas_1epm = []
    dentro_1epm = False

    with pdfplumber.open(caminho_pdf) as pdf:
        for pagina in pdf.pages:
            texto = pagina.extract_text() or ""
            if not texto.strip():
                continue

            for linha in texto.split("\n"):
                linha_limpa = normalizar_linha(linha)
                if not linha_limpa:
                    continue

                if not dentro_1epm and re.match(
                    r"^\s*1(?:[º°o])?\s*EPM\b",
                    linha_limpa,
                    re.IGNORECASE
                ):
                    dentro_1epm = True
                    continue

                if not dentro_1epm:
                    continue

                if re.match(
                    r"^\s*2(?:[º°o])?\s*EPM\b",
                    linha_limpa,
                    re.IGNORECASE
                ):
                    return linhas_1epm

                linhas_1epm.append(linha_limpa)

    return linhas_1epm


def dividir_eventos_1epm(linhas: list[str]):
    """
    Divide o bloco do 1º EPM em blocos menores, um para cada EVENTO:.

    Ignora a tabela administrativa inicial do 1º EPM.
    """

    eventos = []
    atual = []

    for linha in linhas:
        if re.search(r"\bEVENTO\s*:", linha, re.IGNORECASE):
            if atual:
                eventos.append(atual)
            atual = [linha]
        else:
            if atual:
                atual.append(linha)

    if atual:
        eventos.append(atual)

    return eventos


def interpretar_evento_1epm(bloco: list[str]):
    """
    Interpreta um bloco de evento do 1º EPM.
    """

    evento = {
        "evento": "",
        "local": "",
        "ref": "",
        "turno": "",
        "efetivo": 0,
        "semovente": 0,
        "viaturas": [],
        "responsavel": "",
        "telefone": "Não informado"
    }

    evento["evento"] = extrair_nome_evento_1epm(bloco)
    evento["local"] = extrair_local_evento_1epm(bloco)
    evento["ref"] = extrair_ref_evento_1epm(bloco)
    evento["turno"] = extrair_turno_evento_1epm(bloco)
    evento["viaturas"] = extrair_viaturas_evento_1epm(bloco)

    dados_efetivo = extrair_efetivo_evento_1epm(bloco)
    evento["efetivo"] = dados_efetivo["efetivo"]
    evento["semovente"] = dados_efetivo["semovente"]
    evento["responsavel"] = dados_efetivo["responsavel"]
    evento["telefone"] = dados_efetivo["telefone"]

    return evento


def extrair_nome_evento_1epm(bloco: list[str]) -> str:
    """
    Extrai apenas o nome do evento.

    Regras:
    - Começa depois de EVENTO:
    - Para em LOCAL:, REF:, No RPMon:, No local:, Horário:, PATRULHAMENTO,
      MOTORISTAS, EQUINOS RESERVAS, linha de policial, Instrutor ou texto descritivo.
    - Isso evita que eventos sem LOCAL engulam o restante do boletim.
    """

    partes = []
    capturando = False

    padrao_linha_policial = re.compile(
        r"^\s*\d+\s+"
        r"(?:\d+[º°]?\s*)?"
        r"(?:Ten\.?|Sgt\.?|Cap\.?|Maj\.?|Cel\.?|Cb\.?|Sd\.?)\s+"
        r"(?:QP|QOEM|QOE)?\s*PM\b",
        re.IGNORECASE
    )

    def eh_fim_nome_evento(linha: str) -> bool:
        linha = normalizar_linha(linha)
        up = linha.upper()

        if not linha:
            return True

        # Marcadores fortes
        if re.search(r"\bLOCAL\s*:", linha, re.IGNORECASE):
            return True

        if re.search(r"\bREF\s*:", linha, re.IGNORECASE):
            return True

        if re.search(r"\bNO\s+RPMON\s*:", linha, re.IGNORECASE):
            return True

        if re.search(r"\bNO\s+LOCAL\s*:", linha, re.IGNORECASE):
            return True

        if re.search(r"\bHOR[ÁA]RIO\s*:", linha, re.IGNORECASE):
            return True

        # Seções/tabelas
        if up.startswith("PATRULHAMENTO"):
            return True

        if up.startswith("MOTORISTAS"):
            return True

        if up.startswith("EQUINOS RESERVAS"):
            return True

        if up.startswith("Nº ") or up.startswith("N° "):
            return True

        # Linha de policial numerada
        if padrao_linha_policial.search(linha):
            return True

        # Descrição textual que não é nome do evento
        if up.startswith("REALIZAR "):
            return True

        if up.startswith("APÓS ") or up.startswith("APOS "):
            return True

        if up.startswith("VISANDO "):
            return True

        if up.startswith("CONSIDERANDO "):
            return True

        if up.startswith("INSTRUTOR"):
            return True

        if up.startswith("OBS:"):
            return True

        # Assinatura
        if "COMANDANTE DO 1º EPM" in up or "COMANDANTE DO 1° EPM" in up:
            return True

        return False

    for linha in bloco:
        linha_limpa = normalizar_linha(linha)

        m_evento = re.search(r"\bEVENTO\s*:\s*(.*)", linha_limpa, re.IGNORECASE)

        if m_evento:
            capturando = True
            texto = m_evento.group(1).strip()

            # Se EVENTO e algum marcador estiverem na mesma linha
            for marcador in [
                r"\bLOCAL\s*:",
                r"\bREF\s*:",
                r"\bNO\s+RPMON\s*:",
                r"\bNO\s+LOCAL\s*:",
                r"\bHOR[ÁA]RIO\s*:"
            ]:
                if re.search(marcador, texto, re.IGNORECASE):
                    texto = re.split(
                        marcador,
                        texto,
                        maxsplit=1,
                        flags=re.IGNORECASE
                    )[0].strip()
                    break

            if texto:
                partes.append(texto)

            continue

        if capturando:
            if eh_fim_nome_evento(linha_limpa):
                break

            partes.append(linha_limpa)

    return limpar_texto_1epm(" ".join(partes))


def extrair_local_evento_1epm(bloco: list[str]) -> str:
    """
    Extrai o LOCAL real.

    Regra:
    - Se tiver LOCAL:, pega o texto depois dele.
    - Junta a próxima linha apenas se parecer continuação de endereço.
    - Para antes de REF:, descrição longa ou tabelas.
    - Se não tiver LOCAL:, retorna vazio.
    """

    partes = []

    termos_inicio_descricao = (
        "Visando",
        "Considerando",
        "A 52ª",
        "A 52a",
        "Evento é",
        "Polícia Militar",
        "Os ajustes",
        "Manter contato",
        "Os equinos",
        "Obs:",
        "PATRULHAMENTO",
        "MOTORISTAS",
        "EQUINOS RESERVAS",
        "No Rpmon",
        "No local",
        "Instrutor",
        "Realizar",
        "Após",
        "Apos"
    )

    termos_continuacao_local = (
        "Rua",
        "R.",
        "Av.",
        "Avenida",
        "Bairro",
        "Curitiba",
        "Maringá",
        "Maringa",
        "PR",
        "Paraná",
        "Parana",
        "Vila",
        "Parque",
        "Sociedade",
        "Colombo",
        "Morangueira",
        "Francisco",
        "Ribeiro",
        "2186",
        "Haras"
    )

    for i, linha in enumerate(bloco):
        m_local = re.search(r"\bLOCAL\s*:\s*(.*)", linha, re.IGNORECASE)
        if not m_local:
            continue

        texto_local = m_local.group(1).strip()

        if re.search(r"\bREF\s*:", texto_local, re.IGNORECASE):
            texto_local = re.split(
                r"\bREF\s*:",
                texto_local,
                maxsplit=1,
                flags=re.IGNORECASE
            )[0].strip()

        if texto_local:
            partes.append(texto_local)

        j = i + 1

        while j < len(bloco):
            prox = normalizar_linha(bloco[j])

            if not prox:
                break

            if re.search(r"\bREF\s*:", prox, re.IGNORECASE):
                break

            if prox.startswith(termos_inicio_descricao):
                break

            if re.search(r"\bEVENTO\s*:", prox, re.IGNORECASE):
                break

            # Junta só se parecer endereço/local
            if any(t in prox for t in termos_continuacao_local):
                partes.append(prox)
                j += 1
                continue

            break

        return limpar_texto_1epm(" ".join(partes))

    return ""


def extrair_ref_evento_1epm(bloco: list[str]) -> str:
    """
    Procura REF: em qualquer lugar do bloco.
    Aceita variações como:
    REF: O.S . n° 219/ 2026
    REF: O,S . n° 226/ 2026
    REF: N,I. n° 031/ 2026
    """

    for linha in bloco:
        m_ref = re.search(r"\bREF\s*:\s*(.*)", linha, re.IGNORECASE)
        if m_ref:
            ref = m_ref.group(1).strip()
            return normalizar_ref_1epm(ref)

    return ""


def extrair_turno_evento_1epm(bloco: list[str]) -> str:
    """
    Extrai turno.

    Regras:
    1. Se tiver 'No local:', usa essa linha.
    2. Se tiver 'No RPMon:', usa essa linha.
    3. Para eventos de deslocamento, usa Saída/Chegada.
    """

    for linha in bloco:
        m = re.search(r"No local\s*:\s*(.*)", linha, re.IGNORECASE)
        if m:
            return limpar_texto_1epm(m.group(1).strip())

    for linha in bloco:
        m = re.search(r"No RPMon\s*:\s*(.*)", linha, re.IGNORECASE)
        if m:
            return limpar_texto_1epm(m.group(1).strip())

    saida_curitiba = ""
    chegada_destino = ""
    saida_destino = ""
    chegada_curitiba = ""

    for linha in bloco:
        if re.search(r"Sa[ií]da de Curitiba\s*:", linha, re.IGNORECASE):
            saida_curitiba = linha.strip()

        elif re.search(r"Chegada em Maring[aá]", linha, re.IGNORECASE):
            chegada_destino = linha.strip()

        elif re.search(r"Sa[ií]da de Maring[aá]", linha, re.IGNORECASE):
            saida_destino = linha.strip()

        elif re.search(r"Chegada em Curitiba\s*:", linha, re.IGNORECASE):
            chegada_curitiba = linha.strip()

    partes = []

    if saida_curitiba:
        partes.append(saida_curitiba)
    if chegada_destino:
        partes.append(chegada_destino)
    if saida_destino:
        partes.append(saida_destino)
    if chegada_curitiba:
        partes.append(chegada_curitiba)

    if partes:
        return limpar_texto_1epm(" | ".join(partes))

    return ""


def extrair_viaturas_evento_1epm(bloco: list[str]) -> list[str]:
    """
    Extrai viaturas do bloco.

    Aceita:
    - VTR 16560
    - VTR16560
    - VTR 16561
    - Caminhão 11045
    """

    viaturas = []

    padrao_vtr_texto = re.compile(r"\bVTR\s*([1L]\d{4})\b", re.IGNORECASE)
    padrao_vtr_solta = re.compile(r"(?<!\d)(1\d{4}|L\d{4})(?!\d)", re.IGNORECASE)
    padrao_caminhao = re.compile(r"\bCAMINH[ÃA]O\s+(\d{5})\b", re.IGNORECASE)

    for linha in bloco:
        for m in padrao_vtr_texto.findall(linha):
            vtr = m.upper()
            if vtr not in viaturas:
                viaturas.append(vtr)

        for m in padrao_caminhao.findall(linha):
            vtr = m.upper()
            if vtr not in viaturas:
                viaturas.append(vtr)

        # Evita capturar RG como viatura:
        # só aceita número solto 1xxxx/Lxxxx se a linha tiver VTR, caminhão ou motorista.
        if re.search(r"\b(VTR|CAMINH[ÃA]O|MOTORISTA)\b", linha, re.IGNORECASE):
            for m in padrao_vtr_solta.findall(linha):
                vtr = m.upper()
                if vtr not in viaturas:
                    viaturas.append(vtr)

    return viaturas


def extrair_efetivo_evento_1epm(bloco: list[str]) -> dict:
    """
    Extrai:
    - efetivo
    - semovente
    - responsável
    - telefone

    Conta linhas iniciadas por número + posto:
    01 2º Sgt. QP PM ...
    1 Cb. QP PM ...
    7 Sd. QP PM ...
    """

    resultado = {
        "efetivo": 0,
        "semovente": 0,
        "responsavel": "",
        "telefone": "Não informado"
    }

    padrao_policial = re.compile(
        r"^\s*\d+\s+"
        r"(?:\d+[º°]?\s*)?"
        r"(?:Ten\.?|Sgt\.?|Cap\.?|Maj\.?|Cel\.?|Cb\.?|Sd\.?)\s+"
        r"(?:QP|QOEM|QOE)?\s*PM\b",
        re.IGNORECASE
    )

    padrao_tel = re.compile(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}")
    padrao_rg_numerico = re.compile(r"\b\d{7,10}\b")
    padrao_rg_pontuado = re.compile(r"\b\d{1,2}\.\d{3}\.\d{3}-\d\b")

    padrao_equino_nome = re.compile(
        r"\b[A-Za-zÀ-ÿ]{2,}(?:\s+[A-Za-zÀ-ÿ]{2,})?\s+n[º°]\s*\d+\b",
        re.IGNORECASE
    )

    padrao_equino_numero = re.compile(r"\bn[º°]\s*\d+\b", re.IGNORECASE)

    for linha in bloco:
        linha_limpa = normalizar_linha(linha)

        if not padrao_policial.search(linha_limpa):
            continue

        resultado["efetivo"] += 1

        # Semovente só conta se houver equino na linha do policial
        if padrao_equino_nome.search(linha_limpa) or padrao_equino_numero.search(linha_limpa):
            resultado["semovente"] += 1

        if not resultado["responsavel"]:
            resultado["responsavel"] = limpar_responsavel_1epm(
                linha_limpa,
                padrao_tel,
                padrao_rg_numerico,
                padrao_rg_pontuado
            )

            tel = padrao_tel.search(linha_limpa)
            if tel:
                resultado["telefone"] = tel.group()

    return resultado


def limpar_responsavel_1epm(
    linha: str,
    padrao_tel,
    padrao_rg_numerico,
    padrao_rg_pontuado
) -> str:
    """
    Limpa a linha do policial responsável.
    Mantém sua regra atual: remove QP PM, QOEM PM e QOE PM.
    """

    resp = normalizar_linha(linha)

    # Remove número inicial
    resp = re.sub(r"^\s*\d+\s+", "", resp)

    # Remove telefone
    resp = padrao_tel.sub("", resp)

    # Remove RG
    resp = padrao_rg_pontuado.sub("", resp)
    resp = padrao_rg_numerico.sub("", resp)
    resp = re.sub(r"\bRG\b\s*:?", "", resp, flags=re.IGNORECASE)

    # Remove tudo após barra usada antes do RG
    resp = resp.split("/", 1)[0].strip()

    # Remove VTR
    resp = re.sub(r"\(?\bVTR\s*\d{5}\b\)?", "", resp, flags=re.IGNORECASE)

    # Remove caminhão
    resp = re.sub(r"\bCAMINH[ÃA]O\s+\d{5}\b", "", resp, flags=re.IGNORECASE)

    # Remove observações entre parênteses, como TASER
    resp = re.sub(r"\([^)]*\)", "", resp)

    # Remove equinos
    resp = re.sub(
        r"\b[A-Za-zÀ-ÿ]{2,}(?:\s+[A-Za-zÀ-ÿ]{2,})?\s+n[º°]\s*\d+\b",
        "",
        resp,
        flags=re.IGNORECASE
    )
    resp = re.sub(r"\bn[º°]\s*\d+\b", "", resp, flags=re.IGNORECASE)

    # Remove QP/QOEM/QOE PM
    resp = resp.replace(" QP PM", "")
    resp = resp.replace(" QOEM PM", "")
    resp = resp.replace(" QOE PM", "")

    resp = re.sub(r"\s+", " ", resp).strip()

    return resp


def normalizar_ref_1epm(ref: str) -> str:
    """
    Normaliza referência.
    """

    ref = ref or ""
    ref = ref.strip()

    ref = ref.replace("O,S", "O.S")
    ref = ref.replace("O.S .", "O.S.")
    ref = ref.replace("O. S.", "O.S.")
    ref = ref.replace("N,I", "N.I")
    ref = ref.replace("N.I .", "N.I.")
    ref = ref.replace("N. I.", "N.I.")
    ref = ref.replace("n°", "nº")
    ref = ref.replace("N°", "nº")

    ref = re.sub(r"\s+", " ", ref)
    ref = ref.replace("/ ", "/")
    ref = ref.replace(" /", "/")

    return ref.strip()


def limpar_texto_1epm(texto: str) -> str:
    """
    Limpeza geral de texto.
    """

    texto = texto or ""
    texto = texto.replace(" ,", ",")
    texto = texto.replace(" .", ".")
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()
# ============================================================
# CORP / 4º EPM - MODELO NOVO DO BOLETIM DIÁRIO
# ============================================================

def ajustar_turno(turno: str) -> str:
    turno = (turno or "").replace("ás", "às")
    if "06h45" in turno and "12h30" in turno:
        return "6h às 12h45"
    if "15h45" in turno and "21h30" in turno:
        return "15h às 21h45"
    return turno.strip()


def extrair_corp(caminho_pdf: str):
    """
    Nova lógica da CORP.

    Padrão atual do B.I:
    CORP
    ESCALA
    DIA ...
    ADMINISTRATIVO
    ...
    EFETIVO OPERACIONAL
    UNIFORME ...
    LOCAL DE APRESENTAÇÃO ...
    HORÁRIOS - Horário no local: ...
    OBSERVAÇÕES
    ...
    MILITARES
    GRAD NOME CPF TELEFONE
    CAP. QOEM PM ...
    ...
    Assinado no original.
    Comandante da CORP.
    EXTRAJORNADA

    Retorna uma lista com 1 evento da CORP.
    """

    bloco = extrair_bloco_corp_diario(caminho_pdf)

    if not bloco:
        return []

    evento = interpretar_corp_diario(bloco)

    # Evita retornar CORP vazia
    if evento["efetivo"] == 0 and not evento["turno"] and not evento["responsavel"]:
        return []

    return [evento]


def extrair_bloco_corp_diario(caminho_pdf: str):
    """
    Extrai o bloco da CORP diária.

    Começa na linha exata 'CORP'.
    Termina antes de EXTRAJORNADA, EXTRA JORNADA ou 2ª PARTE.
    """

    linhas_corp = []
    dentro_corp = False

    with pdfplumber.open(caminho_pdf) as pdf:
        for pagina in pdf.pages:
            texto = pagina.extract_text() or ""
            if not texto.strip():
                continue

            for linha in texto.split("\n"):
                linha_limpa = normalizar_linha(linha)
                if not linha_limpa:
                    continue

                up = linha_limpa.upper()

                # Início da CORP diária
                # Evita confundir com "EXTRAJORNADA CORP"
                if not dentro_corp:
                    if up == "CORP":
                        dentro_corp = True
                    continue

                # Fim da CORP diária
                if (
                    up.startswith("EXTRAJORNADA")
                    or up.startswith("EXTRA JORNADA")
                    or re.search(r"\b2[ªA]?\s*PARTE\b", up)
                    or re.search(r"\b3[ªA]?\s*PARTE\b", up)
                ):
                    return linhas_corp

                linhas_corp.append(linha_limpa)

    return linhas_corp


def interpretar_corp_diario(bloco: list[str]):
    """
    Interpreta o bloco completo da CORP diária.
    """

    evento = {
        "evento": extrair_evento_corp_diario(bloco),
        "local": extrair_local_corp_diario(bloco),
        "turno": extrair_turno_corp_diario(bloco),
        "viaturas": extrair_viaturas_corp_diario(bloco),
        "efetivo": 0,
        "responsavel": "",
        "telefone": "Não informado"
    }

    dados_efetivo = extrair_efetivo_corp_diario(bloco)

    evento["efetivo"] = dados_efetivo["efetivo"]
    evento["responsavel"] = dados_efetivo["responsavel"]
    evento["telefone"] = dados_efetivo["telefone"]

    return evento


def extrair_evento_corp_diario(bloco: list[str]) -> str:
    """
    Define o nome do evento da CORP.

    Prioridade:
    1. Se houver '-Tema:', usa o tema.
    2. Se houver 'RESPONSÁVEL:', usa como referência.
    3. Senão, usa 'CORP - Efetivo Operacional'.
    """

    tema = ""
    responsavel_observacao = ""

    for linha in bloco:
        m_tema = re.search(r"^-?\s*Tema\s*:\s*(.+)$", linha, re.IGNORECASE)
        if m_tema:
            tema = m_tema.group(1).strip().rstrip(";.")
            break

        m_resp = re.search(r"^-?\s*RESPONS[ÁA]VEL\s*:\s*(.+)$", linha, re.IGNORECASE)
        if m_resp:
            responsavel_observacao = m_resp.group(1).strip().rstrip(";.")

    if tema:
        return f"CORP - {limpar_texto_corp(tema)}"

    if responsavel_observacao:
        return f"CORP - {limpar_texto_corp(responsavel_observacao)}"

    return "CORP - Efetivo Operacional"


def extrair_local_corp_diario(bloco: list[str]) -> str:
    """
    Extrai LOCAL DE APRESENTAÇÃO.

    Junta a linha seguinte quando o endereço continua.
    Para ao encontrar HORÁRIOS, OBSERVAÇÕES, LEGENDA ou MILITARES.
    """

    partes = []
    capturando = False

    for linha in bloco:
        up = linha.upper()

        if re.search(r"\bLOCAL\s+DE\s+APRESENTA[ÇC][ÃA]O\b", linha, re.IGNORECASE):
            capturando = True

            texto = re.sub(
                r"^.*?\bLOCAL\s+DE\s+APRESENTA[ÇC][ÃA]O\b",
                "",
                linha,
                flags=re.IGNORECASE
            ).strip(" :-")

            if texto:
                partes.append(texto)

            continue

        if capturando:
            if (
                up.startswith("HORÁRIOS")
                or up.startswith("HORARIOS")
                or up.startswith("OBSERVAÇÕES")
                or up.startswith("OBSERVACOES")
                or up.startswith("LEGENDA")
                or up.startswith("MILITARES")
                or up.startswith("UNIFORME")
            ):
                break

            partes.append(linha.strip())

    return limpar_texto_corp(" ".join(partes))


def extrair_turno_corp_diario(bloco: list[str]) -> str:
    """
    Extrai o horário da CORP.

    Padrão:
    HORÁRIOS - Horário no local: 8h45min ao término

    Também aceita:
    - Horário no local: ...
    - No RPMon: ...
    - Saída do RPMon: ...
    - Término no RPMon: ...
    """

    # Regra principal: Horário no local
    for linha in bloco:
        m = re.search(r"Hor[áa]rio\s+no\s+local\s*:\s*(.+)$", linha, re.IGNORECASE)
        if m:
            turno = m.group(1).strip()
            return ajustar_turno(limpar_texto_corp(turno))

    # Regra secundária: linhas de horário separadas
    partes = []

    for linha in bloco:
        if re.search(r"\bNo\s+RPMon\s*:", linha, re.IGNORECASE):
            partes.append(linha.strip())

        elif re.search(r"\bSa[ií]da\s+do\s+RPMon\s*:", linha, re.IGNORECASE):
            partes.append(linha.strip())

        elif re.search(r"\bT[ée]rmino\s+no\s+RPMon\s*:", linha, re.IGNORECASE):
            partes.append(linha.strip())

    if partes:
        return limpar_texto_corp(" | ".join(partes))

    return ""


def extrair_viaturas_corp_diario(bloco: list[str]) -> list[str]:
    """
    Extrai VTRs caso existam no bloco.

    No modelo novo da CORP diária, normalmente não há VTR.
    Mesmo assim, mantém compatibilidade com outros boletins.
    """

    viaturas = []

    padrao_vtr_texto = re.compile(r"\bVTR\s*([1L]\d{4})\b", re.IGNORECASE)
    padrao_vtr_solta = re.compile(r"(?<!\d)(1\d{4}|L\d{4})(?!\d)", re.IGNORECASE)

    for linha in bloco:
        # Busca VTR 16535 / VTR16535
        for m in padrao_vtr_texto.findall(linha):
            vtr = m.upper()
            if vtr not in viaturas:
                viaturas.append(vtr)

        # Só busca número solto se a linha tiver indicação de viatura
        if re.search(r"\b(VTR|VIATURA)\b", linha, re.IGNORECASE):
            for m in padrao_vtr_solta.findall(linha):
                vtr = m.upper()
                if vtr not in viaturas:
                    viaturas.append(vtr)

    return viaturas


def extrair_efetivo_corp_diario(bloco: list[str]) -> dict:
    """
    Conta o efetivo somente depois de MILITARES.

    Padrão das linhas:
    CAP. QOEM PM Daniel Gonçalves Conde XXX.464.299-XX (41) 99981-4788
    1°TEN. QOEM PM Juliano Mazza Borges XXX.525.909-XX (41)99781-5018
    2°SGT. QP PM Luciano de Oliveira Franco XXX.002.139-XX (41)99661-0456
    Sd. QP PM Eduarda Estigara XXX.075.969-XX (41) 99162-5537
    """

    resultado = {
        "efetivo": 0,
        "responsavel": "",
        "telefone": "Não informado"
    }

    dentro_militares = False

    padrao_tel = re.compile(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}")
    padrao_cpf_mascarado = re.compile(r"\bXXX\.\d{3}\.\d{3}-XX\b", re.IGNORECASE)
    padrao_cpf_generico = re.compile(r"\bXXX[.\d-]+XX\b", re.IGNORECASE)

    padrao_militar = re.compile(
        r"^\s*"
        r"(?:(?:\d+)[º°]?\s*)?"
        r"(?:TEN\.?|TENENTE|SGT\.?|SARGENTO|CAP\.?|CAPITAO|CAPITÃO|MAJ\.?|MAJOR|CEL\.?|CORONEL|CB\.?|SD\.?|SUBTENENTE|SUBTEN\.?)"
        r"\s*"
        r"(?:QP|QOEM|QOE)?\s*PM\b",
        re.IGNORECASE
    )

    for linha in bloco:
        linha_limpa = normalizar_linha(linha)
        up = linha_limpa.upper()

        # Começa contagem só depois de MILITARES
        if up == "MILITARES":
            dentro_militares = True
            continue

        if not dentro_militares:
            continue

        # Ignora cabeçalho
        if re.search(r"\bGRAD\b.*\bNOME\b.*\bCPF\b.*\bTELEFONE\b", linha_limpa, re.IGNORECASE):
            continue

        # Fim da tabela de militares
        if (
            up.startswith("ASSINADO")
            or "COMANDANTE DA CORP" in up
            or up.startswith("EXTRAJORNADA")
            or up.startswith("EXTRA JORNADA")
            or re.search(r"\b2[ªA]?\s*PARTE\b", up)
            or re.search(r"\b3[ªA]?\s*PARTE\b", up)
        ):
            break

        if not padrao_militar.search(linha_limpa):
            continue

        resultado["efetivo"] += 1

        if not resultado["responsavel"]:
            resultado["responsavel"] = limpar_responsavel_corp_diario(
                linha_limpa,
                padrao_tel,
                padrao_cpf_mascarado,
                padrao_cpf_generico
            )

            tel = padrao_tel.search(linha_limpa)
            if tel:
                resultado["telefone"] = tel.group()

    return resultado


def limpar_responsavel_corp_diario(
    linha: str,
    padrao_tel,
    padrao_cpf_mascarado,
    padrao_cpf_generico
) -> str:
    """
    Limpa a linha do primeiro militar da tabela MILITARES.
    Mantém graduação, remove quadro, CPF e telefone.

    Exemplo:
    CAP. QOEM PM Daniel Gonçalves Conde XXX.464.299-XX (41) 99981-4788

    Saída:
    CAP. Daniel Gonçalves Conde
    """

    resp = normalizar_linha(linha)

    # Remove telefone
    resp = padrao_tel.sub("", resp)

    # Remove CPF mascarado
    resp = padrao_cpf_mascarado.sub("", resp)
    resp = padrao_cpf_generico.sub("", resp)

    # Remove QP/QOEM/QOE PM
    resp = re.sub(r"\b(QP|QOEM|QOE)\s*PM\b", "", resp, flags=re.IGNORECASE)

    # Normaliza graduações grudadas: 1°TEN. -> 1° Ten.
    resp = re.sub(r"(\d+)[º°]\s*TEN\.?", r"\1º Ten.", resp, flags=re.IGNORECASE)
    resp = re.sub(r"(\d+)[º°]\s*SGT\.?", r"\1º Sgt.", resp, flags=re.IGNORECASE)

    # Ajusta caixa dos postos mais comuns
    resp = re.sub(r"\bCAP\.\b", "Cap.", resp, flags=re.IGNORECASE)
    resp = re.sub(r"\bCB\.\b", "Cb.", resp, flags=re.IGNORECASE)
    resp = re.sub(r"\bSD\.\b", "Sd.", resp, flags=re.IGNORECASE)
    resp = re.sub(r"\bSUBTENENTE\b", "Subtenente", resp, flags=re.IGNORECASE)
    resp = re.sub(r"\bSUBTEN\.\b", "Subten.", resp, flags=re.IGNORECASE)

    resp = re.sub(r"\s+", " ", resp).strip()

    return resp


def limpar_texto_corp(texto: str) -> str:
    """
    Limpeza geral de texto da CORP.
    """

    texto = texto or ""
    texto = texto.replace(" ,", ",")
    texto = texto.replace(" .", ".")
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()

# ============================================================
# LANCEIROS (ESCALA LANCEIRO)
# ============================================================

def _extrair_horarios_em_ordem(texto: str):
    """
    Extrai horários no formato 6H33, 6h33min, 06h33, 6h, etc.
    Retorna lista de minutos desde 00:00 na ordem em que aparecem.
    """
    padrao_horas = re.compile(r"\b(\d{1,2})\s*[Hh]\s*(\d{2})?\s*(?:min)?\b")
    horarios = []
    for h, m in padrao_horas.findall(texto or ""):
        hh = int(h)
        mm = int(m) if m else 0
        horarios.append(hh * 60 + mm)
    return horarios

def _fmt_hora(mins: int) -> str:
    hh = mins // 60
    mm = mins % 60
    return f"{hh}h" if mm == 0 else f"{hh}h{mm:02d}"

def _turno_por_primeiro_e_ultimo(texto_horario: str) -> str:
    hs = _extrair_horarios_em_ordem(texto_horario)
    if len(hs) < 2:
        return ""
    return f"{_fmt_hora(hs[0])} às {_fmt_hora(hs[-1])}"

def extrair_lanceiro_escala(caminho_pdf: str):
    """
    Identifica blocos "ESCALA LANCEIRO(S)" e extrai:
    - evento: linhas subsequentes (ignorando a linha "LANCEIROS") até encontrar "DATA:"
    - data: valor após "DATA:"
    - horario_raw: concatena linhas do campo "HORÁRIO:" (pode quebrar linha)
    - turno: PRIMEIRO horário e ÚLTIMO horário encontrados em horario_raw (na ordem do texto)
    - viaturas: mesma lógica do extrair_corp() -> (1\\d{4}|L\\d{4}) deduplicado
    - efetivo: começa a contar após cabeçalho da tabela e só para ao encontrar ASSINATURA
    - responsavel/telefone: escolhe o mais antigo disponível na tabela (Ten/Sgt/Cb/Sd)
    """
    eventos = []
    dentro = False
    bloco = None
    evento_linhas = []
    capturando = None  # "horario" ou "local"
    dentro_tabela = False
    pendente = None
    ordem_polic = 0

    padrao_inicio = re.compile(r"\bESCALA\b.*\bLANCEIR(?:O|OS)\b", re.IGNORECASE)
    padrao_data = re.compile(r"^\s*DATA\s*:\s*(.*)$", re.IGNORECASE)
    padrao_horario = re.compile(r"^\s*HOR[ÁA]RIO\s*:\s*(.*)$", re.IGNORECASE)
    padrao_local = re.compile(r"^\s*LOCAL\s*:\s*(.*)$", re.IGNORECASE)

    # header de página / linhas que não podem encerrar contagem
    padrao_header_pagina = re.compile(
        r"^\s*Boletim\s+Interno\b|\bRegimento\s+de\s+Pol[íi]cia\b|^\s*fl\.\s*\d+",
        re.IGNORECASE
    )

    # assinatura / encerramento de seção (ao encontrar, fecha o bloco)
    padrao_assinatura = re.compile(
        r"^\s*(ASSINA|CONFERE)\s*:|"
        r"\bASSINAD[OA]\b|"
        r"\b(COMANDANTE|SUBCOMANDANTE|CHEFE)\b",
        re.IGNORECASE
    )

    # também encerra se começar outra parte/seção
    padrao_nova_secao = re.compile(
        r"\b(EXTRA\s*JORNADA|EXTRAJORNADA|DEAEV|2[ªa]?\s*PARTE|3[ªa]?\s*PARTE|4[ªa]?\s*PARTE|"
        r"ESCALA\s+DE\s+SERVI[ÇC]O\s+EXTRA|ESCALA\s+DE\s+SERVI[ÇC]O\s+PARA\s+O\s+DIA)\b",
        re.IGNORECASE
    )

    # VTRs (mesma lógica do extrair_corp)
    padrao_vtr = re.compile(r"(?<!\d)(1\d{4}|L\d{4})(?!\d)", re.IGNORECASE)

    # tabela
    padrao_cab_tabela = re.compile(r"\b(N[º°]|N°)\b.*\b(POSTO/GRAD|GRAD)\b.*\bNOME\b", re.IGNORECASE)

    padrao_tel = re.compile(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}")
    padrao_rg_numerico = re.compile(r"\b\d{7,10}\b")
    padrao_rg_pontuado = re.compile(r"\b\d{1,2}\.\d{3}\.\d{3}-\d\b")

    # linha de policial (somente essas graduações entram na contagem)
    padrao_posto_grad = re.compile(
        r"\b(?:(\d+)[º°]?\s*)?(Ten\.?|Sgt\.?|Cb\.?|Sd\.?)\s+(?:QP|QOEM)\s+PM\b",
        re.IGNORECASE
    )

    ordem_base = {"ten": 4, "sgt": 5, "cb": 6, "sd": 7}

    def _tem_rg_ou_tel(s: str) -> bool:
        return bool(padrao_tel.search(s) or padrao_rg_numerico.search(s) or padrao_rg_pontuado.search(s))

    def _peso_antiguidade(posto_grad_str: str):
        s = (posto_grad_str or "").lower()
        m = re.search(r"(?:(\d+)[º°])?\s*(ten|sgt|cb|sd)", s)
        if not m:
            return 9999
        n = int(m.group(1)) if m.group(1) else 9
        base = ordem_base.get(m.group(2), 999)
        return base * 100 + n

    def _extrair_posto_grad_e_nome(linha: str):
        m = padrao_posto_grad.search(linha)
        if not m:
            return None, None
        num = m.group(1)
        sig = m.group(2).strip()
        posto_grad = f"{num}º {sig} QP PM" if num else f"{sig} QP PM"

        resto = linha[m.end():].strip()
        corte = len(resto)
        for mm in [padrao_rg_pontuado.search(resto), padrao_rg_numerico.search(resto), padrao_tel.search(resto)]:
            if mm:
                corte = min(corte, mm.start())
        nome = resto[:corte].strip(" -/|")
        nome = re.sub(r"\s{2,}", " ", nome).strip()
        return posto_grad, nome

    def _novo():
        return {
            "evento": "",
            "data": "",
            "local": "",
            "horario_raw": "",
            "turno": "",
            "viaturas": set(),
            "efetivo": 0,
            "responsavel": "",
            "telefone": "Não informado",
            "_policiais": []
        }

    def _fechar():
        nonlocal bloco, evento_linhas, capturando, dentro_tabela, pendente, ordem_polic, dentro
        if not bloco:
            return

        # calcula turno
        if bloco.get("horario_raw"):
            bloco["turno"] = _turno_por_primeiro_e_ultimo(bloco["horario_raw"])

        # responsável (mais antigo)
        if bloco["_policiais"]:
            bloco["_policiais"].sort(key=lambda x: (x["peso"], x["ordem"]))
            escolhido = bloco["_policiais"][0]
            bloco["responsavel"] = f"{escolhido['posto_grad']} {escolhido['nome']}".strip()
            if escolhido.get("telefone"):
                bloco["telefone"] = escolhido["telefone"]

        bloco["viaturas"] = sorted(list(bloco["viaturas"]))
        bloco.pop("_policiais", None)
        eventos.append(bloco)

        bloco = None
        evento_linhas = []
        capturando = None
        dentro_tabela = False
        pendente = None
        ordem_polic = 0
        dentro = False

    def _linha_eh_label(linha: str) -> bool:
        return bool(re.match(r"^(DATA|HOR[ÁA]RIO|LOCAL|FARDAMENTO|TRANSPORTE)\s*:", linha, re.IGNORECASE))

    with pdfplumber.open(caminho_pdf) as pdf:
        for pagina in pdf.pages:
            texto = pagina.extract_text() or ""
            if not texto.strip():
                continue

            linhas = [normalizar_linha(l) for l in texto.split("\n") if normalizar_linha(l)]
            i = 0
            while i < len(linhas):
                linha = linhas[i]
                up = linha.upper()

                # início
                if padrao_inicio.search(linha):
                    prox = linhas[i + 1] if i + 1 < len(linhas) else ""
                    if (not prox) or prox.upper().startswith("DATA"):
                        i += 1
                        continue

                    # fecha bloco anterior, se estiver aberto
                    if bloco:
                        if pendente:
                            bloco["efetivo"] += 1
                            bloco["_policiais"].append(pendente)
                            pendente = None
                        _fechar()

                    dentro = True
                    bloco = _novo()
                    evento_linhas = []
                    capturando = None
                    dentro_tabela = False
                    pendente = None
                    ordem_polic = 0
                    i += 1
                    continue

                if not dentro or not bloco:
                    i += 1
                    continue

                # ignora cabeçalho de página (não interfere na contagem)
                if padrao_header_pagina.search(linha):
                    i += 1
                    continue

                # se aparecer nova seção depois do lanceiro, fecha (proteção)
                if bloco.get("evento") and (not dentro_tabela) and padrao_nova_secao.search(linha):
                    if pendente:
                        bloco["efetivo"] += 1
                        bloco["_policiais"].append(pendente)
                        pendente = None
                    _fechar()
                    i += 1
                    continue

                # evento até DATA
                mdata = padrao_data.search(linha)
                if mdata and not bloco["evento"]:
                    bloco["data"] = mdata.group(1).strip()
                    bloco["evento"] = " ".join(evento_linhas).strip()
                    i += 1
                    continue
                elif not bloco["evento"]:
                    if up in {"LANCEIRO"}:
                        i += 1
                        continue
                    evento_linhas.append(linha)
                    i += 1
                    continue

                # campos
                mdata2 = padrao_data.search(linha)
                if mdata2:
                    bloco["data"] = mdata2.group(1).strip()
                    capturando = None
                    i += 1
                    continue

                mhor = padrao_horario.search(linha)
                if mhor:
                    bloco["horario_raw"] = (mhor.group(1) or "").strip()
                    capturando = "horario"
                    i += 1
                    continue

                mloc = padrao_local.search(linha)
                if mloc:
                    bloco["local"] = (mloc.group(1) or "").strip()
                    capturando = "local"
                    i += 1
                    continue

                # continuação de horário/local (linhas quebradas)
                if capturando == "horario":
                    if _linha_eh_label(linha):
                        capturando = None
                    else:
                        bloco["horario_raw"] = (bloco["horario_raw"] + " " + linha).strip()
                    i += 1
                    continue

                if capturando == "local":
                    if _linha_eh_label(linha) or up in {"LANCEIROS", "LANCEIRO"} or padrao_cab_tabela.search(linha):
                        capturando = None
                    else:
                        bloco["local"] = (bloco["local"] + " " + linha).strip()
                    i += 1
                    continue

                # VTRs
                for vtr in padrao_vtr.findall(linha):
                    bloco["viaturas"].add(vtr.upper())

                # tabela começa
                if padrao_cab_tabela.search(linha):
                    dentro_tabela = True
                    pendente = None
                    i += 1
                    continue

                # dentro tabela: contar até assinatura
                if dentro_tabela:
                    # encerra tabela e bloco se for assinatura (linha sem posto/rg/tel)
                    if (padrao_assinatura.search(linha) and (not padrao_posto_grad.search(linha)) and (not _tem_rg_ou_tel(linha))) or \
                       (padrao_nova_secao.search(linha) and (not _tem_rg_ou_tel(linha))):
                        if pendente:
                            bloco["efetivo"] += 1
                            bloco["_policiais"].append(pendente)
                            pendente = None
                        dentro_tabela = False
                        _fechar()
                        i += 1
                        continue

                    # linha quebrada (RG/tel na próxima linha)
                    if pendente and (not padrao_posto_grad.search(linha)) and _tem_rg_ou_tel(linha):
                        bloco["efetivo"] += 1
                        mt = padrao_tel.search(linha)
                        if mt and not pendente.get("telefone"):
                            pendente["telefone"] = mt.group()
                        bloco["_policiais"].append(pendente)
                        pendente = None
                        i += 1
                        continue

                    # linha com policial
                    if padrao_posto_grad.search(linha):
                        posto_grad, nome = _extrair_posto_grad_e_nome(linha)
                        if posto_grad and nome:
                            mt = padrao_tel.search(linha)
                            tel = mt.group() if mt else ""
                            ordem_polic += 1
                            polic = {
                                "posto_grad": posto_grad,
                                "nome": nome,
                                "telefone": tel,
                                "peso": _peso_antiguidade(posto_grad),
                                "ordem": ordem_polic
                            }
                            if _tem_rg_ou_tel(linha):
                                bloco["efetivo"] += 1
                                bloco["_policiais"].append(polic)
                                pendente = None
                            else:
                                pendente = polic
                        i += 1
                        continue

                i += 1

    # fecha se terminou o PDF dentro do bloco (sem assinatura encontrada)
    if bloco:
        if pendente:
            bloco["efetivo"] += 1
            bloco["_policiais"].append(pendente)
            pendente = None
        _fechar()

    return eventos

# ============================================================
# EXTRA JORNADA
# ============================================================

def extrair_extrajornada_por_turno(caminho_pdf: str):
    import pdfplumber
    import re

    termos_extra = r"(?:EXTRA\s*[-]?\s*JORNADA|EXTRAJORNADA|DEAEV|EXTRA\s*VOLUNT[ÁA]RIA|EXTRAVOLUNT[ÁA]RIA)"
    re_cab_escala_extra = re.compile(rf"\bESCALA\b.*\b{termos_extra}\b", re.IGNORECASE)
    re_palavra_escala = re.compile(r"\bESCALA\b", re.IGNORECASE)
    re_termo_extra = re.compile(rf"\b{termos_extra}\b", re.IGNORECASE)

    # "Evento:" ou "Evento -"
    re_evento = re.compile(r"\bEVENTO\b\s*[:\-]\s*(.*)$", re.IGNORECASE)
    # "Horário:" ou "Turno:"
    re_horario = re.compile(r"\b(?:HOR[ÁA]RIO|TURNO)\b\s*[:\-]\s*(.*)$", re.IGNORECASE)

    re_2epm = re.compile(r"\b2\s*[ºo°]\s*EPM\b", re.IGNORECASE)
    re_assinatura = re.compile(
        r"^\s*(?:CAP\.?|TEN\.?|TENENTE|MAJ\.?|MAJOR|CEL\.?|CORONEL)\b|"
        r"^\s*(?:CHEFE|COMANDANTE|SUBCOMANDANTE|RESPONDENTE)\b",
        re.IGNORECASE
    )

    re_header_tabela = re.compile(r"\bVTR\b.*\bPOSTO/GRAD\b.*\bNOME\b", re.IGNORECASE)

    re_tel = re.compile(r"\(?\d{2}\)?\s*\d{4,5}-?\d{4}\b")
    re_rg_pont = re.compile(r"\b\d{1,2}\.\d{3}\.\d{3}-\d\b")
    re_rg_num = re.compile(r"\b\d{7,10}\b")

    re_posto = re.compile(
        r"\b(?:(\d+)\s*[º°o]?\s*)?"
        r"(Ten\.?|Sgt\.?|Cb\.?|Sd\.?)\.?"
        r"(?:\s+(?:QP|QOEM))?(?:\s+PM)?\b",
        re.IGNORECASE
    )

    re_equipe_inicio = re.compile(r"^\s*(EQ\.?|EQUIPE|AUXILIAR|\d{1,2})\b", re.IGNORECASE)

    def norm(s: str) -> str:
        s = (s or "").replace("\u00a0", " ").replace("\t", " ")
        s = re.sub(r"\s+", " ", s).strip()
        return s

    def parece_cabecalho_boletim(l: str) -> bool:
        u = l.upper()
        return ("BOLETIM INTERNO" in u) or ("REGIMENTO" in u and "POL" in u) or ("FL." in u)

    def parece_inicio_outra_parte(l: str) -> bool:
        u = l.upper()
        return ("2ª PARTE" in u) or ("2A PARTE" in u) or ("3ª PARTE" in u) or ("3A PARTE" in u)

    def limpar_nome(nome: str) -> str:
        nome = (nome or "").strip().replace("///", " ").replace("|", " ")
        nome = re_rg_pont.sub("", nome)
        nome = re_rg_num.sub("", nome)
        nome = re.sub(r"\s{2,}", " ", nome).strip(" -/|")
        return nome.strip()

    def vtr_valida(v: str) -> bool:
        v = (v or "").upper().strip()
        return bool(re.fullmatch(r"(?:1\d{4}|L\d{4})", v))

    def iniciar_escala(evento_padrao: str = ""):
        return {
            "evento": evento_padrao or "",
            "turno": "",
            "viaturas_set": set(),
            "policiais_set": set(),
            "responsavel": "",
            "telefone": "Não informado",
        }

    def tem_dados(esc) -> bool:
        return bool(esc and (esc["viaturas_set"] or esc["policiais_set"]))

    def fechar_escala(escalas, escala_atual):
        if not escala_atual:
            return None
        if not escala_atual["turno"] and not escala_atual["evento"]:
            return None

        escala_atual["total_viaturas"] = len(escala_atual["viaturas_set"])
        escala_atual["efetivo"] = len(escala_atual["policiais_set"])
        escala_atual["viaturas"] = sorted(escala_atual["viaturas_set"])
        escala_atual["policiais"] = sorted(escala_atual["policiais_set"])
        escala_atual.pop("viaturas_set", None)
        escala_atual.pop("policiais_set", None)
        escalas.append(escala_atual)
        return None

    def extrair_vtr_depois_da_equipe(linha: str):
        if not re_equipe_inicio.search(linha):
            return None
        toks = linha.split()
        if len(toks) < 2:
            return None

        after = toks[1:]
        t0 = after[0].upper()

        m = re.match(r"^L(\d{4})", t0)
        if m:
            v = "L" + m.group(1)
            return v if vtr_valida(v) else None

        if t0 == "L":
            if len(after) >= 2 and re.fullmatch(r"\d{4}", after[1]):
                v = "L" + after[1]
                return v if vtr_valida(v) else None
            return None

        digits = ""
        for tok in after[:6]:
            d = re.match(r"^(\d+)", tok)
            if not d:
                break
            digits += d.group(1)
            if len(digits) >= 5:
                break

        if len(digits) < 5:
            return None

        v = digits[:5]
        return v if vtr_valida(v) else None

    def extrair_vtr_inicio_linha(linha: str):
        toks = linha.strip().split()
        if not toks:
            return None
        t0 = toks[0].upper()

        if t0.startswith("L"):
            m = re.match(r"^L(\d{4})$", t0)
            if m:
                v = "L" + m.group(1)
                return v if vtr_valida(v) else None
            if t0 == "L" and len(toks) >= 2 and re.fullmatch(r"\d{4}", toks[1]):
                v = "L" + toks[1]
                return v if vtr_valida(v) else None
            return None

        digits = re.sub(r"\D", "", t0)
        if len(digits) != 5 or not digits.startswith("1"):
            return None
        return digits if vtr_valida(digits) else None

    def extrair_policiais_da_linha(escala_atual, linha: str):
        matches = list(re_posto.finditer(linha))
        if not matches:
            return

        for i, m in enumerate(matches):
            posto = re.sub(r"\s+", " ", m.group(0)).strip()

            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(linha)
            trecho = linha[start:end].strip()

            mt = re_tel.search(trecho)
            if mt:
                trecho = trecho[:mt.start()].strip()

            trecho = re.sub(r"\bC\.P\.?\b.*$", "", trecho, flags=re.IGNORECASE).strip()
            nome = limpar_nome(trecho)
            nome = re.sub(r"^\b[A-H]\b\s+", "", nome, flags=re.IGNORECASE).strip()

            if not re.search(r"[A-Za-zÀ-ÿ]", nome):
                continue

            chave = f"{posto} {nome}".strip()
            escala_atual["policiais_set"].add(chave)

            if not escala_atual["responsavel"]:
                escala_atual["responsavel"] = chave

    escalas = []
    escala_atual = None
    dentro_bloco_extra = False
    dentro_tabela = False
    linha_prev = ""
    ultimo_evento = ""

    with pdfplumber.open(caminho_pdf) as pdf:
        for page in pdf.pages:
            texto = page.extract_text() or ""
            if not texto.strip():
                continue

            for raw in texto.split("\n"):
                linha = norm(raw)
                if not linha:
                    linha_prev = linha
                    continue

                if parece_cabecalho_boletim(linha):
                    linha_prev = linha
                    continue

                # Fechamentos fortes (mantém sua lógica).
                # Obs: evita fechar “no meio” da tabela.
                if escala_atual and (parece_inicio_outra_parte(linha) or re_2epm.search(linha) or (re_assinatura.search(linha) and not dentro_tabela)):
                    escala_atual = fechar_escala(escalas, escala_atual)
                    dentro_bloco_extra = False
                    dentro_tabela = False
                    linha_prev = linha
                    continue

                # Início do bloco EXTRA JORNADA
                achou_cab = bool(re_cab_escala_extra.search(linha)) or (
                    bool(re_palavra_escala.search(linha_prev)) and bool(re_termo_extra.search(linha))
                )
                if achou_cab:
                    if escala_atual:
                        escala_atual = fechar_escala(escalas, escala_atual)
                    dentro_bloco_extra = True
                    dentro_tabela = False
                    escala_atual = None
                    ultimo_evento = ""
                    linha_prev = linha
                    continue

                if not dentro_bloco_extra:
                    linha_prev = linha
                    continue

                # ========= Detecta “novo turno” por Evento/Horário =========
                me = re_evento.search(linha)
                mh = re_horario.search(linha)

                # Se começar um novo segmento (Evento/Horário) e eu já tenho dados do turno anterior, fecha e abre outro.
                if (me or mh) and escala_atual and escala_atual.get("turno") and tem_dados(escala_atual):
                    escala_atual = fechar_escala(escalas, escala_atual)
                    escala_atual = None
                    dentro_tabela = False

                # garante escala_atual
                if escala_atual is None:
                    escala_atual = iniciar_escala(evento_padrao=ultimo_evento)

                # Evento
                if me:
                    val = (me.group(1) or "").strip()
                    if val:
                        escala_atual["evento"] = val
                        ultimo_evento = val  # carrega p/ próximos turnos do mesmo bloco

                # Horário / Turno
                if mh:
                    val = (mh.group(1) or "").strip()
                    if val:
                        escala_atual["turno"] = val

                # cabeçalho de tabela
                if re_header_tabela.search(linha):
                    dentro_tabela = True
                    linha_prev = linha
                    continue

                # VTR (sempre tenta ambos)
                vtr = extrair_vtr_depois_da_equipe(linha) or extrair_vtr_inicio_linha(linha)
                if vtr:
                    escala_atual["viaturas_set"].add(vtr)

                # Policiais
                extrair_policiais_da_linha(escala_atual, linha)

                # Telefone (primeiro do turno)
                if escala_atual["telefone"] == "Não informado":
                    mt = re_tel.search(linha)
                    if mt:
                        escala_atual["telefone"] = mt.group()

                linha_prev = linha

    if escala_atual:
        escala_atual = fechar_escala(escalas, escala_atual)

    return escalas
  
extrair_extrajornada = extrair_extrajornada_por_turno

def formatar_relatorio_extrajornada(escalas):
    saida = []
    for e in escalas:
        saida.append(
            "👮 EXTRA JORNADA\n"
            f"🔸Turno: {e.get('turno','')}\n"
            f"🔸VTRs: {e.get('total_viaturas',0)}\n"
            f"🔸Efetivo: {e.get('efetivo',0)}\n"
            f"🔸Responsável: {e.get('responsavel','')}\n"
            f"📞Contato: {e.get('telefone','Não informado')}\n"
        )
    return "\n".join(saida)
# ============================================================
# ESCALAS DIVERSAS (TEMPLATE)
# ============================================================

# ============================================================
# ESCALAS DIVERSAS (EXTRAÇÃO + FALLBACK TEMPLATE)
# ============================================================

# ============================================================
# ESCALAS DIVERSAS (EXTRAÇÃO + FALLBACK TEMPLATE)
# ============================================================

def extrair_escalas_diversas(caminho_pdf: str):
    """
    Se encontrar "ESCALAS DIVERSAS":
      1) Procura indícios de semoventes/cavalos e tenta extrair no estilo 1º EPM.
      2) Se não houver indícios, tenta extrair no estilo CORP (horário no local + VTRs + efetivo).
      3) Se nada útil for extraído, mantém flag para imprimir template.
    Fecha o bloco na assinatura do CHEFE P/1 (ou P1) e NÃO contabiliza o oficial assinante.
    """
    eventos = []
    encontrou_diversas = False
    dentro = False
    ev = None

    # padrões
    postos_validos = r"(?:\d+[º°o]?\s*)?(Ten\.?|Tenente|Sgt\.?|Cap\.?|Capit[aã]o|Maj\.?|Cel\.?|Cb\.?|Sd\.?)"
    padrao_tel = re.compile(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}")
    padrao_rg_numerico = re.compile(r"\b\d{7,10}\b")
    padrao_rg_pontuado = re.compile(r"\b\d{1,2}\.\d{3}\.\d{3}-\d\b")
    padrao_vtr = re.compile(r"(?<!\d)(1\d{4}|L\d{4})(?!\d)", re.IGNORECASE)

    # delimitadores
    padrao_fim = re.compile(
        r"\b(EXTRA\s*[-]?\s*JORNADA|EXTRAJORNADA|DEAEV|2[ªa]?\s*PARTE|3[ªa]?\s*PARTE|ASSUNTOS\s+GERAIS|INSTRUÇÃO)\b",
        re.IGNORECASE
    )
    padrao_assinatura = re.compile(r"\bCHEFE\b[\s\S]*?\bP\s*/?\s*1\b|\bP\s*/\s*1\b", re.IGNORECASE)

    # cavalo/semovente
    padrao_cavalo = re.compile(r"\b(SEMOVENTE|SEMOVENTES|EQUIN|EQUINO|EQUINOS|CAVALO|CAVALOS)\b", re.IGNORECASE)

    # horário no local
    padrao_horario_local = re.compile(r"hor[áa]rio\s+no\s+local\s*:\s*(.+)$", re.IGNORECASE)

    # linha de tabela (1º EPM)
    padrao_linha_tabela_1epm = re.compile(rf"^\d+\s+{postos_validos}\b", re.IGNORECASE)

    # oficial que costuma assinar (para desfazer contagem antes do CHEFE P/1)
    padrao_oficial_assinante = re.compile(
        r"^\s*(?:\d+[º°o]?\s*)?(?:1[º°o]?\s*Ten\.?|2[º°o]?\s*Ten\.?|Ten\.?|Tenente|Cap\.?|Capit[aã]o)\b.*",
        re.IGNORECASE
    )

    def tem_rg_ou_tel(l: str) -> bool:
        return bool(padrao_tel.search(l) or padrao_rg_numerico.search(l) or padrao_rg_pontuado.search(l))

    def iniciar_evento(modo: str):
        return {
            "modo": modo,  # "1epm" ou "corp"
            "evento": "",
            "local": "",
            "ref": "",
            "turno": "",
            "viaturas": set(),
            "efetivo": 0,
            "semovente": 0,
            "responsavel": "",
            "telefone": "Não informado",
            # tracking p/ desfazer assinatura
            "_last_count": {"linha": "", "contou": False, "assinante": False, "setou_resp": False, "setou_tel": False},
        }

    def limpar_responsavel(linha: str) -> str:
        resp = (linha or "")
        resp = resp.split("/", 1)[0].strip()
        resp = resp.rstrip("/").strip()
        resp = padrao_tel.sub("", resp)
        resp = padrao_rg_numerico.sub("", resp)
        resp = padrao_rg_pontuado.sub("", resp)
        resp = resp.replace(" QP PM", "").replace(" QOEM PM", "")
        resp = re.sub(r"\s{2,}", " ", resp).strip()
        return resp

    def fechar_evento():
        nonlocal ev
        if not ev:
            return
        ev["viaturas"] = sorted(list(ev["viaturas"]))
        ev.pop("_last_count", None)
        eventos.append(ev)
        ev = None

    def tem_conteudo(e: dict) -> bool:
        return bool(e and (e.get("evento") or e.get("turno") or e.get("viaturas") or e.get("efetivo") or e.get("responsavel")))

    with pdfplumber.open(caminho_pdf) as pdf:
        for pagina in pdf.pages:
            texto = pagina.extract_text() or ""
            if not texto.strip():
                continue

            for linha in texto.split("\n"):
                linha_limpa = normalizar_linha(linha)
                if not linha_limpa:
                    continue

                up = linha_limpa.upper()

                # achou o título
                if re.search(r"\bESCALAS?\s+DIVERSAS?\b", up, re.IGNORECASE):
                    encontrou_diversas = True
                    dentro = True
                    if ev and tem_conteudo(ev):
                        fechar_evento()
                    ev = None
                    continue

                if not dentro:
                    continue

                # fecha por assinatura CHEFE P/1
                if padrao_assinatura.search(linha_limpa):
                    if ev and ev.get("_last_count", {}).get("contou") and ev["_last_count"].get("assinante"):
                        # desfaz 1 do efetivo e limpa resp/tel se vieram do assinante
                        if ev.get("efetivo", 0) > 0:
                            ev["efetivo"] -= 1
                        if ev["_last_count"].get("setou_resp"):
                            ev["responsavel"] = ""
                        if ev["_last_count"].get("setou_tel"):
                            ev["telefone"] = "Não informado"
                    if ev and tem_conteudo(ev):
                        fechar_evento()
                    dentro = False
                    ev = None
                    continue

                # fecha por outros delimitadores gerais
                if padrao_fim.search(linha_limpa):
                    if ev and tem_conteudo(ev):
                        fechar_evento()
                    dentro = False
                    ev = None
                    continue

                # decide/ajusta modo
                if ev is None:
                    modo = "1epm" if padrao_cavalo.search(linha_limpa) else "corp"
                    ev = iniciar_evento(modo=modo)
                else:
                    if ev["modo"] == "corp" and ev["efetivo"] == 0 and padrao_cavalo.search(linha_limpa):
                        ev["modo"] = "1epm"

                # -------------------- modo 1epm --------------------
                if ev["modo"] == "1epm":
                    if linha_limpa.startswith("EVENTO:"):
                        # novo evento dentro de diversas
                        if tem_conteudo(ev):
                            fechar_evento()
                            ev = iniciar_evento(modo="1epm")
                        ev["evento"] = linha_limpa.replace("EVENTO:", "").strip()
                        continue

                    if linha_limpa.startswith("LOCAL:"):
                        ev["local"] = linha_limpa.replace("LOCAL:", "").strip()
                        continue

                    if linha_limpa.upper().startswith("REF"):
                        partes = linha_limpa.split(":", 1)
                        if len(partes) > 1:
                            ev["ref"] = partes[1].strip()
                        continue

                    if "NO LOCAL:" in up and not ev["turno"]:
                        mturno = re.search(r"no\s+local\s*:\s*(.+)$", linha_limpa, re.IGNORECASE)
                        if mturno:
                            ev["turno"] = mturno.group(1).strip()
                        continue

                    mloc = padrao_horario_local.search(linha_limpa)
                    if mloc and not ev["turno"]:
                        ev["turno"] = ajustar_turno(mloc.group(1).strip())
                        continue

                    for vtr in padrao_vtr.findall(linha_limpa):
                        ev["viaturas"].add(vtr.upper())

                    if padrao_linha_tabela_1epm.search(linha_limpa):
                        ev["efetivo"] += 1
                        ev["_last_count"] = {
                            "linha": linha_limpa,
                            "contou": True,
                            "assinante": bool(padrao_oficial_assinante.search(linha_limpa) and not tem_rg_ou_tel(linha_limpa) and not re.match(r"^\d+\s+", linha_limpa)),
                            "setou_resp": False,
                            "setou_tel": False,
                        }

                        if re.search(r"n[º°]\s*\d+", linha_limpa, re.IGNORECASE) or padrao_cavalo.search(linha_limpa):
                            ev["semovente"] += 1

                        if not ev["responsavel"]:
                            ev["responsavel"] = limpar_responsavel(linha_limpa)
                            ev["_last_count"]["setou_resp"] = True
                            tel = padrao_tel.search(linha_limpa)
                            ev["telefone"] = tel.group() if tel else "Não informado"
                            if tel:
                                ev["_last_count"]["setou_tel"] = True

                    if ev["telefone"] == "Não informado":
                        tel2 = padrao_tel.search(linha_limpa)
                        if tel2:
                            ev["telefone"] = tel2.group()

                    continue

                # -------------------- modo corp --------------------
                if ev["modo"] == "corp":
                    mloc = padrao_horario_local.search(linha_limpa)
                    if mloc:
                        ev["turno"] = ajustar_turno(mloc.group(1).strip())

                    if linha_limpa.startswith("EVENTO:") and not ev["evento"]:
                        ev["evento"] = linha_limpa.replace("EVENTO:", "").strip()

                    for vtr in padrao_vtr.findall(linha_limpa):
                        ev["viaturas"].add(vtr.upper())

                    if re.search(rf"\b{postos_validos}\b", linha_limpa, re.IGNORECASE):
                        # evita texto narrativo: exige pelo menos 3 tokens e não começar com "Foi informado..."
                        if len(linha_limpa.split()) >= 3 and not linha_limpa.lower().startswith("foi informado"):
                            ev["efetivo"] += 1
                            ev["_last_count"] = {
                                "linha": linha_limpa,
                                "contou": True,
                                "assinante": bool(padrao_oficial_assinante.search(linha_limpa) and not tem_rg_ou_tel(linha_limpa) and not re.match(r"^\d+\s+", linha_limpa)),
                                "setou_resp": False,
                                "setou_tel": False,
                            }

                            if not ev["responsavel"]:
                                ev["responsavel"] = limpar_responsavel(linha_limpa)
                                ev["_last_count"]["setou_resp"] = True
                                tel = padrao_tel.search(linha_limpa)
                                ev["telefone"] = tel.group() if tel else "Não informado"
                                if tel:
                                    ev["_last_count"]["setou_tel"] = True

                    if ev["telefone"] == "Não informado":
                        tel2 = padrao_tel.search(linha_limpa)
                        if tel2:
                            ev["telefone"] = tel2.group()

                    continue

        # se terminou ainda dentro
        if dentro and ev and tem_conteudo(ev):
            fechar_evento()

    # devolve apenas eventos úteis
    eventos = [e for e in eventos if tem_conteudo(e)]
    if not eventos and encontrou_diversas:
        # sinaliza que existe bloco mas não deu para extrair
        return [{}]
    return eventos

def imprimir_escalas_diversas(caminho_pdf: str) -> bool:
    """
    Imprime ESCALAS DIVERSAS:
    - Se extrair eventos úteis, imprime cada um.
    - Se houver bloco mas não extrair, imprime template.
    """
    eventos = extrair_escalas_diversas(caminho_pdf)
    if not eventos:
        return False

    # caso placeholder -> template
    if len(eventos) == 1 and not any(eventos[0].get(k) for k in ("evento", "turno", "efetivo", "responsavel")):
        print("🚨 ESCALA DIVERSAS - CONFIRA O BOLETIM")
        print("✅Evento: preencher conforme o B.I")
        print("🔸Local: preencher conforme o B.I")
        print("🔸Ref.: preencher conforme o B.I")
        print("🔸Turno: preencher conforme o B.I")
        print("🔸Viatura: preencher conforme o B.I")
        print("🔸Efetivo: preencher conforme o B.I")
        print("🔸Semovente: preencher conforme o B.I")
        print("🔸Responsável: preencher conforme o B.I")
        print("📞Contato: preencher conforme o B.I")
        print()
        return True

    for ev in eventos:
        print("🚨 *ESCALAS DIVERSAS*")
        print(f"✅*Evento:* {ev.get('evento') or 'preencher conforme o B.I'}")
        print(f"🔸*Local:* {ev.get('local') or 'preencher conforme o B.I'}")
        print(f"🔸*Ref.:* {ev.get('ref') or 'preencher conforme o B.I'}")
        print(f"🔸*Turno:* {ev.get('turno') or 'preencher conforme o B.I'}")

        viaturas = ev.get("viaturas") or []
        print(f"🔸*Viatura:* {len(viaturas) if viaturas else 'preencher conforme o B.I'}")

        print(f"🔸*Efetivo:* {ev.get('efetivo') if ev.get('efetivo') else 'preencher conforme o B.I'}")
        print(f"🔸*Semovente:* {ev.get('semovente') if ev.get('semovente') else 'preencher conforme o B.I'}")
        print(f"🔸*Responsável:* {ev.get('responsavel') or 'preencher conforme o B.I'}")
        print(f"📞*Contato:* {ev.get('telefone') or 'preencher conforme o B.I'}")
        print()

    return True


# ============================================================
# DIVISÃO POR DIA (GERAÇÃO DE MINI-PDFs)
# ============================================================

def _detectar_ranges_por_dia(caminho_pdf: str):
    """
    Retorna lista de ranges de páginas (0-based) para cada dia:
      [{"data": "DD/MM/AAAA", "start": i, "end": j}, ...]
    - start: página onde aparece "ESCALA DE SERVIÇO PARA O DIA:"
    - end: página anterior ao próximo dia, ou anterior a "2ª PARTE - INSTRUÇÃO"
    """
    # pega ano do boletim (primeiro 20xx encontrado nas 2 primeiras páginas)
    ano = None
    with pdfplumber.open(caminho_pdf) as pdf:
        for pg in pdf.pages[:2]:
            t = pg.extract_text() or ""
            anos = re.findall(r"\b(20\d{2})\b", t)
            if anos:
                ano = anos[-1]
                break
    ano = ano or "2000"

    MESES = {
        "janeiro": "01", "fevereiro": "02", "março": "03", "marco": "03", "abril": "04",
        "maio": "05", "junho": "06", "julho": "07", "agosto": "08", "setembro": "09",
        "outubro": "10", "novembro": "11", "dezembro": "12"
    }

    padrao_inicio = re.compile(
        r"ESCALA\s+DE\s+SERVI[ÇC]O\s+PARA\s+O\s+DIA\s*:\s*(\d{1,2})\s+de\s+([A-Za-zçÇãõáÁéÉíÍóÓúÚ]+)",
        re.IGNORECASE
    )
    padrao_fim_geral = re.compile(r"2[ªa]?\s*PARTE\s*[–-]\s*INSTRU", re.IGNORECASE)

    inicios = []
    fim_geral_page = None

    with pdfplumber.open(caminho_pdf) as pdf:
        for i, pg in enumerate(pdf.pages):
            txt = pg.extract_text() or ""
            if fim_geral_page is None and padrao_fim_geral.search(txt):
                fim_geral_page = i

            m = padrao_inicio.search(txt)
            if m:
                d = int(m.group(1))
                mes_txt = (m.group(2) or "").strip().lower()
                mes = MESES.get(mes_txt, None)
                data = f"{d:02d}/{mes}/{ano}" if mes else f"{d:02d}/??/{ano}"
                inicios.append((i, data))

    if not inicios:
        return []

    with pdfplumber.open(caminho_pdf) as pdf:
        total_pages = len(pdf.pages)
    fim_geral_page = fim_geral_page if fim_geral_page is not None else total_pages

    ranges = []
    for idx, (start, data) in enumerate(inicios):
        prox_start = inicios[idx + 1][0] if idx + 1 < len(inicios) else fim_geral_page
        end = min(prox_start - 1, fim_geral_page - 1)
        if end >= start:
            ranges.append({"data": data, "start": start, "end": end})

    # mescla ranges contíguos da mesma data
    mesclados = []
    for r in ranges:
        if not mesclados:
            mesclados.append(r)
            continue
        last = mesclados[-1]
        if r["data"] == last["data"] and r["start"] <= last["end"] + 1:
            last["end"] = max(last["end"], r["end"])
        else:
            mesclados.append(r)

    return mesclados

def _exportar_pdf_paginas(src_pdf: str, start0: int, end0: int, out_pdf: str):
    """
    Exporta páginas [start0..end0] (0-based, inclusive) para out_pdf.
    """
    try:
        from pypdf import PdfReader, PdfWriter
    except Exception:
        from PyPDF2 import PdfReader, PdfWriter  # fallback

    reader = PdfReader(src_pdf)
    writer = PdfWriter()
    for i in range(start0, end0 + 1):
        writer.add_page(reader.pages[i])

    with open(out_pdf, "wb") as f:
        writer.write(f)

# ============================================================
# RELATÓRIO (PRINT)
# ============================================================

def _gerar_relatorio_para_um_pdf(pdf_path: str, link_escalas: str):
    data = extrair_data(pdf_path)
    cabecalho = extrair_cabecalho(pdf_path)
    eventos_1epm = extrair_1epm(pdf_path)

    eventos_lanceiro = extrair_lanceiro_escala(pdf_path)

    # CORP do modelo diário (EFETIVO OPERACIONAL)
    eventos_corp = extrair_corp(pdf_path)

    # CORP - ESCALA específica (ESCALA CORP (COMPANHIA...))
    eventos_corp_escala = extrair_corp_escala(pdf_path)

    escalas_extra = extrair_extrajornada(pdf_path)

    print("*RESUMO OPERACIONAL*")
    print("```Gerado pelo Sistema - P3```")
    print()

    if data:
        print(f"📅*Data:* {data}")
    else:
        print("📅*Data:* NÃO ENCONTRADA")

    print("⏰*Turno:* 7h às 7h")
    print()

    for linha in cabecalho:
        funcao, resto = linha.split(":", 1)
        print(f"✅*{funcao.replace('✅', '')}:* {resto.strip()}")

    print()
    print(f"🔗 *Escalas:* {link_escalas}")
    print()

    # 1º EPM
    if eventos_1epm:
        for ev in eventos_1epm:
            print("🐴 1º EPM")
            print()
            print(f"✅*Evento:* {ev.get('evento', '')}")
            print(f"🔸*Local:* {ev.get('local', '')}")
            print(f"🔸*Ref.:* {ev.get('ref', '')}")
            print(f"🔸*Turno:* {ev.get('turno', '')}")

            viaturas = ev.get("viaturas", [])
            if viaturas:
                print(f"🔸*Viatura:* {', '.join(viaturas)}")
            else:
                print("🔸*Viatura:* Não informada")

            print(f"🔸*Efetivo:* {ev.get('efetivo', 0)}")
            print(f"🔸*Semovente:* {ev.get('semovente', 0)}")
            print(f"🔸*Responsável:* {ev.get('responsavel', '')}")
            print(f"📞*Contato:* {ev.get('telefone', 'Não informado')}")
            print()


    # LANCEIRO
    if eventos_lanceiro:
        for ev in eventos_lanceiro:
            print("⚜️ LANCEIRO")
            print(f"✅*Evento:* {ev.get('evento', '')}")
            print(f"🔸*Turno:* {ev.get('turno', '')}")
            print(f"🔸*VTRs:* {len(ev.get('viaturas', []))}")
            print(f"🔸*Efetivo:* {ev.get('efetivo', 0)}")
            print(f"🔸*Responsável:* {ev.get('responsavel', '')}")
            print(f"📞*Contato:* {ev.get('telefone', 'Não informado')}")
            print()

    # CORP - ESCALA específica (prioriza imprimir esta, quando existir)
    if eventos_corp_escala:
        for ev in eventos_corp_escala:
            print("🚔 4º EPM - CORP")
            print(f"✅*Evento:* {ev.get('evento', '')}")
            print(f"🔸*Turno:* {ev.get('turno', '')}")
            print(f"🔸*VTRs:* {len(ev.get('viaturas', []))}")
            print(f"🔸*Efetivo:* {ev.get('efetivo', 0)}")
            print(f"🔸*Responsável:* {ev.get('responsavel', '')}")
            print(f"📞*Contato:* {ev.get('telefone', 'Não informado')}")
            print()

    # CORP do modelo diário (EFETIVO OPERACIONAL)
    if eventos_corp:
        for ev in eventos_corp:
            print("🚔 4º EPM - CORP")
            print(f"✅*Evento:* {ev.get('evento', '')}")
            print(f"🔸*Turno:* {ev.get('turno', '')}")
            print(f"🔸*VTRs:* {len(ev.get('viaturas', []))}")
            print(f"🔸*Efetivo:* {ev.get('efetivo', 0)}")
            print(f"🔸*Responsável:* {ev.get('responsavel', '')}")
            print(f"📞*Contato:* {ev.get('telefone', 'Não informado')}")
            print()

    # EXTRA JORNADA
    if escalas_extra:
        for ex in escalas_extra:
            print("👮 _*EXTRA JORNADA*_")
            print(f"🔸*Turno:* {ex.get('turno', '')}")
            print(f"🔸*VTRs:* {len(ex.get('viaturas', []))}")
            print(f"🔸*Efetivo:* {ex.get('efetivo', 0)}")
            print(f"🔸*Responsável:* {ex.get('responsavel', '')}")
            print(f"📞*Contato:* {ex.get('telefone', 'Não informado')}")
            print()
    else:
        print("Nenhuma extra jornada encontrada.")
        print()

    # ESCALAS DIVERSAS (template) - se quiser sempre imprimir quando achar, descomente:
    imprimir_escalas_diversas(pdf_path)

    print()

# ============================================================
# EXECUÇÃO "POR DIA" (SEGURA) - COM MINI-PDF
# ============================================================

def gerar_relatorios_por_dia(pdf_grande: str, link_escalas: str):
    """
    - Se o boletim tiver 1 dia só: roda normal no PDF inteiro.
    - Se tiver vários dias: cria um mini-PDF por dia (em pasta temporária) e roda em cada um.
    """
    ranges = _detectar_ranges_por_dia(pdf_grande)

    # se não detectou ranges, roda normal (evita travar)
    if not ranges:
        _gerar_relatorio_para_um_pdf(pdf_grande, link_escalas)
        return

    # ✅ 1 dia -> roda normal (sem mini-pdf)
    if len(ranges) == 1:
        _gerar_relatorio_para_um_pdf(pdf_grande, link_escalas)
        return

    pasta_temp = tempfile.gettempdir()

    for r in ranges:
        data_tag = r["data"].replace("/", "-").replace("?", "X")
        out_pdf = os.path.join(pasta_temp, f"BOLETIM_DIA_{data_tag}.pdf")

        _exportar_pdf_paginas(pdf_grande, r["start"], r["end"], out_pdf)
        _gerar_relatorio_para_um_pdf(out_pdf, link_escalas)

# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    pdf_path = "BOL 027 - 10 FEV 2026.pdf"
    link_escalas = "https://drive.google.com/drive/folders/1QXGtE5ApdNXFG5UnrZodcrhDOHpNDK1b"

    gerar_relatorios_por_dia(pdf_path, link_escalas)


