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

logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

MAX_EMAILS_CACHE = 200  # limite máximo de emails em memória


@dataclass
class EmailData:
    content_norm: str  # já normalizado, economiza memória
    hash_id: str
    valor: Optional[str] = None
    banco: Optional[str] = None


@dataclass
class CacheStats:
    total_emails: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    last_update: Optional[datetime] = None


class OptimizedIMAPCache:
    def __init__(self, user_id: int, config: dict):
        self.user_id = user_id
        self.config = config
        self.emails: list[EmailData] = []  # lista simples, sem índice extra
        self.stats = CacheStats()
        self.lock = threading.RLock()
        self.last_update = datetime.now() - timedelta(hours=2)
        self._stop = False

        self.valor_pattern = re.compile(r"R\$\s?([\d.,]+)", re.IGNORECASE)
        self.bancos_patterns = {
            b: re.compile(rf"\b{re.escape(b)}\b", re.IGNORECASE)
            for b in [
                "Nubank", "PicPay", "Itau", "Bradesco", "Caixa", "Santander",
                "Inter", "C6 Bank", "Mercado Pago", "Next", "BTG", "Stone",
                "Sicoob", "Sicredi", "Banrisul", "BRB", "Neon", "Banco do Brasil",
                "Original", "Pan", "Agibank", "Pagbank", "PagSeguro", "Ame",
                "Will Bank", "XP", "Daycoval", "Sofisa"
            ]
        }

        self._thread = threading.Thread(target=self._update_loop, daemon=True)
        self._thread.start()

    def _normalize(self, text: str) -> str:
        return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower().strip()

    def _extract(self, subject: str, text: str) -> EmailData:
        # Usa apenas assunto + primeiros 500 chars do corpo para economizar RAM
        content = f"{subject} {text[:500]}"
        content_lower = content.lower()
        content_norm = self._normalize(content)
        hash_id = hashlib.md5(content_norm.encode()).hexdigest()

        valor_match = self.valor_pattern.search(content)
        valor = valor_match.group(1) if valor_match else None

        banco = "Desconhecido"
        for nome_banco, pattern in self.bancos_patterns.items():
            if pattern.search(content_lower):
                banco = nome_banco
                break

        return EmailData(content_norm=content_norm, hash_id=hash_id, valor=valor, banco=banco)

    def _fetch_and_update(self):
        try:
            mb = MailBox(self.config["imap_server"])
            mb.login(self.config["email_user"], self.config["email_pass"], initial_folder="INBOX")
            # Busca apenas emails de hoje, limite 100
            msgs = list(mb.fetch(AND(date_gte=date.today()), mark_seen=False, limit=100))
            mb.logout()

            novos = []
            hashes_existentes = {e.hash_id for e in self.emails}
            for msg in msgs:
                ed = self._extract(msg.subject or "", msg.text or "")
                if ed.hash_id not in hashes_existentes:
                    novos.append(ed)

            with self.lock:
                self.emails.extend(novos)
                # Mantém apenas os últimos MAX_EMAILS_CACHE
                if len(self.emails) > MAX_EMAILS_CACHE:
                    self.emails = self.emails[-MAX_EMAILS_CACHE:]
                self.stats.total_emails = len(self.emails)
                self.stats.last_update = datetime.now()
                self.last_update = datetime.now()

            if novos:
                logger.warning(f"User {self.user_id}: +{len(novos)} emails ({len(self.emails)} total)")

        except Exception as exc:
            logger.warning(f"User {self.user_id}: Erro IMAP: {exc}")

    def _update_loop(self):
        self._fetch_and_update()
        while not self._stop:
            time.sleep(30)
            if not self._stop:
                self._fetch_and_update()

    def _match_nome(self, content_norm: str, partes: list) -> bool:
        """Verifica se todas as partes do nome estão no conteúdo."""
        return bool(partes) and all(p in content_norm for p in partes)

    def _match_nome_estrito(self, content_norm: str, partes: list) -> bool:
        """Verifica com word boundary (para evitar falsos positivos)."""
        return bool(partes) and all(
            re.search(rf"(?<![a-z]){re.escape(p)}(?![a-z])", content_norm) for p in partes
        )

    def search_payment(self, nome: str) -> Optional[dict]:
        nome_norm = self._normalize(nome)
        partes = [p for p in nome_norm.split() if len(p) >= 3]

        # 1. Busca no cache
        with self.lock:
            for ed in reversed(self.emails):  # mais recentes primeiro
                if self._match_nome_estrito(ed.content_norm, partes):
                    self.stats.cache_hits += 1
                    return {"valor": ed.valor or "N/A", "banco": ed.banco}
            # fallback: busca simples
            for ed in reversed(self.emails):
                if self._match_nome(ed.content_norm, partes):
                    self.stats.cache_hits += 1
                    return {"valor": ed.valor or "N/A", "banco": ed.banco}

        # 2. Busca direta no IMAP com filtro por nome
        try:
            mb = MailBox(self.config["imap_server"])
            mb.login(self.config["email_user"], self.config["email_pass"], initial_folder="INBOX")

            msgs_encontradas = []
            for termo in partes[:2]:
                try:
                    msgs = list(mb.fetch(AND(date_gte=date.today(), text=termo), mark_seen=False, limit=30))
                    msgs_encontradas.extend(msgs)
                except Exception:
                    break

            mb.logout()

            vistos = set()
            for msg in msgs_encontradas:
                uid = getattr(msg, 'uid', id(msg))
                if uid in vistos:
                    continue
                vistos.add(uid)
                ed = self._extract(msg.subject or "", msg.text or "")
                if self._match_nome(ed.content_norm, partes):
                    with self.lock:
                        if ed.hash_id not in {e.hash_id for e in self.emails}:
                            self.emails.append(ed)
                            if len(self.emails) > MAX_EMAILS_CACHE:
                                self.emails = self.emails[-MAX_EMAILS_CACHE:]
                            self.stats.total_emails = len(self.emails)
                    self.stats.cache_hits += 1
                    logger.warning(f"User {self.user_id}: encontrado '{nome_norm}' - assunto: {msg.subject}")
                    return {"valor": ed.valor or "N/A", "banco": ed.banco}

        except Exception as exc:
            logger.warning(f"User {self.user_id}: Erro busca direta: {exc}")

        self.stats.cache_misses += 1
        return None

    def search_payment_optimized(self, nome: str) -> Optional[dict]:
        return self.search_payment(nome)

    def get_stats(self) -> dict:
        with self.lock:
            total = self.stats.cache_hits + self.stats.cache_misses
            hit_rate = self.stats.cache_hits / total if total > 0 else 0
            return {
                "total_emails": self.stats.total_emails,
                "cache_hits": self.stats.cache_hits,
                "cache_misses": self.stats.cache_misses,
                "hit_rate": f"{hit_rate:.2%}",
                "last_update": self.stats.last_update.isoformat() if self.stats.last_update else None,
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
        total_emails = sum(c.stats.total_emails for c in self.caches.values())
        total_hits = sum(c.stats.cache_hits for c in self.caches.values())
        total_misses = sum(c.stats.cache_misses for c in self.caches.values())
        total = total_hits + total_misses
        return {
            "active_caches": len(self.caches),
            "total_emails": total_emails,
            "global_hit_rate": f"{total_hits/total:.2%}" if total > 0 else "0.00%",
            "total_requests": total,
            "caches": {uid: c.get_stats() for uid, c in self.caches.items()},
        }


imap_manager = IMAPCacheManager()
