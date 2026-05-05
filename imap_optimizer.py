import re
import imaplib
import email
import unicodedata
import logging
import threading
import time
from datetime import datetime, timedelta
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


def _parse_email(mail, uid) -> Optional[dict]:
    try:
        _, msg_data = mail.fetch(uid, "(RFC822)")
        if not msg_data or not msg_data[0]:
            return None
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)
        subject = _decode_header_str(msg.get("Subject", ""))
        if not _is_email_pix(subject):
            return None
        body = _get_body(msg)
        content = f"{subject} {body}"
        pagador = _extrair_pagador(content)
        pagador_norm = _normalizar(pagador).lower().strip()
        valor = _extrair_valor(content)
        banco = _detectar_banco(content)
        # Usa a data real do email
        try:
            from email.utils import parsedate_to_datetime
            ts = parsedate_to_datetime(msg.get("Date", "")).replace(tzinfo=None)
        except Exception:
            ts = datetime.now()
        return {
            "uid": uid.decode() if isinstance(uid, bytes) else str(uid),
            "pagador": pagador,
            "pagador_norm": pagador_norm,
            "valor": valor,
            "banco": banco,
            "ts": ts,
        }
    except Exception:
        return None


class IMAPIDLEListener:
    """Mantém conexão IMAP IDLE e armazena emails Pix em memória."""

    def __init__(self, config: dict, log_fn=None):
        self.config = config
        self.log_fn = log_fn
        self._emails: list[dict] = []  # emails recentes em memória
        self._lock = threading.Lock()
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _log(self, msg: str):
        if self.log_fn:
            self.log_fn(msg)
        logger.info(msg)

    def _conectar(self):
        mail = imaplib.IMAP4_SSL(self.config["imap_server"])
        mail.socket().settimeout(60)
        mail.login(self.config["email_user"], self.config["email_pass"])
        mail.select("INBOX")
        return mail

    def _carregar_recentes(self, mail):
        """Carrega emails do Nubank dos últimos 30 min ao iniciar."""
        from datetime import date
        hoje = date.today().strftime("%d-%b-%Y")
        _, data = mail.search(None, f'(SINCE "{hoje}" FROM "nubank.com.br")')
        uids = data[0].split() if data and data[0] else []
        cutoff = datetime.now() - timedelta(minutes=30)
        carregados = 0
        uids_sorted = sorted(uids, key=lambda x: int(x), reverse=True)
        for uid in uids_sorted[:200]:  # max 200 emails iniciais
            entry = _parse_email(mail, uid)
            if entry and entry["ts"] >= cutoff:
                with self._lock:
                    if not any(e["uid"] == entry["uid"] for e in self._emails):
                        self._emails.append(entry)
                        carregados += 1
        self._log(f"\u2705 IDLE ativo: {carregados} emails Pix carregados")

    def _processar_novos(self, mail):
        """Busca emails novos após notificação IDLE."""
        from datetime import date
        hoje = date.today().strftime("%d-%b-%Y")
        with self._lock:
            uids_conhecidos = {e["uid"] for e in self._emails}
        _, data = mail.search(None, f'(SINCE "{hoje}" FROM "nubank.com.br")')
        uids = data[0].split() if data and data[0] else []
        novos = 0
        for uid in sorted(uids, key=lambda x: int(x), reverse=True):
            uid_str = uid.decode() if isinstance(uid, bytes) else str(uid)
            if uid_str in uids_conhecidos:
                continue
            entry = _parse_email(mail, uid)
            if entry:
                with self._lock:
                    self._emails.append(entry)
                novos += 1
                self._log(f"\U0001f4e9 Pix recebido: {entry['pagador']} | R${entry['valor']} | {entry['banco']}")
        # Limpa emails com mais de 30 min
        cutoff = datetime.now() - timedelta(minutes=30)
        with self._lock:
            self._emails = [e for e in self._emails if e["ts"] >= cutoff]
    def _run(self):
        while not self._stop:
            try:
                mail = self._conectar()
                self._log("\u2705 IMAP IDLE conectado")
                self._carregar_recentes(mail)

                while not self._stop:
                    try:
                        # Envia IDLE
                        tag = mail._new_tag()
                        mail.send(f"{tag} IDLE\r\n".encode())
                        mail.readline()  # "+" (continua)

                        # Aguarda notificação por até 25s (Gmail exige refresh a cada 29s)
                        mail.socket().settimeout(25)
                        try:
                            line = mail.readline()
                            if b"EXISTS" in line or b"RECENT" in line:
                                # Sai do IDLE e processa
                                mail.send(b"DONE\r\n")
                                mail.readline()
                                self._processar_novos(mail)
                            else:
                                mail.send(b"DONE\r\n")
                                mail.readline()
                        except Exception:
                            mail.send(b"DONE\r\n")
                            try:
                                mail.readline()
                            except Exception:
                                pass

                        mail.socket().settimeout(60)

                    except Exception as e:
                        self._log(f"\u26a0\ufe0f IDLE erro: {type(e).__name__} — reconectando...")
                        break

                try:
                    mail.logout()
                except Exception:
                    pass

            except Exception as e:
                self._log(f"\u26a0\ufe0f IMAP IDLE falhou: {type(e).__name__}: {str(e)[:100]} — retry em 5s")
                time.sleep(5)

    def buscar(self, nome: str, log_fn=None) -> Optional[dict]:
        def log(msg):
            if log_fn:
                log_fn(msg)

        nome_busca = _normalizar(nome).lower().strip()
        cutoff = datetime.now() - timedelta(minutes=30)

        with self._lock:
            emails = [e for e in self._emails if e["ts"] >= cutoff]

        log(f"\U0001f4ec Memória: {len(emails)} emails nos últimos 30 min")

        for entry in sorted(emails, key=lambda x: x["ts"], reverse=True):
            pagador_norm = entry["pagador_norm"]
            log(f"\U0001f4b0 Verificando: pagador='{pagador_norm}'")
            if pagador_norm and nome_busca and (nome_busca in pagador_norm or _match_nomes(nome_busca, pagador_norm)):
                log(f"\u2705 MATCH: '{pagador_norm}' contém '{nome_busca}'")
                return {"valor": entry["valor"], "banco": entry["banco"], "pagador": entry["pagador"]}

        log(f"\u274c Nenhum pix de '{nome_busca}' encontrado")
        return None

    def stop(self):
        self._stop = True


class IMAPManager:
    def __init__(self):
        self.listeners: dict[int, IMAPIDLEListener] = {}
        self.configs: dict[int, dict] = {}

    def get_cache(self, user_id: int, config: dict):
        self.configs[user_id] = config
        if user_id not in self.listeners:
            self.listeners[user_id] = IMAPIDLEListener(config)
        return self

    def set_log(self, user_id: int, log_fn):
        if user_id in self.listeners:
            self.listeners[user_id].log_fn = lambda msg: log_fn(user_id, msg)

    def stop_cache(self, user_id: int):
        if user_id in self.listeners:
            self.listeners[user_id].stop()
            del self.listeners[user_id]
        self.configs.pop(user_id, None)

    def get_global_stats(self) -> dict:
        return {"active_caches": len(self.listeners)}

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


def buscar_pagamento_imap(config: dict, nome: str, log_fn=None) -> Optional[dict]:
    """Busca nos emails já carregados pelo IDLE listener."""
    # Encontra o listener pelo config
    for listener in imap_manager.listeners.values():
        if listener.config.get("email_user") == config.get("email_user"):
            return listener.buscar(nome, log_fn)

    # Fallback: cria listener temporário e busca diretamente
    if log_fn:
        log_fn("\u26a0\ufe0f IDLE não iniciado, buscando diretamente...")
    return _buscar_direto(config, nome, log_fn)


def _buscar_direto(config: dict, nome: str, log_fn=None) -> Optional[dict]:
    """Fallback: busca direta no IMAP sem IDLE."""
    def log(msg):
        if log_fn:
            log_fn(msg)

    nome_busca = _normalizar(nome).lower().strip()

    for tentativa in range(1, 4):
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
            time.sleep(3)
            continue

    resultado = None
    try:
        from datetime import date
        hoje = date.today().strftime("%d-%b-%Y")
        cutoff = datetime.now() - timedelta(minutes=1)
        _, data = mail.search(None, f'(SINCE "{hoje}" FROM "nubank.com.br")')
        uids = data[0].split() if data and data[0] else []
        uids_sorted = sorted(uids, key=lambda x: int(x), reverse=True)
        log(f"\U0001f4ec {len(uids_sorted)} emails do Nubank hoje")

        for uid in uids_sorted:
            entry = _parse_email(mail, uid)
            if not entry:
                continue
            if entry["ts"] < cutoff:
                continue
            log(f"\U0001f4b0 Pix | pagador='{entry['pagador_norm']}' | R${entry['valor']}")
            if entry["pagador_norm"] and nome_busca and (nome_busca in entry["pagador_norm"] or _match_nomes(nome_busca, entry["pagador_norm"])):
                log(f"\u2705 MATCH: '{entry['pagador_norm']}' contém '{nome_busca}'")
                resultado = {"valor": entry["valor"], "banco": entry["banco"], "pagador": entry["pagador"]}
                break

        if not resultado:
            log(f"\u274c Nenhum pix de '{nome_busca}' encontrado")
    except Exception as e:
        log(f"\u26a0\ufe0f Erro: {type(e).__name__}: {str(e)[:150]}")
    finally:
        try:
            mail.logout()
        except Exception:
            pass

    return resultado
