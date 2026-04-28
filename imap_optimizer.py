import re
import unicodedata
from datetime import datetime
from typing import Optional
from imap_tools import MailBox, AND
import logging

logger = logging.getLogger(__name__)

BANCOS = [
    "Nubank", "PicPay", "Itau", "Bradesco", "Caixa", "Santander",
    "Inter", "C6 Bank", "Mercado Pago", "Next", "BTG", "Stone",
    "Sicoob", "Sicredi", "Banrisul", "BRB", "Safra", "Votorantim",
    "Neon", "Banco do Brasil", "BB", "Original", "Pan", "Agibank",
    "Pagbank", "PagSeguro", "Ame", "99Pay", "RecargaPay", "Digio",
    "Will Bank", "Banco Inter", "XP", "Modal", "Daycoval",
    "Rendimento", "Sofisa", "Banese", "Banpara", "Banestes"
]

VALOR_RE = re.compile(r"R\$\s?([\d.,]+)", re.IGNORECASE)
BANCOS_RE = {b.lower(): re.compile(rf"\b{re.escape(b)}\b", re.IGNORECASE) for b in BANCOS}


def _normalize(text: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&[a-zA-Z]+;', ' ', text)
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower().strip()


def _match_nome(content_norm: str, partes: list) -> bool:
    partes_sig = [p for p in partes if len(p) >= 3] or partes
    matches = sum(1 for p in partes_sig if p in content_norm)
    return matches >= min(2, len(partes_sig))


def _extrair_banco_valor(content: str) -> dict:
    valor_match = VALOR_RE.search(content)
    valor = valor_match.group(1) if valor_match else "N/A"
    banco = "Desconhecido"
    content_lower = content.lower()
    for nome_banco, pattern in BANCOS_RE.items():
        if pattern.search(content_lower):
            banco = nome_banco.title()
            break
    return {"valor": valor, "banco": banco}


class OptimizedIMAPCache:
    """Busca direta no IMAP sem cache - apenas emails de hoje."""

    def __init__(self, user_id: int, config: dict):
        self.user_id = user_id
        self.config = config
        self.stats = type('S', (), {'total_emails': 0, 'cache_hits': 0, 'cache_misses': 0, 'last_update': None})()

    def search_payment_optimized(self, nome: str) -> Optional[dict]:
        return self.search_payment(nome)

    def search_payment(self, nome: str) -> Optional[dict]:
        nome_norm = _normalize(nome)
        partes = nome_norm.split()
        try:
            mb = MailBox(self.config["imap_server"])
            mb.login(self.config["email_user"], self.config["email_pass"], initial_folder="INBOX")
            from datetime import date
            msgs = list(mb.fetch(AND(date_gte=date.today()), mark_seen=False, limit=200, bulk=True))
            mb.logout()
            logger.info(f"User {self.user_id}: {len(msgs)} emails hoje para '{nome_norm}'")
            self.stats.total_emails = len(msgs)
            for msg in msgs:
                content = f"{msg.subject or ''} {msg.text or ''} {msg.html or ''}"
                if _match_nome(_normalize(content), partes):
                    logger.info(f"User {self.user_id}: encontrado '{nome_norm}' - {msg.subject}")
                    self.stats.cache_hits += 1
                    return _extrair_banco_valor(content)
            logger.info(f"User {self.user_id}: '{nome_norm}' nao encontrado")
            self.stats.cache_misses += 1
        except Exception as exc:
            logger.error(f"User {self.user_id}: Erro IMAP [{type(exc).__name__}]: {exc}")
        return None

    def get_stats(self) -> dict:
        return {
            "total_emails": self.stats.total_emails,
            "cache_hits": self.stats.cache_hits,
            "cache_misses": self.stats.cache_misses,
            "hit_rate": "N/A",
            "last_update": None,
            "update_duration": "0s",
        }

    def stop(self):
        pass


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
        return {"active_caches": len(self.caches)}


imap_manager = IMAPCacheManager()
