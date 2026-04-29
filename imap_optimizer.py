import re
import threading
import time
import unicodedata
import hashlib
from datetime import date, datetime
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


def _extrair_log_transferencia(content: str, subject: str, valores: dict) -> str:
    nome_match = NOME_RE.search(subject or '')
    if not nome_match:
        nome_match = NOME_RE.search(content)
    nome_log = nome_match.group(1).strip() if nome_match else 'Desconhecido'
    valor_log = f"R$ {valores.get('valor', '?')}"
    data_log = datetime.now().strftime('%d/%m às %H:%M')
    return f"\U0001f4ac Pagamento: {nome_log} | {valor_log} | {data_log} | {valores.get('banco', '?')}"


class OptimizedIMAPCache:
    def __init__(self, user_id: int, config: dict):
        self.user_id = user_id
        self.config = config
        self.emails: dict[str, str] = {}
        self.valores: dict[str, dict] = {}
        self.uids_vistos: set = set()
        self.lock = threading.RLock()
        self._stop = False
        self.stats = type('S', (), {'total_emails': 0, 'cache_hits': 0, 'cache_misses': 0})()
        self._log = None
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _conectar(self):
        mb = MailBox(self.config["imap_server"], timeout=30)
        mb.login(self.config["email_user"], self.config["email_pass"], initial_folder="INBOX")
        return mb

    def _carregar(self, mb):
        pastas = ["[Gmail]/All Mail", "INBOX"]
        todos = []
        pasta_usada = None
        for pasta in pastas:
            try:
                mb.folder.set(pasta)
                msgs = list(mb.fetch("ALL", mark_seen=False, limit=100))
                todos.extend(msgs)
                pasta_usada = pasta
                if self._log:
                    self._log(self.user_id, f"\U0001f4c2 '{pasta}': {len(msgs)} emails carregados")
                break
            except Exception as e:
                if self._log:
                    self._log(self.user_id, f"\u26a0\ufe0f Pasta '{pasta}' falhou: {e}")
                continue
        with self.lock:
            for msg in todos:
                content = f"{msg.subject or ''} {msg.text or ''} {msg.html or ''}"
                key = str(msg.uid) if msg.uid else hashlib.md5(content.encode()).hexdigest()
                self.emails[key] = _normalize(content)
                self.valores[key] = _extrair_banco_valor(content)
                if msg.uid:
                    self.uids_vistos.add(msg.uid)
            self.stats.total_emails = len(self.emails)
        if self._log:
            self._log(self.user_id, f"\U0001f4e7 Cache pronto: {len(self.emails)} emails em '{pasta_usada}'")

    def _checar_novos(self, mb):
        try:
            msgs = list(mb.fetch("ALL", mark_seen=False, limit=100))
            novos = 0
            for msg in msgs:
                if msg.uid and msg.uid not in self.uids_vistos:
                    content = f"{msg.subject or ''} {msg.text or ''} {msg.html or ''}"
                    key = str(msg.uid)
                    with self.lock:
                        self.emails[key] = _normalize(content)
                        self.valores[key] = _extrair_banco_valor(content)
                        self.uids_vistos.add(msg.uid)
                        self.stats.total_emails = len(self.emails)
                    novos += 1
                    if self._log:
                        linha = _extrair_log_transferencia(content, msg.subject, self.valores[key])
                        self._log(self.user_id, linha)
            return novos
        except Exception as exc:
            raise exc

    def _loop(self):
        while not self._stop:
            try:
                mb = self._conectar()
                if self._log:
                    self._log(self.user_id, "\u2705 Login IMAP OK")
                self._carregar(mb)
                while not self._stop:
                    time.sleep(30)
                    if self._stop:
                        break
                    novos = self._checar_novos(mb)
                    if novos and self._log:
                        self._log(self.user_id, f"\u2705 {novos} nova(s) transfer\u00eancia(s) detectada(s)")
                mb.logout()
            except Exception as exc:
                logger.error(f"User {self.user_id}: Erro IMAP [{type(exc).__name__}]: {exc}")
                if self._log:
                    self._log(self.user_id, f"\u26a0\ufe0f IMAP erro: [{type(exc).__name__}] {str(exc)[:200]}")
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
