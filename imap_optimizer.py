import re
import unicodedata
from datetime import date
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
    """Busca direto no IMAP sem cache."""

    def __init__(self, user_id: int, config: dict):
        self.user_id = user_id
        self.config = config
        self.stats = type('S', (), {'total_emails': 0, 'cache_hits': 0, 'cache_misses': 0})()

    def search_payment_optimized(self, nome: str) -> Optional[dict]:
        return self.search_payment(nome)

    def search_payment(self, nome: str) -> Optional[dict]:
        nome_norm = _normalize(nome)
        partes = nome_norm.split()
        if not partes:
            return None
        try:
            mb = MailBox(self.config["imap_server"], timeout=20)
            mb.login(self.config["email_user"], self.config["email_pass"], initial_folder="INBOX")
            # Busca pelo assunto com o primeiro nome - rapido no Gmail
            try:
                msgs = list(mb.fetch(AND(date_gte=date.today(), subject=partes[0]), mark_seen=False, limit=20))
            except Exception:
                msgs = list(mb.fetch(AND(date_gte=date.today()), mark_seen=False, limit=100))
            mb.logout()
            logger.info(f"User {self.user_id}: {len(msgs)} emails para '{nome_norm}'")
            for msg in msgs:
                content = f"{msg.subject or ''} {msg.text or ''} {msg.html or ''}"
                if _match_nome(_normalize(content), partes):
                    logger.info(f"User {self.user_id}: encontrado '{nome_norm}'")
                    self.stats.cache_hits += 1
                    return _extrair_banco_valor(content)
            self.stats.cache_misses += 1
        except Exception as exc:
            logger.error(f"User {self.user_id}: Erro IMAP [{type(exc).__name__}]: {exc}")
        return None

    def get_stats(self) -> dict:
        return {"total_emails": 0, "hit_rate": "N/A", "last_update": None, "update_duration": "0s"}

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
        self.caches.pop(user_id, None)

    def get_global_stats(self) -> dict:
        return {"active_caches": len(self.caches)}


imap_manager = IMAPCacheManager()
