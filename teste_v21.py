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
                        posto = f"{partes[2]} {partes[3]}"
                        nome_bruto = " ".join(partes[4:])
                    else:
                        funcao = partes[0]
                        posto = f"{partes[1]} {partes[2]}"
                        nome_bruto = " ".join(partes[3:])

                    nome = formatar_nome(nome_bruto.lower())
                    resultado.append(f"✅{funcao}: {posto} {nome}")

    return resultado

# ============================================================
# 1º EPM
# ============================================================

def extrair_1epm(caminho_pdf: str):
    eventos = []
    dentro_1epm = False
    evento_atual = None

    postos_validos = r"(?:\d+[º°]?\s*)?(Ten\.?|Sgt\.?|Cap\.?|Maj\.?|Cel\.?|Cb\.?|Sd\.?)"
    padrao_tel = re.compile(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}")
    padrao_rg_numerico = re.compile(r"\b\d{7,10}\b")
    padrao_rg_pontuado = re.compile(r"\b\d{1,2}\.\d{3}\.\d{3}-\d\b")

    padrao_vtr = re.compile(r"(?<!\d)(1\d{4}|L\d{4})(?!\d)", re.IGNORECASE)

    with pdfplumber.open(caminho_pdf) as pdf:
        for pagina in pdf.pages:
            texto = pagina.extract_text() or ""
            if not texto.strip():
                continue

            for linha in texto.split("\n"):
                linha_limpa = normalizar_linha(linha)
                if not linha_limpa:
                    continue

                # ---------------------------
                # Entrar no bloco 1º EPM (somente cabeçalho)
                # ---------------------------
                if (not dentro_1epm) and re.match(r"^\s*1(?:[º°o])?\s*EPM\b", linha_limpa, re.IGNORECASE):
                    dentro_1epm = True
                    continue

                if not dentro_1epm:
                    continue

                # ---------------------------
                # Sair do 1º EPM (somente se for CABEÇALHO real do 2º/3º EPM ou CORP)
                # Evita sair por "Apoio 2ºEPM"
                # ---------------------------
                eh_inicio_outro_epm = bool(re.match(r"^\s*(2|3)(?:[º°o])?\s*EPM\b", linha_limpa, re.IGNORECASE))
                up = linha_limpa.upper()
                eh_inicio_corp = (up == "CORP") or up.startswith("CORP ") or ("ESCALA CORP" in up)

                if eh_inicio_outro_epm or eh_inicio_corp:
                    if evento_atual:
                        eventos.append(evento_atual)
                        evento_atual = None
                    return eventos

                # ---------------------------
                # Novo evento
                # ---------------------------
                if linha_limpa.startswith("EVENTO:"):
                    if evento_atual:
                        eventos.append(evento_atual)

                    evento_atual = {
                        "evento": linha_limpa.replace("EVENTO:", "").strip(),
                        "local": "",
                        "ref": "",
                        "turno": "",
                        "efetivo": 0,
                        "semovente": 0,
                        "viaturas": [],
                        "responsavel": "",
                        "telefone": "Não informado"
                    }
                    continue

                if not evento_atual:
                    continue

                # ---------------------------
                # Campos do evento
                # ---------------------------
                if linha_limpa.startswith("LOCAL:"):
                    evento_atual["local"] = linha_limpa.replace("LOCAL:", "").strip()
                    continue

                if linha_limpa.upper().startswith("REF"):
                    partes = linha_limpa.split(":", 1)
                    if len(partes) > 1:
                        evento_atual["ref"] = partes[1].strip()
                    continue

                if "NO LOCAL:" in linha_limpa.upper():
                    mturno = re.search(r"No local:\s*(.*)", linha_limpa, re.IGNORECASE)
                    if mturno:
                        evento_atual["turno"] = mturno.group(1).strip()
                    continue

                # Viaturas
                for vtr in padrao_vtr.findall(linha_limpa):
                    vtr = vtr.upper()
                    if vtr not in evento_atual["viaturas"]:
                        evento_atual["viaturas"].append(vtr)

                # ---------------------------
                # Linha de policial (tabela do 1º EPM)
                # Ex.: "1 Cb. QP PM Fulano ... RG ... Tel ..."
                # ---------------------------
                linha_policial_tabela = re.search(rf"^\d+\s+{postos_validos}\b", linha_limpa, re.IGNORECASE)
                if linha_policial_tabela:
                    evento_atual["efetivo"] += 1

                    # semovente: seu critério original
                    if re.search(r"n[º°]\s*\d+", linha_limpa, re.IGNORECASE):
                        evento_atual["semovente"] += 1

                    # responsável = primeiro policial da tabela
                    if not evento_atual["responsavel"]:
                        resp = linha_limpa
                        resp = re.sub(r"^\d+\s+", "", resp)         # remove número da linha
                        resp = resp.split("/", 1)[0].strip()
                        resp = resp.rstrip("/").strip()
                        resp = padrao_tel.sub("", resp)
                        resp = padrao_rg_numerico.sub("", resp)
                        resp = padrao_rg_pontuado.sub("", resp)
                        resp = re.sub(r"\bRG\b\s*:?", "", resp, flags=re.IGNORECASE)
                        resp = re.sub(r"/\s*RG\s*:?", "", resp, flags=re.IGNORECASE)
                        resp = resp.replace(" QP PM", "").replace(" QOEM PM", "")
                        resp = re.sub(r"\s{2,}", " ", resp).strip()

                        evento_atual["responsavel"] = resp

                        tel = padrao_tel.search(linha_limpa)
                        evento_atual["telefone"] = tel.group() if tel else "Não informado"

    # Se o PDF acabou ainda dentro do evento
    if evento_atual:
        eventos.append(evento_atual)

    return eventos
# ============================================================
# CORP / 4º EPM (modelo do boletim diário)
# ============================================================

def ajustar_turno(turno: str) -> str:
    turno = (turno or "").replace("ás", "às")
    if "06h45" in turno and "12h30" in turno:
        return "6h às 12h45"
    if "15h45" in turno and "21h30" in turno:
        return "15h às 21h45"
    return turno.strip()

def extrair_corp(caminho_pdf: str):
    eventos = []
    dentro_corp = False
    dentro_efetivo = False
    evento_atual = None

    postos_validos = r"(?:\d+[º°]?\s*)?(Ten\.?|Sgt\.?|Cap\.?|Maj\.?|Cel\.?|Cb\.?|Sd\.?)"
    padrao_vtr = re.compile(r"(?<!\d)(1\d{4}|L\d{4})(?!\d)", re.IGNORECASE)
    padrao_tel = re.compile(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}")
    padrao_rg_numerico = re.compile(r"\b\d{7,10}\b")
    padrao_rg_pontuado = re.compile(r"\b\d{1,2}\.\d{3}\.\d{3}-\d\b")
    # ✅ Assinatura padrão do fim da escala CORP
    padrao_assinatura_corp = re.compile(
        r"\b(?:respondente|resp\.?)(?:\s*(?:/|\\)\s*|\s+)"
        r"(?:pelo\s+)?(?:comando\s+)?(?:do|da)?\s*corp\b"
        r"|\b(?:respondente|resp\.?)(?:\s+)?pelo\s+comando\s+(?:do|da)?\s*corp\b"
        r"|\bcomandante\s+(?:do|da)?\s*corp\b"
        r"|\bcmt\.?\s*corp\b"
        r"|\bcomando\s+(?:do|da)?\s*corp\b",
        re.IGNORECASE
    )
    padrao_linha_oficial_assina = re.compile(
        r"^\s*(?:\d+\s*)?(?:\d+[º°o]?\s*)?(?:TEN\.?|TENENTE|CAP\.?|CAPITAO|MAJ\.?|MAJOR|CEL\.?|CORONEL)\b.*",
        re.IGNORECASE
    )
    padrao_fim_partes = re.compile(r"\b(2[ªa]?\s*PARTE|3[ªa]?\s*PARTE|ASSUNTOS\s+GERAIS|INSTRUÇÃO)\b", re.IGNORECASE)

    def iniciar_evento():
        return {
            "evento": "Patrulhamento Preventivo",
            "turno": "",
            "viaturas": set(),
            "efetivo": 0,
            "responsavel": "",
            "telefone": "Não informado"
        }

    def fechar_evento():
        nonlocal evento_atual
        if not evento_atual:
            return
        evento_atual["viaturas"] = sorted(list(evento_atual["viaturas"]))
        eventos.append(evento_atual)
        evento_atual = None

    with pdfplumber.open(caminho_pdf) as pdf:
        for pagina in pdf.pages:
            texto = pagina.extract_text()
            if not texto:
                continue

            for linha in texto.split("\n"):
                linha_limpa = normalizar_linha(linha)
                if not linha_limpa:
                    continue

                up = linha_limpa.upper()

                if up == "CORP" or "ESCALA CORP" in up:
                    dentro_corp = True
                    continue

                if "EXTRA JORNADA" in up and dentro_corp:
                    if dentro_efetivo and evento_atual:
                        fechar_evento()
                    dentro_efetivo = False
                    dentro_corp = False
                    continue

                if not dentro_corp:
                    continue


                # 🟦 BACKUP: abre EFETIVO quando o cabeçalho da tabela aparecer
                # (mesmo se "EFETIVO OPERACIONAL" veio com erro de extração)
                if dentro_corp and (not dentro_efetivo) and eh_inicio_tabela_corp(linha_limpa):
                    dentro_efetivo = True
                    evento_atual = iniciar_evento()
                # ✅ Para no fim da escala CORP (assinatura)
                if padrao_assinatura_corp.search(linha_limpa):
                    if dentro_efetivo and evento_atual:
                        fechar_evento()
                    dentro_efetivo = False
                    dentro_corp = False
                    continue
                # ✅ Segurança: não deixar CORP vazar para 2ª/3ª parte
                if padrao_fim_partes.search(linha_limpa):
                    if dentro_efetivo and evento_atual:
                        fechar_evento()
                    dentro_efetivo = False
                    dentro_corp = False
                    continue
                # Linha do oficial assinante costuma vir antes da assinatura e pode aparecer em 1-2 linhas
                if padrao_linha_oficial_assina.search(linha_limpa) and linha_limpa.endswith(','):
                    # não fecha aqui; espera a linha 'Respondente...' para fechar com segurança
                    pass

                if eh_efetivo_operacional(linha_limpa):
                    if dentro_efetivo and evento_atual:
                        fechar_evento()
                    dentro_efetivo = True
                    evento_atual = iniciar_evento()
                    continue

                if dentro_efetivo and ("ESCALAS DIVERSAS" in up or up.startswith("CURITIBA,")):
                    if evento_atual:
                        fechar_evento()
                    dentro_efetivo = False
                    continue

                if not dentro_efetivo or not evento_atual:
                    continue

                if re.search(r"hor[áa]rio\s+no\s+local\s*:", linha_limpa, re.IGNORECASE):
                    mloc = re.search(r"hor[áa]rio\s+no\s+local\s*:\s*(.+)$", linha_limpa, re.IGNORECASE)
                    if mloc:
                        turno_bruto = mloc.group(1).strip()
                        turno_bruto = turno_bruto.replace("ás", "às").replace("Ás", "às")
                        try:
                            evento_atual["turno"] = ajustar_turno(turno_bruto)
                        except Exception:
                            evento_atual["turno"] = turno_bruto

                for vtr in padrao_vtr.findall(linha_limpa):
                    evento_atual["viaturas"].add(vtr.upper())

                if re.search(rf"\b{postos_validos}\b", linha_limpa, re.IGNORECASE):
                    evento_atual["efetivo"] += 1

                    if evento_atual["efetivo"] == 1:
                        resp = linha_limpa
                        resp = padrao_tel.sub("", resp)
                        resp = padrao_rg_numerico.sub("", resp)
                        resp = padrao_rg_pontuado.sub("", resp)
                        resp = re.sub(r"\bRG\b\s*:?", "", resp, flags=re.IGNORECASE)
                        resp = re.sub(r"/\s*RG\s*:?", "", resp, flags=re.IGNORECASE)
                        resp = resp.replace(" QP PM", "").replace(" QOEM PM", "")
                        resp = re.sub(r"\s{2,}", " ", resp).strip()

                        evento_atual["responsavel"] = resp

                        tel = padrao_tel.search(linha_limpa)
                        evento_atual["telefone"] = tel.group() if tel else "Não informado"

    if dentro_efetivo and evento_atual:
        fechar_evento()

    return eventos

# ============================================================
# CORP - ESCALA ESPECÍFICA (ESCALA CORP (COMPANHIA OPERACIONAL...))
# ============================================================

def extrair_corp_escala(caminho_pdf: str):
    """
    Lógica (conforme solicitado):
    - Procurar a linha "ESCALA CORP (COMPANHIA OPERACIONAL DE RECOBRIMENTO PREVENTIVO)"
    - A linha subsequente é o "evento". Se a linha estiver vazia ou começar com "DATA", ignora esse bloco.
    - Dentro do bloco, quando localizar:
        "Data e hora prevista para a saída:" e
        "Data e hora prevista para o retorno:"
      calcula turno = saída até (retorno - 15min).
    - Captura tabela VTR/GRAD/NOME/RG/TELEFONE; VTR = 5 dígitos (às vezes L + 5 dígitos).
    - Se dentro do mesmo bloco aparecer NOVA saída/retorno ou NOVA equipe, fecha e cria novo período.
    - Responsável = policial mais antigo (pela graduação e número ordinal, ex.: 1º Sgt. mais antigo que 3º Sgt.).
    """

    padrao_inicio = re.compile(
        r"\bESCALA\s+CORP\s*\(.*COMPANHIA\s+OPERACIONAL\s+DE\s+RECOBRIMENTO\s+PREVENTIVO.*\)",
        re.IGNORECASE
    )
    padrao_linha_data = re.compile(r"^\s*DATA\b", re.IGNORECASE)

    padrao_equipe = re.compile(r"\bEQUIPE\s+DO\s+\d+[º°]?\s*PER[IÍ]ODO\b", re.IGNORECASE)
    padrao_saida = re.compile(r"Data\s+e\s+hora\s+prevista\s+para\s+a\s+sa[ií]da\s*:\s*(.*)$", re.IGNORECASE)
    padrao_retorno = re.compile(r"Data\s+e\s+hora\s+prevista\s+para\s+o\s+retorno\s*:\s*(.*)$", re.IGNORECASE)

    padrao_fim_assinatura = re.compile(r"^\s*Curitiba\s*,", re.IGNORECASE)
    padrao_fim_secao = re.compile(r"\b(ESCALA\s+DE\s+SERVI[ÇC]O\b|EXTRA\s*[-]?\s*JORNADA\b|ESCALAS?\s+DIVERSAS?\b)\b", re.IGNORECASE)

    padrao_cabecalho_tabela = re.compile(r"\bVTR\b.*\bGRAD\b.*\bNOME\b.*\bRG\b.*\bTELEFONE\b", re.IGNORECASE)

    padrao_tel = re.compile(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}")
    padrao_rg_numerico = re.compile(r"\b\d{7,10}\b")
    padrao_rg_pontuado = re.compile(r"\b\d{1,2}\.\d{3}\.\d{3}-\d\b")

    # VTR: mesma lógica do extrair_corp() (1xxxx ou Lxxxx)
    padrao_vtr = re.compile(r"(?<!\d)(1\d{4}|L\d{4})(?!\d)", re.IGNORECASE)

    # posto/grad detectável na linha (para contar efetivo)
    padrao_posto_grad = re.compile(
        r"\b(?:(\d+)[º°]?\s*)?(Ten\.?|Sgt\.?|Cb\.?|Sd\.?)\s+(?:QP|QOEM)\s+PM\b",
        re.IGNORECASE
    )

    # ordem antiguidade: menor = mais antigo
    ordem_base = {"cel": 1, "maj": 2, "cap": 3, "ten": 4, "sgt": 5, "cb": 6, "sd": 7}

    def _tem_rg_ou_tel(s: str) -> bool:
        return bool(padrao_tel.search(s) or padrao_rg_numerico.search(s) or padrao_rg_pontuado.search(s))

    def _parse_time_to_minutes(s: str):
        if not s:
            return None
        s = s.replace("hmin", "h").replace("min", "").replace("H", "h")
        achados = re.findall(r"(\d{1,2})\s*h\s*(\d{2})?", s, flags=re.IGNORECASE)
        if not achados:
            return None
        hh, mm = achados[-1][0], (achados[-1][1] or "00")
        try:
            return int(hh) * 60 + int(mm)
        except:
            return None

    def _fmt_hora(mins: int):
        if mins is None:
            return ""
        hh = mins // 60
        mm = mins % 60
        return f"{hh}h" if mm == 0 else f"{hh}h{mm:02d}"

    def _montar_turno(saida_raw: str, retorno_raw: str):
        saida_m = _parse_time_to_minutes(saida_raw)
        ret_m = _parse_time_to_minutes(retorno_raw)
        if saida_m is None or ret_m is None:
            return ""
        ret_m_aj = max(0, ret_m - 15)
        return f"{_fmt_hora(saida_m)} às {_fmt_hora(ret_m_aj)}"

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

        if num:
            posto_grad = f"{num}º {sig} QP PM"
        else:
            posto_grad = f"{sig} QP PM"

        resto = linha[m.end():].strip()

        corte = len(resto)
        for mm in [padrao_rg_pontuado.search(resto), padrao_rg_numerico.search(resto), padrao_tel.search(resto)]:
            if mm:
                corte = min(corte, mm.start())

        nome = resto[:corte].strip(" -/|")
        nome = re.sub(r"\s{2,}", " ", nome).strip()
        return posto_grad.replace("  ", " ").strip(), nome

    def _novo_periodo(evento_titulo: str):
        return {
            "evento": evento_titulo,
            "turno": "",
            "viaturas": set(),
            "efetivo": 0,
            "responsavel": "",
            "telefone": "Não informado",
            "_policiais": []
        }

    def _fechar_periodo(periodo, out_list):
        if not periodo:
            return
        if periodo["_policiais"]:
            periodo["_policiais"].sort(key=lambda x: x["peso"])
            escolhido = periodo["_policiais"][0]
            periodo["responsavel"] = f"{escolhido['posto_grad']} {escolhido['nome']}".strip()
            if escolhido.get("telefone"):
                periodo["telefone"] = escolhido["telefone"]

        periodo["viaturas"] = sorted(list(periodo["viaturas"]))
        periodo.pop("_policiais", None)
        out_list.append(periodo)

    eventos = []
    dentro_bloco = False
    evento_titulo = ""
    periodo = None
    dentro_tabela = False

    saida_raw = ""
    retorno_raw = ""

    pendente = None  # para casos em que linha do policial "quebra" e RG/tel vem na linha seguinte

    with pdfplumber.open(caminho_pdf) as pdf:
        for pagina in pdf.pages:
            texto = pagina.extract_text() or ""
            if not texto.strip():
                continue

            linhas = [normalizar_linha(l) for l in texto.split("\n") if normalizar_linha(l)]

            i = 0
            while i < len(linhas):
                linha = linhas[i]

                # início do bloco
                if padrao_inicio.search(linha):
                    prox = linhas[i + 1] if i + 1 < len(linhas) else ""
                    if (not prox) or padrao_linha_data.search(prox):
                        # ignora
                        dentro_bloco = False
                        evento_titulo = ""
                        if periodo:
                            _fechar_periodo(periodo, eventos)
                        periodo = None
                        dentro_tabela = False
                        saida_raw = ""
                        retorno_raw = ""
                        pendente = None
                        i += 1
                        continue

                    # abre bloco com título na linha subsequente
                    evento_titulo = prox.strip()
                    dentro_bloco = True

                    # reseta estado
                    if periodo:
                        _fechar_periodo(periodo, eventos)
                    periodo = None
                    dentro_tabela = False
                    saida_raw = ""
                    retorno_raw = ""
                    pendente = None

                    i += 2
                    continue

                if not dentro_bloco:
                    i += 1
                    continue

                # fim do bloco
                if padrao_fim_assinatura.search(linha) or padrao_fim_secao.search(linha):
                    if pendente and periodo:
                        # se ficou pendente mas já tinha nome/posto, contabiliza mesmo assim
                        periodo["efetivo"] += 1
                        periodo["_policiais"].append(pendente)
                        pendente = None

                    if periodo:
                        _fechar_periodo(periodo, eventos)
                        periodo = None
                    dentro_bloco = False
                    dentro_tabela = False
                    saida_raw = ""
                    retorno_raw = ""
                    pendente = None
                    i += 1
                    continue

                # nova equipe = novo período
                if padrao_equipe.search(linha):
                    if pendente and periodo:
                        periodo["efetivo"] += 1
                        periodo["_policiais"].append(pendente)
                        pendente = None

                    if periodo:
                        _fechar_periodo(periodo, eventos)
                    periodo = _novo_periodo(evento_titulo)
                    dentro_tabela = False
                    saida_raw = ""
                    retorno_raw = ""
                    i += 1
                    continue

                # saída
                m_saida = padrao_saida.search(linha)
                if m_saida:
                    if pendente and periodo:
                        periodo["efetivo"] += 1
                        periodo["_policiais"].append(pendente)
                        pendente = None

                    # se já tinha dados nesse período, abre novo
                    if periodo and (saida_raw or retorno_raw or periodo["efetivo"] > 0 or len(periodo["viaturas"]) > 0):
                        _fechar_periodo(periodo, eventos)
                        periodo = _novo_periodo(evento_titulo)
                        dentro_tabela = False
                        saida_raw = ""
                        retorno_raw = ""

                    if not periodo:
                        periodo = _novo_periodo(evento_titulo)

                    saida_raw = m_saida.group(1).strip()
                    if retorno_raw:
                        periodo["turno"] = _montar_turno(saida_raw, retorno_raw)

                    i += 1
                    continue

                # retorno
                m_ret = padrao_retorno.search(linha)
                if m_ret:
                    if not periodo:
                        periodo = _novo_periodo(evento_titulo)
                    retorno_raw = m_ret.group(1).strip()
                    if saida_raw:
                        periodo["turno"] = _montar_turno(saida_raw, retorno_raw)
                    i += 1
                    continue

                # cabeçalho da tabela
                if padrao_cabecalho_tabela.search(linha):
                    dentro_tabela = True
                    pendente = None
                    i += 1
                    continue

                # dentro da tabela: vtr + efetivo
                if dentro_tabela and periodo:                    # tenta capturar VTR (mesma lógica do extrair_corp)
                    for vtr in padrao_vtr.findall(linha):
                        periodo["viaturas"].add(vtr.upper())

                    # se tinha policial pendente e agora veio RG/tel na linha seguinte
                    if pendente and (not padrao_posto_grad.search(linha)) and _tem_rg_ou_tel(linha):
                        periodo["efetivo"] += 1
                        # atualiza telefone se existir
                        mt = padrao_tel.search(linha)
                        if mt and not pendente.get("telefone"):
                            pendente["telefone"] = mt.group()
                        periodo["_policiais"].append(pendente)
                        pendente = None
                        i += 1
                        continue

                    # detecta linha com posto/grad
                    if padrao_posto_grad.search(linha):
                        posto_grad, nome = _extrair_posto_grad_e_nome(linha)
                        if posto_grad and nome:
                            tel = ""
                            mt = padrao_tel.search(linha)
                            if mt:
                                tel = mt.group()

                            polic = {
                                "posto_grad": posto_grad,
                                "nome": nome,
                                "telefone": tel,
                                "peso": _peso_antiguidade(posto_grad)
                            }

                            # se tem RG/tel na mesma linha, conta já
                            if _tem_rg_ou_tel(linha):
                                periodo["efetivo"] += 1
                                periodo["_policiais"].append(polic)
                                pendente = None
                            else:
                                # aguarda a próxima linha trazer RG/tel
                                pendente = polic

                        i += 1
                        continue

                    # heurística de fim de tabela
                    if linha.lower().startswith("obs:") or linha.lower().startswith("observa"):
                        if pendente and periodo:
                            periodo["efetivo"] += 1
                            periodo["_policiais"].append(pendente)
                            pendente = None
                        dentro_tabela = False

                    i += 1
                    continue

                i += 1

    # fecha último período
    if pendente and periodo:
        periodo["efetivo"] += 1
        periodo["_policiais"].append(pendente)
        pendente = None

    if periodo:
        _fechar_periodo(periodo, eventos)

    return eventos


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

def extrair_extrajornada(caminho_pdf: str):
    """
    EXTRA JORNADA / DEAEV
    Regras (corrigidas):
    - Só processa dentro do bloco de EXTRA JORNADA (ou DEAEV).
    - Cada escala inicia em uma linha com "HORÁRIO:" (mesmo se vier no meio da linha)
      e termina no próximo "HORÁRIO:" ou na ASSINATURA / início de outra parte do boletim.
    - VTRs: conta somente VTRs do tipo 1xxxx ou Lxxxx (com tolerância a espaços), deduplicadas por escala.
    - Efetivo: cada policial = (posto/grad + nome). Conta por linhas de tabela (com tolerância a quebra de linha).
    - Reforço: usa extract_tables() para pegar VTR/linhas que o extract_text() às vezes “perde”.
    """
    import pdfplumber
    import re

    escalas = []
    dentro_extra = False
    escala_atual = None
    dentro_tabela = False
    pendente = None  # guarda policial quando a linha quebra (nome em uma linha, RG/telefone na outra)

    # Início/Fim de seção
    padrao_inicio_extra = re.compile(r"\b(?:EXTRA\s*[-]?\s*JORNADA|EXTRAJORNADA|DEAEV)\b", re.IGNORECASE)
    padrao_fim_secao = re.compile(
        r"\b(2[ªa]?\s*PARTE|3[ªa]?\s*PARTE|SITUAÇÃO|REFERÊNCIAS|REFERENCIAS)\b",
        re.IGNORECASE
    )

    # Horário: pode vir no começo OU no meio da linha (às vezes o PDF cola em "Evento:")
    padrao_horario_linha = re.compile(r"\bHOR[ÁA]RIO\b\s*:\s*(.+)$", re.IGNORECASE)
    padrao_horario_inline = re.compile(
        r"\bHOR[ÁA]RIO\b\s*:\s*([0-9]{1,2}h(?:\d{2})?\s*(?:às|as)\s*[0-9]{1,2}h(?:\d{2})?)",
        re.IGNORECASE
    )

    # Cabeçalho de tabela (aceita "EQ." ou "EQUIPE")
    padrao_cabecalho_tabela = re.compile(
        r"\b(?:EQ\.?|EQUIPE)\b.*\bVTR\b.*\bPOSTO/GRAD\b.*\bNOME\b",
        re.IGNORECASE
    )

    # Assinatura (fecha tabela/escala) - SOMENTE se a linha parecer uma assinatura (início da linha)
    padrao_assinatura = re.compile(
        r"^\s*(?:CAP\.?|TEN\.?|TENENTE|MAJ\.?|MAJOR|CEL\.?|CORONEL)\b|^\s*(?:CHEFE|COMANDANTE|SUBCOMANDANTE|RESPONDENTE)\b",
        re.IGNORECASE
    )

    # VTR (com tolerância a espaço: "16 550")
    padrao_vtr = re.compile(r"(?<!\d)(?:1\s*\d{4}|L\s*\d{4})(?!\d)", re.IGNORECASE)

    # Telefones e RG
    padrao_tel = re.compile(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}")
    padrao_rg_numerico = re.compile(r"\b\d{7,10}\b")
    padrao_rg_pontuado = re.compile(r"\b\d{1,2}\.\d{3}\.\d{3}-\d\b")

    # Posto/Grad + nome
    padrao_posto_grad = re.compile(
        r"\b(?:(\d+)[º°]?\s*)?(Ten\.?|Sgt\.?|Cb\.?|Sd\.?)\.?(?:\s+(?:QP|QOEM))?(?:\s+PM)?\b",
        re.IGNORECASE
    )

    def normalizar_linha_local(s: str) -> str:
        s = (s or "").replace("\u00a0", " ").replace("\t", " ")
        s = re.sub(r"\s+", " ", s).strip()
        return s

    def tem_rg_ou_tel(linha: str) -> bool:
        return bool(padrao_tel.search(linha) or padrao_rg_numerico.search(linha) or padrao_rg_pontuado.search(linha))

    def linha_tem_posto(linha: str) -> bool:
        return bool(padrao_posto_grad.search(linha))

    def limpar_linha_tabela(linha: str) -> str:
        s = linha.strip()
        s = re.sub(r"^\s*\d+\s+", "", s)  # remove EQ
        s = re.sub(r"^\s*(?:L?\s*\d{4,5})\s+", "", s, flags=re.IGNORECASE)  # remove VTR se vier antes do posto
        return s.strip()

    def extrair_posto_nome(linha: str):
        s = limpar_linha_tabela(linha)
        m = padrao_posto_grad.search(s)
        if not m:
            return None, None

        posto_grad = re.sub(r"\s+", " ", m.group(0)).strip()
        resto = s[m.end():].strip()

        # corta no início de RG ou telefone, se houver
        corte = len(resto)
        for mm in [padrao_rg_pontuado.search(resto), padrao_rg_numerico.search(resto), padrao_tel.search(resto)]:
            if mm:
                corte = min(corte, mm.start())
        nome = resto[:corte].strip(" -/|")
        nome = re.sub(r"\s{2,}", " ", nome).strip()

        return posto_grad, nome

    def iniciar_escala(turno: str):
        return {
            "turno": (turno or "").strip(),
            "viaturas": set(),
            "policiais_set": set(),
            "efetivo": 0,
            "responsavel": "",
            "telefone": "Não informado"
        }

    def add_vtr(vtr_raw: str):
        if not escala_atual:
            return
        v = re.sub(r"\s+", "", (vtr_raw or "")).upper()
        if re.fullmatch(r"(1\d{4}|L\d{4})", v):
            escala_atual["viaturas"].add(v)

    def add_policial(posto_grad: str, nome: str, tel: str | None = None):
        if not escala_atual:
            return
        posto_grad = normalizar_linha_local(posto_grad)
        nome = normalizar_linha_local(nome)
        chave = (posto_grad.upper() + "|" + nome.upper()).strip("|")
        if chave:
            escala_atual["policiais_set"].add(chave)

        if (not escala_atual["responsavel"]) and posto_grad and nome:
            escala_atual["responsavel"] = f"{posto_grad} {nome}".strip()
            if tel and escala_atual["telefone"] == "Não informado":
                escala_atual["telefone"] = tel

    def absorver_tabela_da_pagina(page):
        """
        Reforço: tenta capturar VTR/efetivo via extract_tables().
        Isso resolve linhas em que extract_text() “some” com a coluna VTR.
        """
        if not escala_atual:
            return
        try:
            tables = page.extract_tables() or []
        except Exception:
            tables = []

        for tb in tables:
            if not tb or len(tb) < 2:
                continue

            header = " ".join(str(x or "") for x in tb[0])
            if ("VTR" not in header.upper()) or ("NOME" not in header.upper()):
                continue

            # colunas esperadas: EQ | VTR | Posto/Grad | Nome | RG | TELEFONE | C.P.
            for row in tb[1:]:
                if not row or len(row) < 4:
                    continue

                vtr = str(row[1] or "").strip()
                posto = str(row[2] or "").strip()
                nome = str(row[3] or "").strip()
                tel = str(row[5] or "").strip() if len(row) >= 6 else ""

                if re.fullmatch(r"\d{5}", vtr):
                    add_vtr(vtr)

                if posto and nome and padrao_posto_grad.search(posto):
                    add_policial(posto, nome, tel if padrao_tel.search(tel) else None)

            break  # achou uma tabela válida

    def fechar_escala():
        nonlocal escala_atual, dentro_tabela, pendente
        if not escala_atual:
            return

        # finaliza pendente (se houver)
        if pendente:
            add_policial(pendente.get("posto_grad", ""), pendente.get("nome", ""))
            pendente = None

        escala_atual["viaturas"] = sorted(list(escala_atual["viaturas"]))
        escala_atual["efetivo"] = len(escala_atual["policiais_set"])
        escala_atual.pop("policiais_set", None)
        escalas.append(escala_atual)

        escala_atual = None
        dentro_tabela = False
        pendente = None

    with pdfplumber.open(caminho_pdf) as pdf:
        for page in pdf.pages:
            texto = page.extract_text() or ""
            if not texto.strip():
                continue

            # detecta entrada no bloco EXTRA (mesmo se vier só no título da escala)
            if (not dentro_extra) and (padrao_inicio_extra.search(texto) or "ESCALA DE SERVIÇO EXTRA JORNADA" in texto.upper()):
                dentro_extra = True

            if not dentro_extra:
                continue

            for linha in texto.split("\n"):
                linha = normalizar_linha_local(linha)
                if not linha:
                    continue

                # fim do bloco extra
                if padrao_fim_secao.search(linha):
                    if escala_atual:
                        fechar_escala()
                    dentro_extra = False
                    break

                # nova escala pelo horário
                mhor = padrao_horario_linha.search(linha) or padrao_horario_inline.search(linha)
                if mhor:
                    if escala_atual:
                        fechar_escala()
                    escala_atual = iniciar_escala(mhor.group(1).strip())
                    dentro_tabela = False
                    pendente = None

                    # reforço: já tenta puxar tabela inteira da página
                    absorver_tabela_da_pagina(page)
                    continue

                if not escala_atual:
                    continue

                # abre tabela
                if padrao_cabecalho_tabela.search(linha):
                    dentro_tabela = True
                    pendente = None
                    continue

                # se achou posto/grad, assume que é tabela mesmo sem cabeçalho (continuação de página)
                if (not dentro_tabela) and linha_tem_posto(linha):
                    dentro_tabela = True

                # assinatura fecha a escala
                if padrao_assinatura.search(linha) and (not linha_tem_posto(linha)) and (not tem_rg_ou_tel(linha)):
                    fechar_escala()
                    continue

                # VTRs (fallback)
                for vtr in padrao_vtr.findall(linha):
                    add_vtr(vtr)

                if not dentro_tabela:
                    # telefone fallback
                    if escala_atual["telefone"] == "Não informado":
                        mt = padrao_tel.search(linha)
                        if mt:
                            escala_atual["telefone"] = mt.group()
                    continue

                # ---- dentro da tabela ----

                # pendente + veio RG/tel na linha seguinte
                if pendente and (not linha_tem_posto(linha)) and tem_rg_ou_tel(linha):
                    add_policial(pendente.get("posto_grad", ""), pendente.get("nome", ""))
                    if not escala_atual["responsavel"]:
                        escala_atual["responsavel"] = f"{pendente.get('posto_grad','')} {pendente.get('nome','')}".strip()
                        mt = padrao_tel.search(linha)
                        if mt:
                            escala_atual["telefone"] = mt.group()
                    pendente = None
                    continue

                # pendente + veio continuação do nome
                if pendente and (not linha_tem_posto(linha)) and (not tem_rg_ou_tel(linha)) and re.search(r"[A-Za-zÀ-ÿ]", linha):
                    pendente["nome"] = (pendente.get("nome", "") + " " + linha).strip()
                    continue

                if linha_tem_posto(linha):
                    posto_grad, nome = extrair_posto_nome(linha)
                    if posto_grad is None:
                        continue

                    if tem_rg_ou_tel(linha):
                        add_policial(posto_grad, nome)
                        if not escala_atual["responsavel"]:
                            escala_atual["responsavel"] = f"{posto_grad} {nome}".strip()
                            mt = padrao_tel.search(linha)
                            if mt:
                                escala_atual["telefone"] = mt.group()
                        pendente = None
                    else:
                        pendente = {"posto_grad": posto_grad, "nome": nome or ""}
                        if not escala_atual["responsavel"]:
                            escala_atual["responsavel"] = f"{posto_grad} {nome}".strip()

            # ao fim da página: reforça tabelas (continuação)
            if dentro_extra and escala_atual and dentro_tabela:
                absorver_tabela_da_pagina(page)

    if escala_atual:
        fechar_escala()

    return escalas
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
