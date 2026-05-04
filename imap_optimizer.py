import re
import unicodedata
import logging
from datetime import date
from typing import Optional
from imap_tools import MailBox, AND

logger = logging.getLogger(__name__)

BANCOS_PATTERNS = {
    "Nubank":          [r"nubank"],
    "PicPay":          [r"picpay"],
    "Itau":            [r"ita[u\u00fa]"],
    "Bradesco":        [r"bradesco"],
    "Santander":       [r"santander"],
    "Caixa":           [r"caixa", r"cef\.gov\.br"],
    "Inter":           [r"banco\s*inter|bancointer"],
    "Mercado Pago":    [r"mercado\s*pago"],
    "PagSeguro":       [r"pagseguro|pagbank"],
    "C6 Bank":         [r"c6\s*bank"],
    "Next":            [r"\bnext\b"],
    "Neon":            [r"\bneon\b"],
    "BTG":             [r"\bbtg\b"],
    "Stone":           [r"\bstone\b"],
    "Sicoob":          [r"sicoob"],
    "Sicredi":         [r"sicredi"],
    "Banco do Brasil": [r"banco\s*do\s*brasil"],
    "Original":        [r"banco\s*original"],
    "Pan":             [r"banco\s*pan"],
    "Will Bank":       [r"will\s*bank"],
}

NOME_PADROES = [
    r"voc[e\u00ea]\s+recebeu\s+um\s+pix\s+de\s+(.+?)\s+e\s+o\s+valor",
    r"transfer[e\u00ea]ncia\s+de\s+(.+?)\s+e\s+o\s+valor",
    r"voc[e\u00ea]\s+recebeu.*?de\s+([A-Za-z\u00C0-\u00FF][A-Za-z\u00C0-\u00FF\-\'\s]{2,60})",
    r"pix\s+de\s+([A-Za-z\u00C0-\u00FF][A-Za-z\u00C0-\u00FF\-\'\s]{2,60})",
    r"recebido\s+de\s+([A-Za-z\u00C0-\u00FF][A-Za-z\u00C0-\u00FF\-\'\s]{2,60})",
    r"pagador\s*[:\s]+([A-Za-z\u00C0-\u00FF][A-Za-z\u00C0-\u00FF\-\'\s]{2,60})",
    r"remetente\s*[:\s]+([A-Za-z\u00C0-\u00FF][A-Za-z\u00C0-\u00FF\-\'\s]{2,60})",
]


def _limpar_html(text: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&[a-zA-Z0-9#]+;', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _normalizar(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower().strip()


def _detectar_banco(content: str) -> str:
    cl = content.lower()
    for banco, patterns in BANCOS_PATTERNS.items():
        for p in patterns:
            if re.search(p, cl, re.IGNORECASE):
                return banco
    return "Desconhecido"


def _extrair_valor(content: str) -> str:
    padroes = [
        r'valor\s*creditado\s*[:\s]*R\$\s*([0-9]+(?:[\.,][0-9]{1,2})?)',
        r'valor\s*[:\-]\s*R\$\s*([0-9]+(?:[\.,][0-9]{1,2})?)',
        r'R\$\s*([0-9]+(?:[\.,][0-9]{1,2})?)',
    ]
    for padrao in padroes:
        m = re.search(padrao, content, re.IGNORECASE)
        if m:
            valor = m.group(1).strip().replace('.', ',')
            if ',' not in valor:
                valor += ',00'
            elif len(valor.split(',')[1]) == 1:
                valor += '0'
            return valor
    return "N/A"


def _extrair_pagador(content: str) -> str:
    corpo = _limpar_html(content)
    for padrao in NOME_PADROES:
        m = re.search(padrao, corpo, flags=re.IGNORECASE)
        if m:
            nome = m.group(1).strip()
            nome = re.split(r'\s+e\s+o\s+|\s+via\s+|\s+no\s+valor|[,;\.]', nome, flags=re.IGNORECASE)[0].strip()
            palavras = [p for p in nome.split() if re.match(r'^[A-Za-z\u00C0-\u00FF\-]+$', p) and len(p) >= 2]
            if len(palavras) >= 2:
                return ' '.join(palavras).title()
    return "Desconhecido"


def buscar_pagamento_imap(config: dict, nome: str, log_fn=None) -> Optional[dict]:
    def log(msg):
        if log_fn:
            log_fn(msg)
        logger.info(msg)

    nome_busca = _normalizar(nome).lower().strip()
    since = date.today()

    try:
        mb = MailBox(config["imap_server"], timeout=15)
        mb.login(config["email_user"], config["email_pass"], initial_folder="INBOX")
        log("\u2705 IMAP conectado")
    except Exception as e:
        log(f"\u26a0\ufe0f Falha IMAP: {type(e).__name__}: {str(e)[:150]}")
        return None

    resultado = None
    try:
        for pasta in ["INBOX", "[Gmail]/All Mail"]:
            try:
                mb.folder.set(pasta)
                msgs = list(mb.fetch(AND(date_gte=since), mark_seen=False, limit=30))
                log(f"\U0001f4c2 '{pasta}': {len(msgs)} emails hoje")
                for msg in reversed(msgs):
                    content = f"{msg.subject or ''} {msg.text or ''} {msg.html or ''}"
                    pagador = _extrair_pagador(content)
                    pagador_norm = _normalizar(pagador).lower().strip()
                    log(f"\U0001f4e7 '{msg.subject}' | pagador='{pagador_norm}'")
                    if pagador_norm and nome_busca and nome_busca in pagador_norm:
                        valor = _extrair_valor(content)
                        banco = _detectar_banco(content)
                        log(f"\u2705 MATCH: '{pagador_norm}' | R${valor} | {banco}")
                        resultado = {"valor": valor, "banco": banco, "pagador": pagador}
                        break
                break
            except Exception as e:
                log(f"\u26a0\ufe0f '{pasta}' falhou: {type(e).__name__}: {str(e)[:80]}")
                continue
    finally:
        try:
            mb.logout()
        except Exception:
            pass

    if not resultado:
        log(f"\u274c Nenhum pagamento de '{nome_busca}' encontrado hoje")

    return resultado


class IMAPManager:
    def __init__(self):
        self.configs: dict[int, dict] = {}

    def get_cache(self, user_id: int, config: dict):
        self.configs[user_id] = config
        return self

    def stop_cache(self, user_id: int):
        self.configs.pop(user_id, None)

    def get_global_stats(self) -> dict:
        return {"active_caches": len(self.configs)}

    class _Stats:
        total_emails = 0
    stats = _Stats()

    @property
    def _log(self):
        return None

    @_log.setter
    def _log(self, value):
        pass


imap_manager = IMAPManager()
