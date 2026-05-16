import re
import imaplib
import email
import unicodedata
import os
import threading
import time
from datetime import datetime, date
from email.header import decode_header

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)


def _escrever_log_usuario(user_id, entry):
    if not user_id:
        return
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        linha = (
            f"[{ts}] [PGTO  ] 💰 PIX DETECTADO | "
            f"pagador='{entry['pagador']}' | "
            f"R${entry['valor']} | "
            f"{entry['banco']}"
        )
        path = os.path.join(LOG_DIR, f"user_{user_id}.log")
        with open(path, "a", encoding="utf-8", errors="replace") as f:
            f.write(linha + "\n")
            f.flush()
    except Exception:
        pass


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


def _buscar_direto_imap(config, nome, log_fn=None, uids_usados: set = None, mail_conn=None):
    """Busca emails de hoje no IMAP e procura o pagador."""
    def log(msg):
        if log_fn:
            log_fn(msg)

    if uids_usados is None:
        uids_usados = set()

    nome_busca = _normalizar(nome).strip()
    hoje = date.today()
    hoje_str = hoje.strftime("%d-%b-%Y")
    fechar_ao_fim = mail_conn is None

    try:
        if mail_conn is None:
            mail_conn = imaplib.IMAP4_SSL(config["imap_server"])
            mail_conn.socket().settimeout(25)
            mail_conn.login(config["email_user"], config["email_pass"])

        mail_conn.select("INBOX")

        # Busca apenas emails de PIX de hoje usando filtro no servidor
        uids_pix = set()
        for assunto in ASSUNTOS_PIX:
            try:
                _, data = mail_conn.search(None, f'(SINCE "{hoje_str}" SUBJECT "{assunto}")')
                if data and data[0]:
                    for u in data[0].split():
                        uids_pix.add(u)
            except Exception:
                pass

        # Fallback: busca todos de hoje se filtro por assunto nao retornou nada
        if not uids_pix:
            _, data = mail_conn.search(None, f'(SINCE "{hoje_str}")')
            if data and data[0]:
                for u in data[0].split():
                    uids_pix.add(u)

        uids = sorted(uids_pix, key=lambda x: int(x), reverse=True)  # mais recente primeiro
        log(f"📬 {len(uids)} emails PIX de hoje")

        for uid_bytes in uids:
            uid_str = uid_bytes.decode() if isinstance(uid_bytes, bytes) else str(uid_bytes)
            if uid_str in uids_usados:
                continue
            uid_fetch = uid_bytes if isinstance(uid_bytes, bytes) else uid_str.encode()
            try:
                _, msgs_data = mail_conn.fetch(uid_fetch, "(RFC822)")
                raw = next((x[1] for x in msgs_data if isinstance(x, tuple)), None)
                if not raw:
                    continue
                msg = email.message_from_bytes(raw)
                subject = _decode_header_str(msg.get("Subject", ""))
                if not _is_email_pix(subject):
                    continue
                try:
                    from email.utils import parsedate_to_datetime
                    email_date = parsedate_to_datetime(msg.get("Date", "")).date()
                    if email_date < hoje:
                        continue
                except Exception:
                    pass
                content = f"{subject} {_get_body(msg)}"
                pagador = _extrair_pagador(content)
                pagador_norm = _normalizar(pagador)
                log(f"💰 UID {uid_str} | pagador='{pagador_norm}' | {_extrair_valor(content)}")
                if pagador_norm and nome_busca and (
                    nome_busca in pagador_norm or _match_nomes(nome_busca, pagador_norm)
                ):
                    log(f"✅ MATCH: '{pagador}'")
                    if fechar_ao_fim:
                        try: mail_conn.logout()
                        except Exception: pass
                    return {
                        "valor": _extrair_valor(content),
                        "banco": _detectar_banco(content),
                        "pagador": pagador,
                        "uid": uid_str,
                    }
            except Exception as e:
                log(f"⚠️ Erro UID {uid_str}: {type(e).__name__}")
                continue

        if fechar_ao_fim:
            try: mail_conn.logout()
            except Exception: pass
    except Exception as e:
        log(f"❌ Erro IMAP: {type(e).__name__}: {str(e)[:100]}")
        if fechar_ao_fim and mail_conn:
            try: mail_conn.logout()
            except Exception: pass

    return None


# ── Monitor em tempo real (thread por usuario) ──────────────────────────────

class PersistentIMAPConnection:
    """Mantém conexão IMAP persistente para buscas rápidas e monitor em tempo real."""

    def __init__(self, config, log_fn=None, user_id=None):
        self.config = config
        self.log_fn = log_fn
        self.user_id = user_id
        self._on_novo_pix = None
        self._stop = False
        self._uids_usados = set()
        self._carregar_uids_arquivo()
        self._cache = {}  # mantido para compatibilidade
        self._cache_lock = threading.Lock()
        self._monitor_connected = False
        self._search_connected = False
        # Conexão persistente para buscas (reusada, sem reconnect a cada pg)
        self._conn = None
        self._conn_lock = threading.Lock()
        self._conectar()
        # Monitor de PIX em tempo real
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

    def _conectar(self):
        try:
            if self._conn:
                try: self._conn.logout()
                except Exception: pass
            self._conn = imaplib.IMAP4_SSL(self.config["imap_server"])
            self._conn.socket().settimeout(25)
            self._conn.login(self.config["email_user"], self.config["email_pass"])
            self._conn.select("INBOX")
            self._search_connected = True
            return True
        except Exception as e:
            self._search_connected = False
            print(f"[IMAP CONN {self.user_id}] {type(e).__name__}: {e}", flush=True)
            return False

    def _garantir_conn(self):
        """Garante que a conexão está viva, reconecta se necessário."""
        try:
            self._conn.noop()
            return True
        except Exception:
            return self._conectar()

    def _log(self, msg):
        if self.log_fn:
            self.log_fn(msg)

    def _monitor_loop(self):
        """Monitor com conexao persistente e polling a cada 3s."""
        time.sleep(3)
        uids_vistos = set(self._uids_usados)
        inicializado = False
        mail = None

        def conectar():
            nonlocal mail
            try:
                if mail:
                    try: mail.logout()
                    except Exception: pass
                mail = imaplib.IMAP4_SSL(self.config["imap_server"])
                mail.socket().settimeout(20)
                mail.login(self.config["email_user"], self.config["email_pass"])
                mail.select("INBOX")
                self._monitor_connected = True
                return True
            except Exception as e:
                self._monitor_connected = False
                print(f"[MONITOR {self.user_id}] falha conexao: {e}", flush=True)
                return False

        while not self._stop:
            try:
                if mail is None:
                    if not conectar():
                        time.sleep(10)
                        continue

                hoje = date.today()
                hoje_str = hoje.strftime("%d-%b-%Y")

                try:
                    mail.select("INBOX")
                    _, data = mail.search(None, f'(SINCE "{hoje_str}")')
                    uids = data[0].split() if data and data[0] else []
                except Exception as e:
                    print(f"[MONITOR {self.user_id}] search falhou: {e} — reconectando", flush=True)
                    mail = None
                    continue

                if not inicializado:
                    for uid_bytes in uids:
                        uids_vistos.add(uid_bytes.decode())
                    inicializado = True
                    time.sleep(3)
                    continue

                novos = [u for u in reversed(uids) if u.decode() not in uids_vistos]

                for uid_bytes in novos:
                    uid_str = uid_bytes.decode()
                    uids_vistos.add(uid_str)
                    try:
                        _, msgs_data = mail.fetch(uid_bytes, "(RFC822)")
                        raw = next((x[1] for x in msgs_data if isinstance(x, tuple)), None)
                        if not raw:
                            continue
                        msg = email.message_from_bytes(raw)
                        subject = _decode_header_str(msg.get("Subject", ""))
                        if not _is_email_pix(subject):
                            continue
                        try:
                            from email.utils import parsedate_to_datetime
                            if parsedate_to_datetime(msg.get("Date", "")).date() < hoje:
                                continue
                        except Exception:
                            pass
                        content = f"{subject} {_get_body(msg)}"
                        pagador = _extrair_pagador(content)
                        entry = {
                            "pagador": pagador,
                            "valor": _extrair_valor(content),
                            "banco": _detectar_banco(content),
                            "uid": uid_str,
                        }
                        _escrever_log_usuario(self.user_id, entry)
                        if self._on_novo_pix:
                            try:
                                self._on_novo_pix(entry)
                            except Exception:
                                pass
                    except Exception:
                        continue

            except Exception as e:
                self._monitor_connected = False
                mail = None

            time.sleep(3)

    def buscar(self, nome, log_fn=None):
        """Abre conexao propria para busca — nunca bloqueia pelo monitor."""
        return _buscar_direto_imap(self.config, nome, log_fn, self._uids_usados)

    def marcar_uid_usado(self, uid: str):
        self._uids_usados.add(uid)
        try:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"uids_usados_{self._uid_chave()}.txt")
            with open(path, "a", encoding="utf-8") as f:
                f.write(uid + "\n")
        except Exception:
            pass

    def _carregar_uids_arquivo(self):
        try:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"uids_usados_{self._uid_chave()}.txt")
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        uid = line.strip()
                        if uid:
                            self._uids_usados.add(uid)
        except Exception:
            pass

    def _uid_chave(self):
        email_user = str(self.config.get("email_user", "")).strip().lower()
        if not email_user:
            return "default"
        safe = re.sub(r"[^a-z0-9._-]", "_", email_user)
        return safe or "default"

    def stop(self):
        self._stop = True


class IMAPManager:
    def __init__(self):
        self.connections = {}
        self.configs = {}

    def get_cache(self, user_id, config):
        self.configs[user_id] = config
        if user_id not in self.connections:
            self.connections[user_id] = PersistentIMAPConnection(config, user_id=user_id)
        return self

    def set_log(self, user_id, log_fn):
        if user_id in self.connections:
            self.connections[user_id].log_fn = lambda msg: log_fn(user_id, msg)

    def set_pix_callback(self, user_id, callback):
        if user_id in self.connections:
            self.connections[user_id]._on_novo_pix = callback

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


def buscar_pagamento_imap(config, nome, log_fn=None, user_id=None):
    def log(msg):
        if log_fn:
            log_fn(msg)

    uids_usados = set()
    if user_id is not None and user_id in imap_manager.connections:
        uids_usados = imap_manager.connections[user_id]._uids_usados

    resultado = _buscar_direto_imap(config, nome, log_fn, uids_usados)

    if resultado and user_id is not None and user_id in imap_manager.connections:
        imap_manager.connections[user_id].marcar_uid_usado(resultado["uid"])

    return resultado
