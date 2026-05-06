import re
import imaplib
import email
import unicodedata
import logging
import threading
import time
from datetime import datetime, timedelta, date
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
    "pagamento recebido via pix",
    "pagamento recebido",
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


def _limpar_html(text):
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&[a-zA-Z0-9#]+;', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _normalizar(text):
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower().strip()


def _detectar_banco(content):
    cl = content.lower()
    for banco, patterns in BANCOS_PATTERNS.items():
        for p in patterns:
            if re.search(p, cl, re.IGNORECASE):
                return banco
    return "Desconhecido"


def _extrair_valor(content):
    for padrao in [
        r'valor\s*creditado\s*[:\s]*R\$\s*([0-9]+(?:[\.,][0-9]{1,2})?)',
        r'valor\s*[:\-]\s*R\$\s*([0-9]+(?:[\.,][0-9]{1,2})?)',
        r'R\$\s*([0-9]+(?:[\.,][0-9]{1,2})?)',
    ]:
        m = re.search(padrao, content, re.IGNORECASE)
        if m:
            v = m.group(1).strip().replace('.', ',')
            if ',' not in v:
                v += ',00'
            elif len(v.split(',')[1]) == 1:
                v += '0'
            return v
    return "N/A"


def _extrair_pagador(content):
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


def _is_email_pix(subject):
    return any(p in _normalizar(subject) for p in ASSUNTOS_PIX)


def _match_nomes(nome_cmd, nome_email):
    ignorar = {'de', 'da', 'do', 'dos', 'das', 'e'}
    partes_cmd = [p for p in nome_cmd.split() if p not in ignorar and len(p) >= 3]
    partes_email = nome_email.split()
    return all(any(p in pe or pe.startswith(p) for pe in partes_email) for p in partes_cmd)


def _decode_header_str(value):
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


def _get_body(msg):
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() in ("text/plain", "text/html"):
                try:
                    body += part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="ignore")
                except Exception:
                    pass
    else:
        try:
            body = msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", errors="ignore")
        except Exception:
            pass
    return body


class PersistentIMAPConnection:
    """Mantém uma conexão IMAP aberta permanentemente com keepalive."""

    def __init__(self, config, log_fn=None):
        self.config = config
        self.log_fn = log_fn
        self._mail = None
        self._lock = threading.Lock()
        self._stop = False
        self._connected = False
        self._keepalive_thread = threading.Thread(target=self._keepalive_loop, daemon=True)
        self._keepalive_thread.start()

    def _log(self, msg):
        if self.log_fn:
            self.log_fn(msg)
        logger.info(msg)

    def _conectar(self):
        try:
            mail = imaplib.IMAP4_SSL(self.config["imap_server"])
            mail.socket().settimeout(30)
            mail.login(self.config["email_user"], self.config["email_pass"])
            mail.select("INBOX")
            self._mail = mail
            self._connected = True
            self._log("\u2705 IMAP conexao persistente estabelecida")
            return True
        except Exception as e:
            self._log(f"\u26a0\ufe0f Falha conexao IMAP: {type(e).__name__}: {str(e)[:100]}")
            self._connected = False
            return False

    def _keepalive_loop(self):
        """Mantém a conexão viva com NOOP a cada 5 minutos."""
        while not self._stop:
            time.sleep(300)  # 5 minutos
            if self._stop:
                break
            with self._lock:
                if self._mail and self._connected:
                    try:
                        self._mail.noop()
                    except Exception:
                        self._connected = False
                        self._log("\u26a0\ufe0f Conexao IMAP perdida, reconectando...")
                        self._conectar()

    def _garantir_conexao(self):
        """Garante que a conexão está ativa, reconecta se necessário."""
        if not self._connected or self._mail is None:
            return self._conectar()
        try:
            self._mail.noop()
            return True
        except Exception:
            self._connected = False
            return self._conectar()

    def buscar(self, nome, log_fn=None):
        def log(msg):
            if log_fn:
                log_fn(msg)

        nome_busca = _normalizar(nome).lower().strip()

        with self._lock:
            if not self._garantir_conexao():
                log("\u26a0\ufe0f Sem conexao IMAP")
                return None

            try:
                hoje = date.today().strftime("%d-%b-%Y")
                cutoff = datetime.now() - timedelta(minutes=3)

                _, data = self._mail.search(None, f'(SINCE "{hoje}" FROM "nubank.com.br")')
                uids_nubank = data[0].split() if data and data[0] else []

                _, data = self._mail.search(None, f'(SINCE "{hoje}" FROM "picpay.com")')
                uids_picpay = data[0].split() if data and data[0] else []

                uids = list(set(uids_nubank + uids_picpay))
                uids_sorted = sorted(uids, key=lambda x: int(x), reverse=True)[:20]
                log(f"\U0001f4ec {len(uids_sorted)} emails recentes (Nubank: {len(uids_nubank)}, PicPay: {len(uids_picpay)})")

                if not uids_sorted:
                    log(f"\u274c Nenhum pix de '{nome_busca}' encontrado")
                    return None

                uid_set = b",".join(uids_sorted)
                _, msgs_data = self._mail.fetch(uid_set, "(RFC822)")

                emails_parsed = []
                for i in range(0, len(msgs_data), 2):
                    try:
                        if isinstance(msgs_data[i], tuple):
                            msg = email.message_from_bytes(msgs_data[i][1])
                            subject = _decode_header_str(msg.get("Subject", ""))
                            if not _is_email_pix(subject):
                                continue
                            try:
                                from email.utils import parsedate_to_datetime
                                ts = parsedate_to_datetime(msg.get("Date", "")).replace(tzinfo=None)
                                if ts < cutoff:
                                    continue
                            except Exception:
                                pass
                            content = f"{subject} {_get_body(msg)}"
                            pagador = _extrair_pagador(content)
                            pagador_norm = _normalizar(pagador).lower().strip()
                            valor = _extrair_valor(content)
                            banco = _detectar_banco(content)
                            emails_parsed.append((pagador, pagador_norm, valor, banco))
                    except Exception:
                        continue

                for pagador, pagador_norm, valor, banco in emails_parsed:
                    log(f"\U0001f4b0 pagador='{pagador_norm}' | R${valor}")
                    if pagador_norm and nome_busca and (nome_busca in pagador_norm or _match_nomes(nome_busca, pagador_norm)):
                        log(f"\u2705 MATCH: '{pagador_norm}'")
                        return {"valor": valor, "banco": banco, "pagador": pagador}

                log(f"\u274c Nenhum pix de '{nome_busca}' encontrado")
                return None

            except Exception as e:
                log(f"\u26a0\ufe0f Erro busca: {type(e).__name__}: {str(e)[:150]}")
                self._connected = False
                return None

    def stop(self):
        self._stop = True
        try:
            if self._mail:
                self._mail.logout()
        except Exception:
            pass


class IMAPManager:
    def __init__(self):
        self.connections = {}
        self.configs = {}

    def get_cache(self, user_id, config):
        self.configs[user_id] = config
        if user_id not in self.connections:
            self.connections[user_id] = PersistentIMAPConnection(config)
        return self

    def set_log(self, user_id, log_fn):
        if user_id in self.connections:
            self.connections[user_id].log_fn = lambda msg: log_fn(user_id, msg)

    def stop_cache(self, user_id):
        if user_id in self.connections:
            self.connections[user_id].stop()
            del self.connections[user_id]
        self.configs.pop(user_id, None)

    def get_global_stats(self):
        return {"active_caches": len(self.connections)}

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


def buscar_pagamento_imap(config, nome, log_fn=None):
    """Busca usando conexão persistente."""
    for conn in imap_manager.connections.values():
        if conn.config.get("email_user") == config.get("email_user"):
            return conn.buscar(nome, log_fn)

    # Fallback: cria conexão temporária
    conn_temp = PersistentIMAPConnection(config)
    time.sleep(2)  # aguarda conectar
    resultado = conn_temp.buscar(nome, log_fn)
    conn_temp.stop()
    return resultado
