import re
import imaplib
import email
import unicodedata
import logging
from datetime import date, datetime, timedelta
from typing import Optional
from email.header import decode_header

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
    "Sicoob":          [r"sicoob"],
    "Sicredi":         [r"sicredi"],
    "Banco do Brasil": [r"banco\s*do\s*brasil"],
}

ASSUNTOS_PIX = [
    "recebeu uma transfer",
    "recebeu um pix",
    "transferencia via pix",
    "pix recebido",
    "recebemos sua transfer",
]

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


def _is_email_pix(subject: str) -> bool:
    s = _normalizar(subject)
    return any(p in s for p in ASSUNTOS_PIX)


def _match_nomes(nome_cmd: str, nome_email: str) -> bool:
    ignorar = {'de', 'da', 'do', 'dos', 'das', 'e'}
    partes_cmd = [p for p in nome_cmd.split() if p not in ignorar and len(p) >= 3]
    partes_email = nome_email.split()
    return all(
        any(p in pe or pe.startswith(p) for pe in partes_email)
        for p in partes_cmd
    )


def _decode_header_str(value: str) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    result = []
    for part, enc in parts:
        if isinstance(part, bytes):
            result.append(part.decode(enc or "utf-8", errors="ignore"))
        else:
            result.append(part)
    return " ".join(result)


def _get_body(msg) -> str:
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct in ("text/plain", "text/html"):
                try:
                    body += part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="ignore"
                    )
                except Exception:
                    pass
    else:
        try:
            body = msg.get_payload(decode=True).decode(
                msg.get_content_charset() or "utf-8", errors="ignore"
            )
        except Exception:
            pass
    return body


def buscar_pagamento_imap(config: dict, nome: str, log_fn=None) -> Optional[dict]:
    def log(msg):
        if log_fn:
            log_fn(msg)
        logger.info(msg)

    nome_busca = _normalizar(nome).lower().strip()

    for tentativa in range(1, 4):  # 3 tentativas
        try:
            mail = imaplib.IMAP4_SSL(config["imap_server"])
            mail.socket().settimeout(30)
            mail.login(config["email_user"], config["email_pass"])
            mail.select("INBOX")
            log(f"\u2705 IMAP conectado (tentativa {tentativa})")
            break
        except Exception as e:
            log(f"\u26a0\ufe0f Falha IMAP tentativa {tentativa}: {type(e).__name__}: {str(e)[:100]}")
            if tentativa == 3:
                return None
            import time
            time.sleep(3)
            continue

    resultado = None
    try:
        # Data de hoje no formato IMAP
        hoje = date.today().strftime("%d-%b-%Y")
        cutoff = datetime.now() - timedelta(hours=2)

        # Busca todos os emails do Nubank de hoje (muito menos que 500)
        _, data = mail.search(None, f'(SINCE "{hoje}" FROM "nubank.com.br")')
        uids = data[0].split() if data and data[0] else []
        log(f"\U0001f4ec INBOX: {len(uids)} emails do Nubank hoje")

        # Ordena do mais recente para o mais antigo
        uids_sorted = sorted(uids, key=lambda x: int(x), reverse=True)

        for uid in uids_sorted:
            _, msg_data = mail.fetch(uid, "(RFC822)")
            if not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            subject = _decode_header_str(msg.get("Subject", ""))
            date_str = msg.get("Date", "")

            # Filtra por hora
            try:
                from email.utils import parsedate_to_datetime
                msg_dt = parsedate_to_datetime(date_str).replace(tzinfo=None)
                if msg_dt < cutoff:
                    continue
            except Exception:
                pass

            if not _is_email_pix(subject):
                continue

            body = _get_body(msg)
            content = f"{subject} {body}"
            pagador = _extrair_pagador(content)
            pagador_norm = _normalizar(pagador).lower().strip()
            valor = _extrair_valor(content)
            banco = _detectar_banco(content)

            log(f"\U0001f4b0 Pix | pagador='{pagador_norm}' | R${valor} | {banco}")

            if pagador_norm and nome_busca and (nome_busca in pagador_norm or _match_nomes(nome_busca, pagador_norm)):
                log(f"\u2705 MATCH: '{pagador_norm}' contém '{nome_busca}'")
                resultado = {"valor": valor, "banco": banco, "pagador": pagador}
                break

        if not resultado:
            log(f"\u274c Nenhum pix de '{nome_busca}' encontrado")

    except Exception as e:
        log(f"\u26a0\ufe0f Erro na busca: {type(e).__name__}: {str(e)[:150]}")
    finally:
        try:
            mail.logout()
        except Exception:
            pass

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
