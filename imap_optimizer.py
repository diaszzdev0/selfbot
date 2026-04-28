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
    content_norm: str  # normalizado (sem acento, lowercase)
    content_orig: str  # original para busca com acento
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

    def _extract(self, subject: str, text: str, html: str = "") -> EmailData:
        # Pega o melhor conteúdo disponível
        corpo = text[:1000] if text.strip() else ""
        if not corpo and html:
            # Remove tags HTML para extrair texto puro
            corpo = re.sub(r'<[^>]+>', ' ', html)[:1000]
        content = f"{subject} {corpo}"
        content_norm = self._normalize(content)
        content_orig = content.lower()
        hash_id = hashlib.md5(content_norm.encode()).hexdigest()

        valor_match = self.valor_pattern.search(content)
        valor = valor_match.group(1) if valor_match else None

        banco = "Desconhecido"
        for nome_banco, pattern in self.bancos_patterns.items():
            if pattern.search(content):
                banco = nome_banco
                break

        return EmailData(content_norm=content_norm, content_orig=content_orig, hash_id=hash_id, valor=valor, banco=banco)

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
                ed = self._extract(msg.subject or "", msg.text or "", msg.html or "")
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
        return bool(partes) and all(p in content_norm for p in partes)

    def _match_nome_estrito(self, content_norm: str, partes: list) -> bool:
        return bool(partes) and all(
            re.search(rf"(?<![a-z]){re.escape(p)}(?![a-z])", content_norm) for p in partes
        )

    def _match_qualquer(self, ed: EmailData, partes: list) -> bool:
        """Tenta match no conteudo normalizado e no original (com acento)."""
        return (
            self._match_nome_estrito(ed.content_norm, partes) or
            self._match_nome(ed.content_norm, partes) or
            all(p in ed.content_orig for p in partes)
        )

    def search_payment(self, nome: str) -> Optional[dict]:
        nome_norm = self._normalize(nome)
        partes = [p for p in nome_norm.split() if len(p) >= 2]

        # 1. Busca no cache
        with self.lock:
            logger.warning(f"User {self.user_id}: cache tem {len(self.emails)} emails, buscando '{nome_norm}'")
            for ed in reversed(self.emails):
                if self._match_qualquer(ed, partes):
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
                    logger.warning(f"User {self.user_id}: termo '{termo}' -> {len(msgs)} emails")
                    for m in msgs:
                        logger.warning(f"  assunto: {m.subject}")
                    msgs_encontradas.extend(msgs)
                except Exception as exc:
                    logger.warning(f"User {self.user_id}: erro filtro '{termo}': {exc}")
                    # fallback sem filtro
                    msgs = list(mb.fetch(AND(date_gte=date.today()), mark_seen=False, limit=50))
                    logger.warning(f"User {self.user_id}: fallback -> {len(msgs)} emails")
                    msgs_encontradas.extend(msgs)
                    break

            mb.logout()

            vistos = set()
            for msg in msgs_encontradas:
                uid = getattr(msg, 'uid', id(msg))
                if uid in vistos:
                    continue
                vistos.add(uid)
                ed = self._extract(msg.subject or "", msg.text or "", msg.html or "")
                if self._match_qualquer(ed, partes):
                    with self.lock:
                        if ed.hash_id not in {e.hash_id for e in self.emails}:
                            self.emails.append(ed)
                            if len(self.emails) > MAX_EMAILS_CACHE:
                                self.emails = self.emails[-MAX_EMAILS_CACHE:]
                            self.stats.total_emails = len(self.emails)
                    self.stats.cache_hits += 1
                    logger.warning(f"User {self.user_id}: ENCONTRADO '{nome_norm}' - assunto: {msg.subject}")
                    return {"valor": ed.valor or "N/A", "banco": ed.banco}

            logger.warning(f"User {self.user_id}: '{nome_norm}' NAO encontrado em {len(msgs_encontradas)} emails")

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
