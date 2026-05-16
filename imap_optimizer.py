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
        sep = "=" * 60
        linhas = [
            sep,
            f"[{ts}] [PGTO  ] 💰 PIX RECEBIDO NO E-MAIL",
            f"[{ts}] [PGTO  ]    └─ Pagador : {entry['pagador']}",
            f"[{ts}] [PGTO  ]    └─ Valor   : R$ {entry['valor']}",
            f"[{ts}] [PGTO  ]    └─ Banco   : {entry['banco']}",
            f"[{ts}] [PGTO  ]    └─ UID     : {entry.get('uid', 'N/A')}",
            sep,
        ]
        path = os.path.join(LOG_DIR, f"user_{user_id}.log")
        with open(path, "a", encoding="utf-8", errors="replace") as f:
            f.write("\n".join(linhas) + "\n")
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
    "voce recebeu",
    "você recebeu",
    "credito em conta",
    "crédito em conta",
    "transferencia recebida",
    "transferência recebida",
    "deposito recebido",
    "depósito recebido",
    "entrada via pix",
    "pix efetuado",
    "pix realizado",
    "recebimento pix",
    "recebimento de pix",
    "novo pix",
    "pix confirmado",
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


def _nova_conexao(config):
    mail = imaplib.IMAP4_SSL(config["imap_server"])
    mail.socket().settimeout(20)
    mail.login(config["email_user"], config["email_pass"])
    mail.select("INBOX")
    return mail


class PersistentIMAPConnection:
    """
    Uma conexao IMAP persistente por usuario.
    - Monitor: polling a cada 3s na mesma conexao, detecta PIX novos
    - Cache: armazena emails de hoje processados (uid -> entry)
    - Busca: consulta o cache primeiro (instantaneo), fallback na conexao se nao achar
    """

    def __init__(self, config, log_fn=None, user_id=None):
        self.config = config
        self.log_fn = log_fn
        self.user_id = user_id
        self._on_novo_pix = None
        self._stop = False
        self._uids_usados = set()
        self._carregar_uids_arquivo()
        # Cache de emails PIX de hoje: uid -> {pagador, pagador_norm, valor, banco, data}
        self._cache = {}
        self._cache_lock = threading.Lock()
        self._monitor_connected = False
        self._search_connected = False
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        self._connect_errors = []  # buffer de erros antes do log_fn estar pronto

    def _log(self, msg):
        if self.log_fn:
            # drena erros acumulados antes do log_fn estar pronto
            while self._connect_errors:
                self.log_fn(self._connect_errors.pop(0))
            self.log_fn(msg)
        else:
            self._connect_errors.append(msg)

    def _processar_email(self, uid_bytes, mail):
        """Processa um email e adiciona ao cache. Retorna entry ou None."""
        uid_str = uid_bytes.decode() if isinstance(uid_bytes, bytes) else str(uid_bytes)
        try:
            _, msgs_data = mail.fetch(uid_bytes if isinstance(uid_bytes, bytes) else uid_str.encode(), "(RFC822)")
            raw = next((x[1] for x in msgs_data if isinstance(x, tuple)), None)
            if not raw:
                with self._cache_lock:
                    self._cache[uid_str] = None
                return None
            msg = email.message_from_bytes(raw)
            subject = _decode_header_str(msg.get("Subject", ""))
            self._log(f"📧 UID {uid_str} | Assunto: '{subject}'")
            if not _is_email_pix(subject):
                self._log(f"⏭️ UID {uid_str} ignorado (assunto não é PIX)")
                with self._cache_lock:
                    self._cache[uid_str] = None
                return None
            try:
                from email.utils import parsedate_to_datetime
                email_date = parsedate_to_datetime(msg.get("Date", "")).date()
                if email_date < date.today():
                    with self._cache_lock:
                        self._cache[uid_str] = None
                    return None
            except Exception:
                pass
            content = f"{subject} {_get_body(msg)}"
            pagador = _extrair_pagador(content)
            entry = {
                "pagador": pagador,
                "pagador_norm": _normalizar(pagador),
                "valor": _extrair_valor(content),
                "banco": _detectar_banco(content),
                "uid": uid_str,
            }
            self._log(f"✅ UID {uid_str} processado | pagador='{pagador}' | valor={entry['valor']} | banco={entry['banco']}")
            with self._cache_lock:
                self._cache[uid_str] = entry
            return entry
        except Exception as e:
            self._log(f"⚠️ Erro ao processar UID {uid_str}: {type(e).__name__}: {e}")
            return None

    def _monitor_loop(self):
        """Conexao persistente: polling a cada 3s, popula cache e notifica PIX novos."""
        time.sleep(5)  # aguarda log_fn ser setado pelo bot_logic
        uids_notificados = set(self._uids_usados)
        inicializado = False
        mail = None

        def conectar():
            nonlocal mail
            try:
                if mail:
                    try: mail.logout()
                    except Exception: pass
                mail = _nova_conexao(self.config)
                self._monitor_connected = True
                self._log(f"✅ Monitor IMAP conectado ({self.config.get('email_user', '?')})")
                return True
            except Exception as e:
                self._monitor_connected = False
                err = f"{type(e).__name__}: {e}"
                self._log(f"❌ Monitor IMAP falha ao conectar: {err}")
                print(f"[MONITOR {self.user_id}] falha: {err}", flush=True)
                return False

        while not self._stop:
            try:
                if mail is None:
                    if not conectar():
                        time.sleep(10)
                        continue

                hoje_str = date.today().strftime("%d-%b-%Y")
                try:
                    mail.select("INBOX")
                    _, data = mail.search(None, f'(SINCE "{hoje_str}" SUBJECT "pix")')
                    uids = data[0].split() if data and data[0] else []
                    # fallback sem filtro de assunto se nao achou nada
                    if not uids:
                        _, data = mail.search(None, f'(SINCE "{hoje_str}")')
                        uids = data[0].split() if data and data[0] else []
                except Exception:
                    mail = None
                    continue

                if not inicializado:
                    # Primeira rodada: popula cache sem notificar
                    self._log(f"📬 Inicializando cache com {len(uids)} emails de hoje")
                    for u in uids:
                        uid_str = u.decode()
                        uids_notificados.add(uid_str)
                        with self._cache_lock:
                            if uid_str not in self._cache:
                                self._processar_email(u, mail)
                    inicializado = True
                    time.sleep(3)
                    continue

                # Rodadas seguintes: processa novos e notifica
                for u in reversed(uids):
                    uid_str = u.decode()
                    with self._cache_lock:
                        ja_no_cache = uid_str in self._cache
                    if not ja_no_cache:
                        self._processar_email(u, mail)
                    if uid_str in uids_notificados:
                        continue
                    uids_notificados.add(uid_str)
                    with self._cache_lock:
                        entry = self._cache.get(uid_str)
                    if not entry:
                        continue
                    _escrever_log_usuario(self.user_id, entry)
                    if self._on_novo_pix:
                        try:
                            self._on_novo_pix(entry)
                        except Exception:
                            pass

            except Exception as e:
                self._monitor_connected = False
                mail = None
                self._log(f"⚠️ Monitor IMAP erro: {type(e).__name__}: {str(e)[:200]}")

            time.sleep(3)

    def buscar(self, nome, log_fn=None):
        """Busca no cache (instantaneo). Se nao achar, forca refresh na conexao do monitor."""
        def log(msg):
            if log_fn:
                log_fn(msg)

        nome_busca = _normalizar(nome).strip()

        # 1) Busca no cache (sem IO, instantaneo)
        with self._cache_lock:
            snapshot = sorted(
                [(uid, e) for uid, e in self._cache.items() if e],
                key=lambda x: int(x[0]) if x[0].isdigit() else 0,
                reverse=True
            )

        log(f"📬 {len(snapshot)} emails PIX no cache")

        for uid_str, entry in snapshot:
            if uid_str in self._uids_usados:
                continue
            pagador_norm = entry.get("pagador_norm", "")
            log(f"💰 UID {uid_str} | pagador='{pagador_norm}' | {entry.get('valor')}")
            if pagador_norm and nome_busca and (
                nome_busca in pagador_norm or _match_nomes(nome_busca, pagador_norm)
            ):
                log(f"✅ MATCH: '{entry['pagador']}'")
                self.marcar_uid_usado(uid_str)
                return {
                    "valor": entry["valor"],
                    "banco": entry["banco"],
                    "pagador": entry["pagador"],
                    "uid": uid_str,
                }

        # 2) Cache nao tem — abre conexao propria para buscar emails que o monitor ainda nao processou
        log("🔄 Nao encontrado no cache, buscando direto no IMAP...")
        try:
            mail2 = _nova_conexao(self.config)
            hoje_str = date.today().strftime("%d-%b-%Y")
            mail2.select("INBOX")
            _, data = mail2.search(None, f'(SINCE "{hoje_str}" SUBJECT "pix")')
            uids = data[0].split() if data and data[0] else []
            if not uids:
                _, data = mail2.search(None, f'(SINCE "{hoje_str}")')
                uids = data[0].split() if data and data[0] else []
            log(f"📬 {len(uids)} emails encontrados no IMAP direto")
            with self._cache_lock:
                novos = [u for u in uids if u.decode() not in self._cache]
            log(f"🔄 {len(novos)} emails novos para processar")
            for u in novos:
                self._processar_email(u, mail2)
            try:
                mail2.logout()
            except Exception:
                pass
        except Exception as e:
            import traceback as _tb
            log(f"⚠️ Refresh IMAP: {type(e).__name__}: {e}")
            log(f"⚠️ Detalhe: {_tb.format_exc().splitlines()[-1]}")

        # 3) Tenta cache novamente apos refresh
        with self._cache_lock:
            snapshot2 = sorted(
                [(uid, e) for uid, e in self._cache.items() if e],
                key=lambda x: int(x[0]) if x[0].isdigit() else 0,
                reverse=True
            )
        for uid_str, entry in snapshot2:
            if uid_str in self._uids_usados:
                continue
            pagador_norm = entry.get("pagador_norm", "")
            if pagador_norm and nome_busca and (
                nome_busca in pagador_norm or _match_nomes(nome_busca, pagador_norm)
            ):
                log(f"✅ MATCH (refresh): '{entry['pagador']}'")
                self.marcar_uid_usado(uid_str)
                return {
                    "valor": entry["valor"],
                    "banco": entry["banco"],
                    "pagador": entry["pagador"],
                    "uid": uid_str,
                }

        log(f"❌ Nao encontrado: '{nome}'")
        return None

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
    if user_id is not None and user_id in imap_manager.connections:
        return imap_manager.connections[user_id].buscar(nome, log_fn)

    # Fallback por email/config
    target_user = str(config.get("email_user", "")).strip().lower()
    target_pass = str(config.get("email_pass", "")).strip()
    target_server = str(config.get("imap_server", "")).strip().lower()
    for uid, conn in imap_manager.connections.items():
        if uid == user_id:
            continue
        if (str(conn.config.get("email_user", "")).strip().lower() == target_user and
                str(conn.config.get("email_pass", "")).strip() == target_pass and
                str(conn.config.get("imap_server", "")).strip().lower() == target_server):
            return conn.buscar(nome, log_fn)

    if log_fn:
        log_fn(f"❌ Nenhuma conexão IMAP ativa para '{target_user}'")
    return None
