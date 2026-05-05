import re
import unicodedata
import logging
import os
import requests

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
    r"pagador\s*[:\-]?\s*([A-Za-z\u00C0-\u00FF][A-Za-z\u00C0-\u00FF\s]{4,50})",
    r"remetente\s*[:\-]?\s*([A-Za-z\u00C0-\u00FF][A-Za-z\u00C0-\u00FF\s]{4,50})",
    r"origem\s*[:\-]?\s*([A-Za-z\u00C0-\u00FF][A-Za-z\u00C0-\u00FF\s]{4,50})",
    r"de\s*[:\-]?\s*([A-Z][a-z\u00C0-\u00FF]+(?:\s+[A-Z][a-z\u00C0-\u00FF]+)+)",
    r"nome\s*[:\-]?\s*([A-Za-z\u00C0-\u00FF][A-Za-z\u00C0-\u00FF\s]{4,50})",
]


def _normalizar(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower().strip()


def _detectar_banco(text: str) -> str:
    tl = text.lower()
    for banco, patterns in BANCOS.items():
        for p in patterns:
            if re.search(p, tl):
                return banco
    return "Comprovante"


def _extrair_valor_texto(text: str) -> str:
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


def _extrair_pagador_texto(text: str) -> str:
    for padrao in NOME_PADROES:
        m = re.search(padrao, text, flags=re.IGNORECASE)
        if m:
            nome = m.group(1).strip()
            palavras = [p for p in nome.split() if re.match(r'^[A-Za-z\u00C0-\u00FF]+$', p) and len(p) >= 2]
            if len(palavras) >= 2:
                return ' '.join(palavras).title()
    return "Desconhecido"


def _match_nomes(nome_cmd: str, texto_norm: str) -> bool:
    ignorar = {'de', 'da', 'do', 'dos', 'das', 'e'}
    partes = [p for p in nome_cmd.split() if p not in ignorar and len(p) >= 3]
    return all(p in texto_norm for p in partes)


def ler_comprovante_url(image_url: str, nome: str = "") -> dict:
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

        encontrado = valor != "N/A"

        return {
            "encontrado": encontrado,
            "valor": valor,
            "pagador": pagador,
            "banco": banco,
            "texto": texto[:500],
        }

    except Exception as e:
        logger.error(f"OCR falhou: {type(e).__name__}: {str(e)[:100]}")
        return {"encontrado": False, "erro": str(e)[:100]}
