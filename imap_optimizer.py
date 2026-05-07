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
            nome = re.split(r'\s+e\s+o\s+|\s+via\s+|\s+no\s+valor|\s+valor\s+enviado|\s+enviou|[,;\.]', nome, flags=re.IGNORECASE)[0].strip()
            palavras = [p for p in nome.split() if re.match(r'^[A-Za-z\u00C0-\u00FF\-]+$', p) and len(p) >= 2]
            if len(palavras) >= 2:
                return ' '.join(palavras).title()
    return "Desconhecido"


def _is_email_pix(subject):
    return any(p in _normalizar(subject) for p in ASSUNTOS_PIX)


def _match_nomes(nome_cmd, nome_email):
    ignorar = {'de', 'da', 'do', 'dos', 'das', 'e'}
    partes_cmd   = [p for p in nome_cmd.split()   if p not in ignorar and len(p) >= 3]
    partes_email = [p for p in nome_email.split() if p not in ignorar and len(p) >= 3]

    if not partes_cmd or not partes_email:
        return False

    def _batem(a, b):
        if a == b:
            return True
        # so aceita prefixo se a palavra menor tiver pelo menos 5 chars
        menor, maior = (a, b) if len(a) <= len(b) else (b, a)
        if len(menor) < 5:
            return False
        return maior.startswith(menor)

    primeiro = partes_cmd[0]
    ultimo = partes_cmd[-1]

    tem_primeiro = any(_batem(primeiro, pe) for pe in partes_email)

    if len(partes_cmd) == 1:
        return tem_primeiro

    tem_ultimo = any(_batem(ultimo, pe) for pe in partes_email)
    if tem_primeiro and tem_ultimo:
        return True

    matches = sum(1 for pc in partes_cmd if any(_batem(pc, pe) for pe in partes_email))
    return matches >= 2


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

    def __init__(self, config, log_fn=None):
        self.config = config
        self.log_fn = log_fn
        self._mail = None
        self._lock = threading.Lock()
        self._stop = False
        self._connected = False
        self._uids_usados = set()
        # Cache em memoria: uid -> {pagador, pagador_norm, valor, banco}
        self._cache = {}
        self._cache_lock = threading.Lock()
        self._keepalive_thread = threading.Thread(target=self._keepalive_loop, daemon=True)
        self._keepalive_thread.start()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

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
        while not self._stop:
            time.sleep(300)
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

    def _monitor_loop(self):
        """Atualiza cache de e-mails PIX do dia a cada 5s."""
        time.sleep(10)
        while not self._stop:
            try:
                with self._lock:
                    if not self._garantir_conexao():
                        time.sleep(5)
                        continue
                    hoje = date.today().strftime("%d-%b-%Y")
                    _, data = self._mail.search(None, f'(SINCE "{hoje}")')
                    uids_all = data[0].split() if data and data[0] else []
                    with self._cache_lock:
                        novos = [u for u in uids_all if u.decode() not in self._cache]
                    if novos:
                        uid_set = b",".join(novos)
                        _, msgs_data = self._mail.fetch(uid_set, "(RFC822)")
                        for i in range(0, len(msgs_data), 2):
                            try:
                                if not isinstance(msgs_data[i], tuple):
                                    continue
                                msg = email.message_from_bytes(msgs_data[i][1])
                                subject = _decode_header_str(msg.get("Subject", ""))
                                if not _is_email_pix(subject):
                                    continue
                                content = f"{subject} {_get_body(msg)}"
                                pagador = _extrair_pagador(content)
                                valor = _extrair_valor(content)
                                banco = _detectar_banco(content)
                                uid_str = novos[i // 2].decode()
                                with self._cache_lock:
                                    self._cache[uid_str] = {
                                        "pagador": pagador,
                                        "pagador_norm": _normalizar(pagador),
                                        "valor": valor,
                                        "banco": banco,
                                    }
                                print(f"\U0001f4ec NOVO PIX | {pagador} | R${valor} | {banco}", flush=True)
                            except Exception:
                                continue
            except Exception:
                pass
            time.sleep(5)

    def _garantir_conexao(self):
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

        # Busca instantanea no cache
        with self._cache_lock:
            cache_snapshot = list(self._cache.items())

        log(f"\U0001f4ec {len(cache_snapshot)} emails no cache")

        for uid, entry in cache_snapshot:
            if uid in self._uids_usados:
                log(f"\u23e9 Ignorado (ja usado): UID {uid}")
                continue
            pagador_norm = entry["pagador_norm"]
            log(f"\U0001f4b0 pagador='{pagador_norm}' | R${entry['valor']} | {entry['banco']}")
            if pagador_norm and nome_busca and (nome_busca in pagador_norm or _match_nomes(nome_busca, pagador_norm)):
                log(f"\u2705 MATCH: '{pagador_norm}'")
                self._uids_usados.add(uid)
                return {"valor": entry["valor"], "banco": entry["banco"], "pagador": entry["pagador"]}

        log(f"\u274c Nenhum pix de '{nome_busca}' encontrado")
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
    for conn in imap_manager.connections.values():
        if conn.config.get("email_user") == config.get("email_user"):
            return conn.buscar(nome, log_fn)

    conn_temp = PersistentIMAPConnection(config)
    time.sleep(2)
    resultado = conn_temp.buscar(nome, log_fn)
    conn_temp.stop()
    return resultado
