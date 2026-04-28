import threading
import time
from datetime import datetime, date
from typing import Optional
from dataclasses import dataclass
import hashlib
import re
import unicodedata
from imap_tools import MailBox, AND
import logging

logger = logging.getLogger(__name__)


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
        self._total = 0
        self._cache_hits = 0
        self._cache_misses = 0
        self._last_update: Optional[datetime] = None

        self.valor_pattern = re.compile(r"R\$\s?([\d.,]+)", re.IGNORECASE)
        self.bancos = [
            "Nubank", "PicPay", "Itau", "Bradesco", "Caixa", "Santander",
            "Inter", "C6 Bank", "Mercado Pago", "Next", "BTG", "Stone",
            "Sicoob", "Sicredi", "Banrisul", "BRB", "Neon", "Banco do Brasil",
            "Original", "Pan", "Agibank", "Pagbank", "PagSeguro", "Ame",
            "Will Bank", "XP", "Daycoval", "Sofisa"
        ]

    def _normalize(self, text: str) -> str:
        return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower().strip()

    def _extract_banco(self, content: str) -> str:
        cl = content.lower()
        for b in self.bancos:
            if b.lower() in cl:
                return b
        return "Desconhecido"

    def _extract_valor(self, content: str) -> Optional[str]:
        m = self.valor_pattern.search(content)
        return m.group(1) if m else None

    def _match(self, content_norm: str, content_orig: str, partes: list) -> bool:
        return (
            all(p in content_norm for p in partes) or
            all(p in content_orig for p in partes)
        )

    def search_payment(self, nome: str) -> Optional[dict]:
        """Busca direta no IMAP — mesmo fluxo original que funcionava."""
        nome_norm = self._normalize(nome)
        partes = [p for p in nome_norm.split() if len(p) >= 2]
        if not partes:
            return None

        try:
            mb = MailBox(self.config["imap_server"])
            mb.login(self.config["email_user"], self.config["email_pass"], initial_folder="INBOX")
            msgs = list(mb.fetch(AND(date_gte=date.today()), mark_seen=False, limit=200))
            mb.logout()

            self._total = len(msgs)
            self._last_update = datetime.now()

            for msg in reversed(msgs):  # mais recentes primeiro
                subject = msg.subject or ""
                corpo = msg.text or ""
                if not corpo.strip():
                    corpo = re.sub(r'<[^>]+>', ' ', msg.html or "")
                content = f"{subject} {corpo[:1000]}"
                content_norm = self._normalize(content)
                content_orig = content.lower()

                if self._match(content_norm, content_orig, partes):
                    self._cache_hits += 1
                    return {
                        "valor": self._extract_valor(content) or "N/A",
                        "banco": self._extract_banco(content)
                    }

            self._cache_misses += 1
            return None

        except Exception as exc:
            logger.warning(f"User {self.user_id}: Erro IMAP: {exc}")
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
        pass  # nada para parar


class IMAPCacheManager:
    def __init__(self):
        self.caches: dict[int, OptimizedIMAPCache] = {}

    def get_cache(self, user_id: int, config: dict) -> OptimizedIMAPCache:
        if user_id not in self.caches:
            self.caches[user_id] = OptimizedIMAPCache(user_id, config)
        return self.caches[user_id]

    def stop_cache(self, user_id: int):
        if user_id in self.caches:
            del self.caches[user_id]

    def get_global_stats(self) -> dict:
        total_hits = sum(c._cache_hits for c in self.caches.values())
        total_misses = sum(c._cache_misses for c in self.caches.values())
        total = total_hits + total_misses
        return {
            "active_caches": len(self.caches),
            "total_emails": sum(c._total for c in self.caches.values()),
            "global_hit_rate": f"{total_hits/total:.2%}" if total > 0 else "0.00%",
            "total_requests": total,
            "caches": {uid: c.get_stats() for uid, c in self.caches.items()},
        }


imap_manager = IMAPCacheManager()
