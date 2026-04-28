import threading
import time
from datetime import datetime, timedelta, date
from typing import Optional
from dataclasses import dataclass, field
from collections import defaultdict
import hashlib
import re
import unicodedata
from imap_tools import MailBox, AND
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


@dataclass
class EmailData:
    content: str
    timestamp: datetime
    hash_id: str
    valor: Optional[str] = None
    banco: Optional[str] = None


@dataclass
class CacheStats:
    total_emails: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    last_update: Optional[datetime] = None
    update_duration: float = 0.0


class OptimizedIMAPCache:
    def __init__(self, user_id: int, config: dict):
        self.user_id = user_id
        self.config = config
        self.emails: dict[str, EmailData] = {}
        self.nome_index: dict[str, set[str]] = defaultdict(set)
        self.stats = CacheStats()
        self.lock = threading.RLock()
        self.last_full_update = datetime.now() - timedelta(hours=2)
        self._stop = False

        self.valor_pattern = re.compile(r"R\$\s?([\d.,]+)", re.IGNORECASE)
        self.pix_nome_pattern = re.compile(
            r'(?:transfer[eê]ncia|pix|pagamento|recebeu?|recebido|enviado|depositado)\s+(?:de|do|da|por)?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)',
            re.IGNORECASE
        )
        self.bancos_patterns = {
            b.lower(): re.compile(rf"\b{re.escape(b)}\b", re.IGNORECASE)
            for b in [
                "Nubank", "PicPay", "Itau", "Bradesco", "Caixa", "Santander",
                "Inter", "C6 Bank", "Mercado Pago", "Next", "BTG", "Stone",
                "Sicoob", "Sicredi", "Banrisul", "BRB", "Safra", "Votorantim",
                "Neon", "Banco do Brasil", "BB", "Original", "Pan", "Agibank",
                "Pagbank", "PagSeguro", "Ame", "99Pay", "RecargaPay", "Digio",
                "Will Bank", "Banco Inter", "XP", "Modal", "Daycoval",
                "Rendimento", "Sofisa", "Banese", "Banpara", "Banestes"
            ]
        }

        # Inicia thread de atualização
        self._thread = threading.Thread(target=self._update_loop, daemon=True)
        self._thread.start()

    def _normalize(self, text: str) -> str:
        # Remove tags HTML antes de normalizar
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'&[a-z]+;', ' ', text)  # entidades HTML como &nbsp;
        return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower().strip()

    def _extract(self, content: str) -> EmailData:
        content_lower = content.lower()
        hash_id = hashlib.md5(content.encode()).hexdigest()
        valor_match = self.valor_pattern.search(content)
        valor = valor_match.group(1) if valor_match else None
        banco = "Desconhecido"
        for nome_banco, pattern in self.bancos_patterns.items():
            if pattern.search(content_lower):
                banco = nome_banco.title()
                break
        return EmailData(content=content, timestamp=datetime.now(), hash_id=hash_id, valor=valor, banco=banco)

    def _build_indexes(self):
        self.nome_index.clear()
        for hash_id, email_data in self.emails.items():
            for word in self._normalize(email_data.content).split():
                if len(word) >= 2:
                    self.nome_index[word].add(hash_id)

    def _fetch_emails(self, criterio, limit: int) -> list:
        try:
            mb = MailBox(self.config["imap_server"])
            mb.login(self.config["email_user"], self.config["email_pass"], initial_folder="INBOX")
            msgs = list(mb.fetch(criterio, mark_seen=False, limit=limit))
            mb.logout()
            return msgs
        except Exception as exc:
            logger.error(f"User {self.user_id}: Erro IMAP [{type(exc).__name__}]: {exc}")
            self._last_imap_error = str(exc)
            return []

    def update_full(self):
        start = time.time()
        try:
            since = (datetime.now() - timedelta(days=3)).date()
            msgs = self._fetch_emails(AND(date_gte=since), 500)
            with self.lock:
                self.emails.clear()
                for msg in msgs:
                    content = f"{msg.subject or ''} {msg.text or ''} {msg.html or ''}"
                    ed = self._extract(content)
                    self.emails[ed.hash_id] = ed
                self._build_indexes()
                self.stats.total_emails = len(self.emails)
                self.stats.last_update = datetime.now()
                self.last_full_update = datetime.now()
            self.stats.update_duration = time.time() - start
            logger.info(f"User {self.user_id}: {len(self.emails)} emails carregados em {self.stats.update_duration:.2f}s")
        except Exception as exc:
            logger.error(f"User {self.user_id}: Erro update_full: {exc}")

    def update_incremental(self):
        try:
            since = (datetime.now() - timedelta(days=3)).date()
            msgs = self._fetch_emails(AND(date_gte=since), 100)
            new_count = 0
            with self.lock:
                for msg in msgs:
                    content = f"{msg.subject or ''} {msg.text or ''} {msg.html or ''}"
                    ed = self._extract(content)
                    if ed.hash_id not in self.emails:
                        self.emails[ed.hash_id] = ed
                        new_count += 1
                if new_count > 0:
                    self._build_indexes()
                    self.stats.total_emails = len(self.emails)
                    self.stats.last_update = datetime.now()
                    logger.info(f"User {self.user_id}: +{new_count} novos emails")
        except Exception as exc:
            logger.error(f"User {self.user_id}: Erro update_incremental: {exc}")

    def _update_loop(self):
        # Atualização completa inicial
        self.update_full()
        while not self._stop:
            time.sleep(30)
            if self._stop:
                break
            # Atualização completa a cada 30 minutos
            if (datetime.now() - self.last_full_update).total_seconds() > 1800:
                self.update_full()
            else:
                self.update_incremental()

    def search_payment(self, nome: str) -> Optional[dict]:
        """Busca pagamento - primeiro no cache, depois direto no IMAP."""
        nome_norm = self._normalize(nome)
        partes = nome_norm.split()

        # Tenta no cache primeiro
        resultado = self._search_in_cache(nome_norm, partes)
        if resultado:
            return resultado

        # Fallback: busca direta no IMAP
        log_msg_fn = getattr(self, '_log', None)
        resultado = self._search_direct_imap(nome_norm, partes)
        if resultado:
            return resultado

        self.stats.cache_misses += 1
        return None

    def _search_in_cache(self, nome_norm: str, partes: list) -> Optional[dict]:
        with self.lock:
            if not self.emails:
                return None
            for ed in self.emails.values():
                content_norm = self._normalize(ed.content)
                if self._match_nome(content_norm, partes):
                    self.stats.cache_hits += 1
                    return {"valor": ed.valor or "N/A", "banco": ed.banco}
        return None

    def _match_nome(self, content_norm: str, partes: list) -> bool:
        """Pelo menos nome e sobrenome presentes como palavras completas."""
        if not partes:
            return False
        # Filtra partes com 3+ letras (ignora preposições como 'de', 'da')
        partes_sig = [p for p in partes if len(p) >= 3]
        if not partes_sig:
            partes_sig = partes
        matches = sum(1 for p in partes_sig if re.search(rf"\b{re.escape(p)}\b", content_norm))
        # Exige pelo menos 2 partes encontradas (nome + sobrenome)
        return matches >= min(2, len(partes_sig))

    def _search_direct_imap(self, nome_norm: str, partes: list) -> Optional[dict]:
        try:
            mb = MailBox(self.config["imap_server"])
            mb.login(self.config["email_user"], self.config["email_pass"], initial_folder="INBOX")
            since = (datetime.now() - timedelta(days=3)).date()
            termos = partes if len(partes) >= 2 else [nome_norm]
            msgs_encontradas = []
            for termo in termos[:2]:
                try:
                    msgs = list(mb.fetch(AND(date_gte=since, text=termo), mark_seen=False, limit=50))
                    msgs_encontradas.extend(msgs)
                    logger.info(f"User {self.user_id}: termo '{termo}' -> {len(msgs)} emails")
                except Exception:
                    msgs = list(mb.fetch(AND(date_gte=since), mark_seen=False, limit=200))
                    msgs_encontradas.extend(msgs)
                    break
            mb.logout()

            vistos = set()
            for msg in msgs_encontradas:
                uid = getattr(msg, 'uid', None) or id(msg)
                if uid in vistos:
                    continue
                vistos.add(uid)
                content = f"{msg.subject or ''} {msg.text or ''} {msg.html or ''}"
                content_norm = self._normalize(content)
                if self._match_nome(content_norm, partes):
                    ed = self._extract(content)
                    with self.lock:
                        self.emails[ed.hash_id] = ed
                        self._build_indexes()
                        self.stats.total_emails = len(self.emails)
                    self.stats.cache_hits += 1
                    logger.info(f"User {self.user_id}: encontrado '{nome_norm}' - assunto: {msg.subject}")
                    return {"valor": ed.valor or "N/A", "banco": ed.banco}

            logger.info(f"User {self.user_id}: '{nome_norm}' nao encontrado")
        except Exception as exc:
            logger.error(f"User {self.user_id}: Erro busca direta IMAP: {exc}")
        return None

    # Mantém compatibilidade com código existente
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
                "update_duration": f"{self.stats.update_duration:.2f}s",
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
