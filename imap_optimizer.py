import re
import imaplib
import email
import unicodedata
import logging
import threading
import time
from datetime import datetime, timedelta, date
from email.header import decode_header

import logging

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


def _nova_conexao_imap(config):
    mail = imaplib.IMAP4_SSL(config["imap_server"])
    mail.socket().settimeout(30)
    mail.login(config["email_user"], config["email_pass"])
    mail.select("INBOX")
    return mail


class PersistentIMAPConnection:

    def __init__(self, config, log_fn=None):
        self.config = config
        self.log_fn = log_fn
        self._on_novo_pix = None
        self._monitor_mail = None
        self._monitor_connected = False
        self._search_mail = None
        self._search_connected = False
        self._search_lock = threading.Lock()
        self._stop = False
        self._uids_usados = set()
        self._carregar_uids_arquivo()
        self._cache = {}  # sempre começa vazio, monitor recarrega emails de hoje
        self._cache_lock = threading.Lock()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

    def _log(self, msg):
        if self.log_fn:
            self.log_fn(msg)

    def _conectar_monitor(self):
        try:
            self._monitor_mail = _nova_conexao_imap(self.config)
            self._monitor_connected = True
            print("\u2705 IMAP monitor conectado", flush=True)
            return True
        except Exception as e:
            self._monitor_connected = False
            import logging
            print(f"\u26a0\ufe0f IMAP monitor falhou: {type(e).__name__}: {e}", flush=True)
            return False

    def _conectar_search(self):
        try:
            self._search_mail = _nova_conexao_imap(self.config)
            self._search_connected = True
            self._log("\u2705 IMAP conexao persistente estabelecida")
            return True
        except Exception as e:
            self._search_connected = False
            self._log(f"\u26a0\ufe0f Falha conexao IMAP: {type(e).__name__}: {str(e)[:100]}")
            return False

    def _processar_uid(self, uid_bytes, mail_conn):
        """Processa um UID e adiciona ao cache. Retorna entry ou None."""
        uid_str = uid_bytes.decode()
        try:
            _, msgs_data = mail_conn.fetch(uid_bytes, "(RFC822)")
            raw = next((x[1] for x in msgs_data if isinstance(x, tuple)), None)
            if raw is None:
                with self._cache_lock:
                    self._cache[uid_str] = None
                return None
            msg = email.message_from_bytes(raw)
            subject = _decode_header_str(msg.get("Subject", ""))
            email_date = date.today()
            try:
                from email.utils import parsedate_to_datetime
                date_header = msg.get("Date", "")
                if date_header:
                    email_date = parsedate_to_datetime(date_header).date()
            except Exception:
                pass
            if not _is_email_pix(subject):
                with self._cache_lock:
                    self._cache[uid_str] = None
                return None
            content = f"{subject} {_get_body(msg)}"
            entry = {
                "pagador": _extrair_pagador(content),
                "pagador_norm": _normalizar(_extrair_pagador(content)),
                "valor": _extrair_valor(content),
                "banco": _detectar_banco(content),
                "uid": uid_str,
                "data": email_date,
            }
            with self._cache_lock:
                self._cache[uid_str] = entry
            return entry
        except Exception as e:
            print(f"[MONITOR ERR] {type(e).__name__}: {e}", flush=True)
            return None

    def _monitor_loop(self):
        """Polling a cada 3s para detectar emails novos. Usa lock para não conflitar com buscar()."""
        time.sleep(5)
        while not self._stop:
            try:
                with self._search_lock:
                    if not self._monitor_connected or self._monitor_mail is None:
                        if not self._conectar_monitor():
                            time.sleep(10)
                            continue
                    hoje = date.today().strftime("%d-%b-%Y")
                    try:
                        self._monitor_mail.select("INBOX")
                        _, data = self._monitor_mail.search(None, f'(SINCE "{hoje}")')
                    except Exception as e:
                        print(f"[MONITOR] Erro search: {e}", flush=True)
                        self._monitor_connected = False
                        time.sleep(5)
                        continue
                    uids_all = data[0].split() if data and data[0] else []
                    with self._cache_lock:
                        novos = [u for u in uids_all if u.decode() not in self._cache]
                    for uid_bytes in novos:
                        entry = self._processar_uid(uid_bytes, self._monitor_mail)
                        if entry:
                            print(f"\U0001f4ec NOVO PIX | {entry['pagador']} | R${entry['valor']} | {entry['banco']}", flush=True)
                            if self._on_novo_pix:
                                try:
                                    self._on_novo_pix(entry)
                                except Exception as _cb_err:
                                    print(f"[CALLBACK ERR] {_cb_err}", flush=True)
            except Exception as e:
                print(f"[MONITOR LOOP ERR] {type(e).__name__}: {e}", flush=True)
                self._monitor_connected = False
            time.sleep(3)

    def _garantir_search(self):
        if not self._search_connected or self._search_mail is None:
            return self._conectar_search()
        try:
            self._search_mail.noop()
            return True
        except Exception:
            self._search_connected = False
            return self._conectar_search()

    def _buscar_no_cache(self, nome, log_fn=None):
        def log(msg):
            if log_fn:
                log_fn(msg)

        nome_busca = _normalizar(nome).lower().strip()
        hoje = date.today()

        with self._cache_lock:
            cache_snapshot = sorted(self._cache.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 0, reverse=True)

        log(f"\U0001f4ec {len(cache_snapshot)} emails no cache")

        for uid, entry in cache_snapshot:
            if entry is None:
                continue
            if uid in self._uids_usados:
                log(f"\u23e9 Ignorado (ja usado): UID {uid}")
                continue
            entry_date = entry.get("data")
            if not entry_date or entry_date < hoje:
                log(f"\u23e9 Ignorado (email antigo {entry_date}): UID {uid}")
                continue
            pagador_norm = entry["pagador_norm"]
            log(f"\U0001f4b0 Verificando UID {uid} | pagador='{pagador_norm}' | R${entry['valor']} | {entry['banco']}")
            if pagador_norm and nome_busca and (nome_busca in pagador_norm or _match_nomes(nome_busca, pagador_norm)):
                log(f"\u2705 MATCH: '{pagador_norm}'")
                self.marcar_uid_usado(uid)
                return {"valor": entry["valor"], "banco": entry["banco"], "pagador": entry["pagador"], "uid": uid}
        return None

    def buscar(self, nome, log_fn=None):
        def log(msg):
            if log_fn:
                log_fn(msg)

        # 1) tenta cache primeiro (rápido)
        res = self._buscar_no_cache(nome, log_fn)
        if res:
            return res

        # 2) fallback: força refresh usando a conexão de search (separada do monitor)
        try:
            with self._search_lock:
                if self._garantir_search():
                    hoje = date.today().strftime("%d-%b-%Y")
                    self._search_mail.select("INBOX")
                    _, data = self._search_mail.search(None, f'(SINCE "{hoje}")')
                    uids_all = data[0].split() if data and data[0] else []
                    with self._cache_lock:
                        faltantes = [u for u in uids_all if u.decode() not in self._cache]
                    for uid_bytes in faltantes[:50]:
                        self._processar_uid(uid_bytes, self._search_mail)
        except Exception as e:
            log(f"\u26a0\ufe0f Refresh IMAP falhou: {type(e).__name__}: {str(e)[:80]}")
            self._search_connected = False

        # 3) tenta cache novamente após refresh
        res = self._buscar_no_cache(nome, log_fn)
        if res:
            return res

        log(f"\u274c Nenhum pix de '{_normalizar(nome).lower().strip()}' encontrado")
        return None

    def marcar_uid_usado(self, uid: str):
        """Marca UID como usado em memória e persiste em arquivo."""
        self._uids_usados.add(uid)
        try:
            import os
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"uids_usados_{self._uid_chave()}.txt")
            with open(path, "a", encoding="utf-8") as f:
                f.write(uid + "\n")
        except Exception:
            pass

    def _carregar_uids_arquivo(self):
        """Carrega UIDs usados do arquivo ao iniciar."""
        try:
            import os
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
        for conn in [self._monitor_mail, self._search_mail]:
            try:
                if conn:
                    conn.logout()
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

    def set_pix_callback(self, user_id, callback):
        """Registra callback chamado quando novo PIX é detectado: callback(entry)"""
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
    """Busca pagamento usando a conexão IMAP correta do usuário.
    
    Args:
        config: Configuração do usuário
        nome: Nome a buscar
        log_fn: Função de log
        user_id: ID do usuário (preferencial para encontrar a conexão correta)
    """
    def log(msg):
        if log_fn:
            log_fn(msg)
    
    target_user = str(config.get("email_user", "")).strip().lower()
    target_pass = str(config.get("email_pass", "")).strip()
    target_server = str(config.get("imap_server", "")).strip().lower()
    
    log(f"🔍 Buscando '{nome}' | user_id={user_id} | email_target={target_user}")
    
    # 1) Primeiro tenta encontrar pela user_id se fornecida
    if user_id is not None and user_id in imap_manager.connections:
        conn = imap_manager.connections[user_id]
        c_user = str(conn.config.get("email_user", "")).strip().lower()
        log(f"📬 Usando conexão IMAP do user_id={user_id} (email: {c_user})")
        resultado = conn.buscar(nome, log_fn)
        if resultado:
            log(f"✅ Encontrado via user_id: {resultado.get('pagador')} | R${resultado.get('valor')}")
            return resultado
        log(f"❌ Não encontrado na conexão user_id={user_id}")
    
    # 2) Fallback: procurar por email/config que combinetarget_user = target_user.
    # Nota: isso evita conflito quando múltiplos usuários usam o mesmo email
    for uid, conn in imap_manager.connections.items():
        c_user = str(conn.config.get("email_user", "")).strip().lower()
        c_pass = str(conn.config.get("email_pass", "")).strip()
        c_server = str(conn.config.get("imap_server", "")).strip().lower()
        if c_user == target_user and c_pass == target_pass and c_server == target_server:
            log(f"📬 Conexão encontrada: uid={uid} | email={c_user}")
            resultado = conn.buscar(nome, log_fn)
            if resultado:
                log(f"✅ Encontrado: {resultado.get('pagador')} | R${resultado.get('valor')}")
                return resultado
            log(f"❌ Não encontrado nesta conexão")
    
    # 3) Fallback temporário (só deve ser usado se não houver conexão persistente)
    log(f"⚠️ Criando conexão temporária...")
    conn_temp = PersistentIMAPConnection(config)
    time.sleep(2)
    resultado = conn_temp.buscar(nome, log_fn)
    if not resultado:
        time.sleep(2)
        resultado = conn_temp.buscar(nome, log_fn)
    conn_temp.stop()
    return resultado
