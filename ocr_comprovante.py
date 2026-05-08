import re
import unicodedata
import logging
import os
import requests
from datetime import datetime

logger = logging.getLogger(__name__)

OCR_API_URL = "https://api.ocr.space/parse/image"
OCR_API_KEY = os.getenv("OCR_API_KEY", "helloworld")

BANCOS = {
    "Nubank":       [r"nubank"],
    "Itau":         [r"ita[u\u00fa]"],
    "Bradesco":     [r"bradesco"],
    "Santander":    [r"santander"],
    "Inter":        [r"banco\s*inter"],
    "Caixa":        [r"caixa"],
    "Mercado Pago": [r"mercado\s*pago"],
    "PicPay":       [r"picpay"],
}

NOME_PADROES = [
    r"institui[c\u00e7][a\u00e3]o\s+([A-Za-z\u00C0-\u00FF][A-Za-z\u00C0-\u00FF\s]{4,60?})\s*(?:\||\d|R\$)",
    r"destino\s*nome\s+([A-Za-z\u00C0-\u00FF][A-Za-z\u00C0-\u00FF\s]{4,60}?)\s*institui[c\u00e7][a\u00e3]o",
    r"pagador\s*[:\-]?\s*([A-Za-z\u00C0-\u00FF][A-Za-z\u00C0-\u00FF\s]{4,50})",
    r"remetente\s*[:\-]?\s*([A-Za-z\u00C0-\u00FF][A-Za-z\u00C0-\u00FF\s]{4,50})",
    r"recebedor\s*[:\-]?\s*([A-Za-z\u00C0-\u00FF][A-Za-z\u00C0-\u00FF\s]{4,50})",
    r"origem\s*[:\-]?\s*([A-Za-z\u00C0-\u00FF][A-Za-z\u00C0-\u00FF\s]{4,50})",
    r"de\s*[:\-]?\s*([A-Z][a-z\u00C0-\u00FF]+(?:\s+[A-Z][a-z\u00C0-\u00FF]+)+)",
    r"nome\s*[:\-]?\s*([A-Za-z\u00C0-\u00FF][A-Za-z\u00C0-\u00FF\s]{4,50})",
]

VALIDACOES = {
    "Nubank": {"campos": [r"nubank", r"R\$", r"\d{2}/\d{2}/\d{4}|\d{2}\s+de\s+\w+"]},
    "Itau":   {"campos": [r"ita[u\u00fa]", r"R\$", r"autentica"]},
    "Bradesco":{"campos": [r"bradesco", r"R\$", r"autentica"]},
    "Inter":  {"campos": [r"inter", r"R\$", r"\d{2}/\d{2}/\d{4}"]},
    "Caixa":  {"campos": [r"caixa", r"R\$", r"\d{2}/\d{2}/\d{4}"]},
}

MESES = {
    'janeiro':1,'fevereiro':2,'marco':3,'abril':4,'maio':5,'junho':6,
    'julho':7,'agosto':8,'setembro':9,'outubro':10,'novembro':11,'dezembro':12,
    'jan':1,'fev':2,'mar':3,'abr':4,'mai':5,'jun':6,
    'jul':7,'ago':8,'set':9,'out':10,'nov':11,'dez':12
}

LIMITE_SEGUNDOS = 24 * 3600  # 24 horas


def _normalizar(text):
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower().strip()


def _detectar_banco(text):
    tl = text.lower()
    for banco, patterns in BANCOS.items():
        for p in patterns:
            if re.search(p, tl):
                return banco
    return "Comprovante"


def _extrair_valor_texto(text):
    for padrao in [
        r'R\$\s*([0-9]+(?:[.,][0-9]{1,2})?)',
        r'valor\s*[:\-]?\s*([0-9]+(?:[.,][0-9]{1,2})?)',
    ]:
        m = re.search(padrao, text, re.IGNORECASE)
        if m:
            v = m.group(1).strip().replace('.', ',')
            if ',' not in v:
                v += ',00'
            elif len(v.split(',')[1]) == 1:
                v += '0'
            return v
    return "N/A"


def _extrair_pagador_texto(text):
    texto_limpo = re.sub(r'nome\s*cpf\s*institui[c\u00e7][a\u00e3]o\s*', '', text, flags=re.IGNORECASE)
    texto_limpo = re.sub(r'nome\s*cpf\s*', '', texto_limpo, flags=re.IGNORECASE)
    texto_limpo = re.sub(r'recebedor\s*nome\s*cpf\s*institui[c\u00e7][a\u00e3]o\s*', '', texto_limpo, flags=re.IGNORECASE)

    for padrao in NOME_PADROES:
        m = re.search(padrao, texto_limpo, flags=re.IGNORECASE)
        if m:
            nome = m.group(1).strip()
            palavras = [p for p in nome.split() if re.match(r'^[A-Za-z\u00C0-\u00FF]+$', p) and len(p) >= 2]
            if len(palavras) >= 2:
                return ' '.join(palavras).title()

    m = re.search(r'([A-Z][a-z\u00C0-\u00FF]+(?:\s+[A-Z][a-z\u00C0-\u00FF]+){1,4})', texto_limpo)
    if m:
        palavras = [p for p in m.group(1).strip().split() if len(p) >= 2]
        if len(palavras) >= 2:
            return ' '.join(palavras).title()

    return "Desconhecido"


def _validar_data(text):
    agora = datetime.now()

    # DD MMM YYYY - HH:MM:SS (Nubank: 05 MAI 2026 - 17:28:25)
    m = re.search(r'(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})\s*[-\u2013]\s*(\d{2}):(\d{2})(?::(\d{2}))?', text, re.IGNORECASE)
    if m:
        try:
            mes = MESES.get(_normalizar(m.group(2)))
            if mes:
                dt = datetime(int(m.group(3)), mes, int(m.group(1)),
                              int(m.group(4)), int(m.group(5)))
                diff = abs((agora - dt).total_seconds())
                if diff > LIMITE_SEGUNDOS:
                    return False, f"Comprovante de {int(diff/3600)} horas atr\u00e1s"
                return True, None
        except Exception:
            pass

    # DD/MM/YYYY, HH:MM:SS (Caixa) ou DD/MM/YYYY HH:MM
    m = re.search(r'(\d{2})/(\d{2})/(\d{4}),?\s*(\d{2}):(\d{2})(?::(\d{2}))?', text)
    if m:
        try:
            dt = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)),
                          int(m.group(4)), int(m.group(5)))
            diff = abs((agora - dt).total_seconds())
            if diff > LIMITE_SEGUNDOS:
                return False, f"Comprovante de {int(diff/3600)} horas atr\u00e1s"
            return True, None
        except Exception:
            pass

    # DD de mes de YYYY as HH:MM (Nubank)
    m = re.search(r'(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})\s*(?:\u00e0s|as)?\s*(\d{2}):(\d{2})', text, re.IGNORECASE)
    if m:
        try:
            mes = MESES.get(_normalizar(m.group(2)))
            if mes:
                dt = datetime(int(m.group(3)), mes, int(m.group(1)),
                              int(m.group(4)), int(m.group(5)))
                diff = abs((agora - dt).total_seconds())
                if diff > LIMITE_SEGUNDOS:
                    return False, f"Comprovante de {int(diff/3600)} horas atr\u00e1s"
                return True, None
        except Exception:
            pass

    # Sem hora — verifica só a data
    m = re.search(r'(\d{2})/(\d{2})/(\d{4})', text)
    if m:
        try:
            dt = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            if dt.date() != agora.date():
                return False, "Comprovante de outro dia"
            return True, None
        except Exception:
            pass

    return True, None


def _validar_formato(text, banco):
    if banco not in VALIDACOES:
        return True, None
    campos_ok = sum(1 for p in VALIDACOES[banco]["campos"] if re.search(p, text, re.IGNORECASE))
    if campos_ok < 2:
        return False, f"Comprovante n\u00e3o parece ser do {banco}"
    return True, None


def _match_nomes(nome_cmd, texto_norm):
    ignorar = {'de', 'da', 'do', 'dos', 'das', 'e'}
    partes = [p for p in nome_cmd.split() if p not in ignorar and len(p) >= 3]
    return all(p in texto_norm for p in partes)


def ler_comprovante_url(image_url, nome=""):
    try:
        resp = requests.post(
            OCR_API_URL,
            data={
                "url": image_url,
                "apikey": OCR_API_KEY,
                "language": "por",
                "isOverlayRequired": False,
                "detectOrientation": True,
                "scale": True,
                "OCREngine": 2,
            },
            timeout=15
        )
        data = resp.json()

        if data.get("IsErroredOnProcessing"):
            return {"encontrado": False, "erro": data.get("ErrorMessage", "Erro OCR")}

        texto = ""
        for result in data.get("ParsedResults", []):
            texto += result.get("ParsedText", "") + " "

        valor = _extrair_valor_texto(texto)
        pagador = _extrair_pagador_texto(texto)
        banco = _detectar_banco(texto)

        logger.info(f"OCR texto: {texto[:300]}")

        if valor == "N/A":
            return {"encontrado": False, "erro": "Valor n\u00e3o encontrado no comprovante"}

        data_valida, motivo_data = _validar_data(texto)
        if not data_valida:
            return {"encontrado": False, "fake": True, "erro": f"\u26a0\ufe0f Comprovante suspeito: {motivo_data}"}

        formato_valido, motivo_formato = _validar_formato(texto, banco)
        if not formato_valido:
            return {"encontrado": False, "fake": True, "erro": f"\u26a0\ufe0f Comprovante suspeito: {motivo_formato}"}

        return {
            "encontrado": True,
            "valor": valor,
            "pagador": pagador,
            "banco": banco,
            "texto": texto[:500],
        }

    except Exception as e:
        logger.error(f"OCR falhou: {type(e).__name__}: {str(e)[:100]}")
        return {"encontrado": False, "erro": str(e)[:100]}
