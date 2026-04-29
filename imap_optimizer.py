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


class OptimizedIMAPCache:
    def __init__(self, user_id: int, config: dict):
        self.user_id = user_id
        self.config = config
        self.emails: dict[str, str] = {}  # hash -> content_norm
        self.valores: dict[str, dict] = {}  # hash -> {valor, banco}
        self.lock = threading.RLock()
        self._stop = False
        self.stats = type('S', (), {'total_emails': 0, 'cache_hits': 0, 'cache_misses': 0})()
        self._thread = threading.Thread(target=self._idle_loop, daemon=True)
        self._thread.start()

    def _carregar(self):
        try:
            mb = MailBox(self.config["imap_server"], timeout=30)
            mb.login(self.config["email_user"], self.config["email_pass"], initial_folder="INBOX")
            msgs = list(mb.fetch(AND(date_gte=date.today()), mark_seen=False, limit=100))
            with self.lock:
                self.emails.clear()
                self.valores.clear()
                for msg in msgs:
                    content = f"{msg.subject or ''} {msg.text or ''} {msg.html or ''}"
                    h = hashlib.md5(content.encode()).hexdigest()
                    self.emails[h] = _normalize(content)
                    self.valores[h] = _extrair_banco_valor(content)
                self.stats.total_emails = len(self.emails)
            logger.info(f"User {self.user_id}: {len(self.emails)} emails carregados")
            # IDLE: espera novos emails em tempo real
            for idle_data in mb.idle.wait(timeout=300):
                if self._stop:
                    break
                if idle_data:
                    self._adicionar_novos(mb)
            mb.logout()
        except Exception as exc:
            logger.error(f"User {self.user_id}: Erro IMAP [{type(exc).__name__}]: {exc}")

    def _adicionar_novos(self, mb):
        try:
            msgs = list(mb.fetch(AND(date_gte=date.today()), mark_seen=False, limit=10))
            novos = 0
            with self.lock:
                for msg in msgs:
                    content = f"{msg.subject or ''} {msg.text or ''} {msg.html or ''}"
                    h = hashlib.md5(content.encode()).hexdigest()
                    if h not in self.emails:
                        self.emails[h] = _normalize(content)
                        self.valores[h] = _extrair_banco_valor(content)
                        novos += 1
                self.stats.total_emails = len(self.emails)
            if novos:
                logger.info(f"User {self.user_id}: +{novos} emails novos via IDLE")
        except Exception as exc:
            logger.error(f"User {self.user_id}: Erro ao adicionar novos: {exc}")

    def _idle_loop(self):
        while not self._stop:
            self._carregar()
            if not self._stop:
                time.sleep(5)  # reconecta após 5s em caso de erro

    def search_payment(self, nome: str) -> Optional[dict]:
        nome_norm = _normalize(nome)
        partes = nome_norm.split()
        with self.lock:
            for h, content_norm in self.emails.items():
                if _match_nome(content_norm, partes):
                    self.stats.cache_hits += 1
                    return self.valores[h]
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
