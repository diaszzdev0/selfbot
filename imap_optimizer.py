import threading
import time
from datetime import datetime, timedelta, date
from typing import Optional
from dataclasses import dataclass
import hashlib
import re
import unicodedata
from imap_tools import MailBox, AND
import logging

logger = logging.getLogger(__name__)

MAX_EMAILS_CACHE = 300


@dataclass
class EmailData:
    content_norm: str
    content_orig: str
    hash_id: str
    valor: Optional[str] = None
    banco: Optional[str] = None


class OptimizedIMAPCache:
    def __init__(self, user_id: int, config: dict):
        self.user_id = user_id
        self.config = config
        self.emails: list[EmailData] = []
        self.lock = threading.RLock()
        self._stop = False
        self._cache_hits = 0
        self._cache_misses = 0
        self._total = 0
        self._last_update: Optional[datetime] = None

        self.valor_pattern = re.compile(r"R\$\s?([\d.,]+)", re.IGNORECASE)
        self.bancos = [
            "Nubank", "PicPay", "Itau", "Bradesco", "Caixa", "Santander",
            "Inter", "C6 Bank", "Mercado Pago", "Next", "BTG", "Stone",
            "Sicoob", "Sicredi", "Banrisul", "BRB", "Neon", "Banco do Brasil",
            "Original", "Pan", "Agibank", "Pagbank", "PagSeguro", "Ame",
            "Will Bank", "XP", "Daycoval", "Sofisa"
        ]

        # Inicia atualização imediata em background
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _normalize(self, text: str) -> str:
        return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower().strip()

    def _extract_banco(self, content: str) -> str:
        for b in self.bancos:
            if b.lower() in content.lower():
                return b
        return "Desconhecido"

    def _extract(self, subject: str, text: str, html: str) -> EmailData:
        corpo = text[:1000] if text.strip() else re.sub(r'<[^>]+>', ' ', html)[:1000]
        content = f"{subject} {corpo}"
        content_norm = self._normalize(content)
        content_orig = content.lower()
        hash_id = hashlib.md5(content_norm.encode()).hexdigest()
        valor_match = self.valor_pattern.search(content)
        valor = valor_match.group(1) if valor_match else None
        banco = self._extract_banco(content)
        return EmailData(content_norm=content_norm, content_orig=content_orig,
                         hash_id=hash_id, valor=valor, banco=banco)

    def _fetch_all(self):
        """Baixa todos os emails do dia e atualiza o cache."""
        try:
            mb = MailBox(self.config["imap_server"])
            mb.login(self.config["email_user"], self.config["email_pass"], initial_folder="INBOX")
            msgs = list(mb.fetch(AND(date_gte=date.today()), mark_seen=False, limit=300))
            mb.logout()

            with self.lock:
                hashes = {e.hash_id for e in self.emails}
                novos = 0
                for msg in msgs:
                    ed = self._extract(msg.subject or "", msg.text or "", msg.html or "")
                    if ed.hash_id not in hashes:
                        self.emails.append(ed)
                        hashes.add(ed.hash_id)
                        novos += 1
                if len(self.emails) > MAX_EMAILS_CACHE:
                    self.emails = self.emails[-MAX_EMAILS_CACHE:]
                self._total = len(self.emails)
                self._last_update = datetime.now()
                if novos:
                    logger.warning(f"User {self.user_id}: cache +{novos} emails ({self._total} total)")
        except Exception as exc:
            logger.warning(f"User {self.user_id}: Erro IMAP fetch: {exc}")

    def _loop(self):
        self._fetch_all()
        while not self._stop:
            time.sleep(60)  # atualiza a cada 60s
            if not self._stop:
                self._fetch_all()

    def _match(self, ed: EmailData, partes: list) -> bool:
        """Match em conteúdo normalizado e original."""
        norm_ok = all(p in ed.content_norm for p in partes)
        orig_ok = all(p in ed.content_orig for p in partes)
        return norm_ok or orig_ok

    def search_payment(self, nome: str) -> Optional[dict]:
        nome_norm = self._normalize(nome)
        partes = [p for p in nome_norm.split() if len(p) >= 2]
        if not partes:
            return None

        with self.lock:
            emails_snapshot = list(reversed(self.emails))

        # Busca no cache
        for ed in emails_snapshot:
            if self._match(ed, partes):
                self._cache_hits += 1
                return {"valor": ed.valor or "N/A", "banco": ed.banco}

        # Se cache vazio ou nao achou, busca direta rapida (so assunto)
        try:
            mb = MailBox(self.config["imap_server"])
            mb.login(self.config["email_user"], self.config["email_pass"], initial_folder="INBOX")
            msgs = list(mb.fetch(AND(date_gte=date.today()), mark_seen=False, limit=300))
            mb.logout()

            # Atualiza cache com novos
            with self.lock:
                hashes = {e.hash_id for e in self.emails}
                for msg in msgs:
                    ed = self._extract(msg.subject or "", msg.text or "", msg.html or "")
                    if ed.hash_id not in hashes:
                        self.emails.append(ed)
                        hashes.add(ed.hash_id)
                if len(self.emails) > MAX_EMAILS_CACHE:
                    self.emails = self.emails[-MAX_EMAILS_CACHE:]
                self._total = len(self.emails)
                self._last_update = datetime.now()

            # Busca nos emails recém baixados
            for msg in reversed(msgs):
                ed = self._extract(msg.subject or "", msg.text or "", msg.html or "")
                if self._match(ed, partes):
                    self._cache_hits += 1
                    logger.warning(f"User {self.user_id}: encontrado '{nome_norm}' - {msg.subject}")
                    return {"valor": ed.valor or "N/A", "banco": ed.banco}

        except Exception as exc:
            logger.warning(f"User {self.user_id}: Erro busca direta: {exc}")

        self._cache_misses += 1
        return None

    def search_payment_optimized(self, nome: str) -> Optional[dict]:
        return self.search_payment(nome)

    def get_stats(self) -> dict:
        total = self._cache_hits + self._cache_misses
        return {
            "total_emails": self._total,
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "hit_rate": f"{self._cache_hits/total:.2%}" if total > 0 else "0.00%",
            "last_update": self._last_update.isoformat() if self._last_update else None,
        }

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
            del self.caches[user_id]

    def get_global_stats(self) -> dict:
        total_emails = sum(c._total for c in self.caches.values())
        total_hits = sum(c._cache_hits for c in self.caches.values())
        total_misses = sum(c._cache_misses for c in self.caches.values())
        total = total_hits + total_misses
        return {
            "active_caches": len(self.caches),
            "total_emails": total_emails,
            "global_hit_rate": f"{total_hits/total:.2%}" if total > 0 else "0.00%",
            "total_requests": total,
            "caches": {uid: c.get_stats() for uid, c in self.caches.items()},
        }


imap_manager = IMAPCacheManager()
