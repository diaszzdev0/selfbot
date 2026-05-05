import re
import unicodedata
import logging
import os
import requests

logger = logging.getLogger(__name__)

OCR_API_URL = "https://api.ocr.space/parse/image"
OCR_API_KEY = os.getenv("OCR_API_KEY", "helloworld")


def _normalizar(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower().strip()


def _extrair_valor_texto(text: str) -> str:
    padroes = [
        r'R\$\s*([0-9]+(?:[.,][0-9]{1,2})?)',
        r'valor\s*[:\-]?\s*([0-9]+(?:[.,][0-9]{1,2})?)',
    ]
    for padrao in padroes:
        m = re.search(padrao, text, re.IGNORECASE)
        if m:
            v = m.group(1).strip().replace('.', ',')
            if ',' not in v:
                v += ',00'
            elif len(v.split(',')[1]) == 1:
                v += '0'
            return v
    return "N/A"


def _match_nomes(nome_cmd: str, texto_norm: str) -> bool:
    ignorar = {'de', 'da', 'do', 'dos', 'das', 'e'}
    partes = [p for p in nome_cmd.split() if p not in ignorar and len(p) >= 3]
    return all(p in texto_norm for p in partes)


def ler_comprovante_url(image_url: str, nome: str) -> dict:
    """
    Lê comprovante via URL usando OCR.space.
    Retorna dict com 'encontrado', 'valor', 'texto' ou None se falhar.
    """
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
            logger.warning(f"OCR erro: {data.get('ErrorMessage')}")
            return {"encontrado": False, "erro": data.get("ErrorMessage", "Erro OCR")}

        texto = ""
        for result in data.get("ParsedResults", []):
            texto += result.get("ParsedText", "") + " "

        texto_norm = _normalizar(texto)
        nome_norm = _normalizar(nome)

        encontrado = _match_nomes(nome_norm, texto_norm)
        valor = _extrair_valor_texto(texto)

        return {
            "encontrado": encontrado,
            "valor": valor,
            "texto": texto[:500],
            "nome_encontrado": encontrado,
        }

    except Exception as e:
        logger.error(f"OCR falhou: {type(e).__name__}: {str(e)[:100]}")
        return {"encontrado": False, "erro": str(e)[:100]}


async def ler_comprovante_discord(attachment, nome: str) -> dict:
    """Lê comprovante de um attachment do Discord."""
    url = attachment.url
    content_type = getattr(attachment, 'content_type', '') or ''

    # Aceita imagens e PDFs
    if not any(t in content_type for t in ['image', 'pdf']):
        ext = url.split('.')[-1].lower().split('?')[0]
        if ext not in ('png', 'jpg', 'jpeg', 'gif', 'webp', 'pdf'):
            return {"encontrado": False, "erro": "Formato não suportado"}

    return ler_comprovante_url(url, nome)
