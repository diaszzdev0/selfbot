import re
import threading
import time
import unicodedata
import hashlib
from datetime import date, datetime, timedelta
from typing import Optional
from imap_tools import MailBox, AND
import logging

logger = logging.getLogger(__name__)

BANCOS_RE = {b.lower(): re.compile(rf"\b{re.escape(b)}\b", re.IGNORECASE) for b in [
    "Nubank", "PicPay", "Itau", "Bradesco", "Caixa", "Santander", "Inter", "C6 Bank",
    "Mercado Pago", "Next", "BTG", "Stone", "Sicoob", "Sicredi", "Banrisul", "BRB",
    "Safra", "Votorantim", "Neon", "Banco do Brasil", "BB", "Original", "Pan", "Agibank",
    "Pagbank", "PagSeguro", "Ame", "99Pay", "RecargaPay", "Digio", "Will Bank",
    "Banco Inter", "XP", "Modal", "Daycoval", "Rendimento", "Sofisa", "Banese", "Banpara", "Banestes"
]}
VALOR_RE = re.compile(r"R\$\s?([\d.,]+)", re.IGNORECASE)
NOME_RE = re.compile(
    r'transfer[eê]ncia de ([A-Z][a-zA-Z\u00C0-\u00FF]+(?: [A-Z][a-zA-Z\u00C0-\u00FF]+)+)',
    re.IGNORECASE
)


def _normalize(text: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&[a-zA-Z0-9#]+;', ' ', text)
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower().strip()


def _match_nome(content_norm: str, partes: list) -> bool:
    return bool(partes) and all(re.search(rf"\b{re.escape(p)}\b", content_norm) for p in partes)


def _extrair_banco_valor(content: str) -> dict:
    valor_match = VALOR_RE.search(content)
    valor = valor_match.group(1) if valor_match else "N/A"
    banco = "Desconhecido"
    for nome_banco, pattern in BANCOS_RE.items():
        if pattern.search(content.lower()):
            banco = nome_banco.title()
            break
    return {"valor": valor, "banco": banco}


def _log_transferencia(content: str, subject: str, valores: dict) -> str:
    nome_match = NOME_RE.search(subject or '') or NOME_RE.search(content)
    nome = nome_match.group(1).strip() if nome_match else 'Desconhecido'
    return f"\U0001f4ac Pagamento: {nome} | R$ {valores.get('valor','?')} | {datetime.now().strftime('%d/%m as %H:%M')} | {valores.get('banco','?')}"


class OptimizedIMAPCache:
    def __init__(self, user_id: int, config: dict):
        self.user_id = user_id
        self.config = config
        self.emails: dict[str, str] = {}
        self.valores: dict[str, dict] = {}
        self.uids_vistos: set = set()
        self.lock = threading.RLock()
        self._stop = False
        self._log = None
        self.stats = type('S', (), {'total_emails': 0, 'cache_hits': 0, 'cache_misses': 0})()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _log_msg(self, msg: str):
        if self._log:
            self._log(self.user_id, msg)
        logger.info(f"User {self.user_id}: {msg}")

    def _fetch(self, mb, pasta: str) -> list:
        try:
            mb.folder.set(pasta)
            since = date.today() - timedelta(days=1)
            msgs = list(mb.fetch(AND(date_gte=since), mark_seen=False, limit=200))
            self._log_msg(f"\U0001f4c2 '{pasta}': {len(msgs)} emails")
            return msgs
        except Exception as e:
            self._log_msg(f"\u26a0\ufe0f '{pasta}' falhou: {type(e).__name__}: {str(e)[:100]}")
            return []

    def _processar(self, msgs: list, apenas_novos: bool = False) -> int:
        count = 0
        for msg in msgs:
            uid = str(msg.uid) if msg.uid else None
            if apenas_novos and uid and uid in self.uids_vistos:
                continue
            content = f"{msg.subject or ''} {msg.text or ''} {msg.html or ''}"
            key = uid or hashlib.md5(content.encode()).hexdigest()
            norm = _normalize(content)
            valores = _extrair_banco_valor(content)
            with self.lock:
                self.emails[key] = norm
                self.valores[key] = valores
                if uid:
                    self.uids_vistos.add(uid)
                self.stats.total_emails = len(self.emails)
            if apenas_novos:
                count += 1
                self._log_msg(_log_transferencia(content, msg.subject, valores))
        return count

    def _loop(self):
        while not self._stop:
            try:
                mb = MailBox(self.config["imap_server"], timeout=30)
                mb.login(self.config["email_user"], self.config["email_pass"], initial_folder="INBOX")
                self._log_msg("\u2705 Login IMAP OK")

                # Carrega emails iniciais
                msgs = self._fetch(mb, "[Gmail]/All Mail")
                if not msgs:
                    msgs = self._fetch(mb, "INBOX")
                self._processar(msgs)
                self._log_msg(f"\U0001f4e7 Cache pronto: {self.stats.total_emails} emails")

                # Loop de verificacao de novos
                while not self._stop:
                    time.sleep(30)
                    if self._stop:
                        break
                    try:
                        novos_msgs = self._fetch(mb, "[Gmail]/All Mail") or self._fetch(mb, "INBOX")
                        novos = self._processar(novos_msgs, apenas_novos=True)
                        if novos:
                            self._log_msg(f"\u2705 {novos} nova(s) transferencia(s)")
                    except Exception:
                        break  # reconecta

                mb.logout()
            except Exception as e:
                self._log_msg(f"\u26a0\ufe0f IMAP erro: {type(e).__name__}: {str(e)[:150]}")
                time.sleep(10)

    def search_payment(self, nome: str) -> Optional[dict]:
        nome_norm = _normalize(nome)
        partes = nome_norm.split()
        with self.lock:
            for key, content_norm in self.emails.items():
                if _match_nome(content_norm, partes):
                    self.stats.cache_hits += 1
                    return self.valores[key]
        self.stats.cache_misses += 1
        return None

    def search_payment_optimized(self, nome: str) -> Optional[dict]:
        return self.search_payment(nome)

    def get_stats(self) -> dict:
        return {"total_emails": self.stats.total_emails, "hit_rate": "N/A", "last_update": None, "update_duration": "0s"}

    def stop(self):
        self._stop = True


class IMAPCacheManager:
    def __init__(self):
        self.caches: dict[int, OptimizedIMAPCache] = {}

    def get_cache(self, user_id: int, config: dict) -> OptimizedIMAPCache:
        if user_id not in self.caches:
            self.caches[user_id] = OptimizedIMAPCache(user_id, config)
        return self.caches[user_id]

    def stop_cache(self, user_id: int):
        if user_id in self.caches:
            self.caches[user_id].stop()
            self.caches.pop(user_id, None)

    def get_global_stats(self) -> dict:
        return {"active_caches": len(self.caches)}


imap_manager = IMAPCacheManager()
