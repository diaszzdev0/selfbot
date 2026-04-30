import re
import threading
import time
import unicodedata
import hashlib
import email
from datetime import datetime, timedelta, date
from typing import Optional
from imap_tools import MailBox, AND
import logging

logger = logging.getLogger(__name__)

# Padrões específicos por banco
BANCOS_PATTERNS = {
    "Nubank": [
        r"nubank", r"nu\.com\.br", r"transferencia.*nubank", r"nubank.*transferencia"
    ],
    "PicPay": [r"picpay", r"pic\s*pay"],
    "Itau": [r"ita[uú]", r"itau\.com\.br"],
    "Bradesco": [r"bradesco", r"bradesco\.com\.br"],
    "Santander": [r"santander"],
    "Caixa": [r"caixa", r"cef\.gov\.br"],
    "Inter": [r"\bbanco\s*inter\b", r"\binter\b.*banco", r"bancointer"],
    "Mercado Pago": [r"mercado\s*pago", r"mercadopago"],
    "PagSeguro": [r"pagseguro", r"pagbank"],
    "C6 Bank": [r"c6\s*bank", r"c6bank"],
    "Next": [r"\bnext\b"],
    "Neon": [r"\bneon\b"],
    "BTG": [r"\bbtg\b"],
    "Stone": [r"\bstone\b"],
    "Sicoob": [r"sicoob"],
    "Sicredi": [r"sicredi"],
    "Banco do Brasil": [r"banco\s*do\s*brasil", r"\bbb\b"],
    "Original": [r"banco\s*original"],
    "Pan": [r"\bpan\b.*banco", r"banco\s*pan"],
    "Agibank": [r"agibank"],
    "Pagbank": [r"pagbank"],
    "Will Bank": [r"will\s*bank"],
    "XP": [r"\bxp\b.*investimentos"],
}

VALOR_RE = re.compile(
    r"R\$\s*(\d{1,3}(?:\.\d{3})*(?:,\d{2})?|\d+(?:,\d{2})?)"
    r"|(\d{1,3}(?:\.\d{3})*(?:,\d{2})?|\d+(?:,\d{2})?)\s*reais",
    re.IGNORECASE
)

NOME_RE = re.compile(
    r'(?:transfer[eê]ncia\s+(?:de|do|da)|recebeu?\s+de|pix\s+de|de)\s+'
    r'([A-Z][a-zA-Z\u00C0-\u00FF]{1,}(?:\s+[A-Za-z\u00C0-\u00FF]{2,})+)',
    re.IGNORECASE
)


def _normalize(text: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&[a-zA-Z0-9#]+;', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower().strip()


def _detectar_banco(content: str) -> str:
    content_lower = content.lower()
    for banco, patterns in BANCOS_PATTERNS.items():
        for p in patterns:
            if re.search(p, content_lower):
                return banco
    return "Desconhecido"


def _extrair_valor(content: str) -> str:
    match = VALOR_RE.search(content)
    if match:
        return match.group(1) or match.group(2) or "N/A"
    return "N/A"


def _match_nome(content_norm: str, partes: list) -> bool:
    """Busca flexível: aceita nome parcial (só nome ou nome+sobrenome)."""
    if not partes:
        return False
    partes_sig = [p for p in partes if len(p) >= 3]
    if not partes_sig:
        partes_sig = partes
    matches = sum(1 for p in partes_sig if re.search(rf"\b{re.escape(p)}\b", content_norm))
    # Aceita se encontrar pelo menos 2 partes ou todas se tiver só 1
    return matches >= min(2, len(partes_sig))


def _log_transferencia(content: str, subject: str, valores: dict) -> str:
    nome_match = NOME_RE.search(subject or '') or NOME_RE.search(content[:500])
    nome = nome_match.group(1).strip() if nome_match else 'Desconhecido'
    return (f"\U0001f4ac Pagamento: {nome} | "
            f"R$ {valores.get('valor','?')} | "
            f"{datetime.now().strftime('%d/%m as %H:%M')} | "
            f"{valores.get('banco','?')}")


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

    def _fetch_emails(self, mb) -> list:
        """Busca emails dos últimos 2 dias em todas as pastas relevantes."""
        pastas = ["[Gmail]/All Mail", "INBOX"]
        since = date.today() - timedelta(days=1)
        for pasta in pastas:
            try:
                mb.folder.set(pasta)
                msgs = list(mb.fetch(AND(date_gte=since), mark_seen=False, limit=300))
                self._log_msg(f"\U0001f4c2 '{pasta}': {len(msgs)} emails")
                return msgs
            except Exception as e:
                self._log_msg(f"\u26a0\ufe0f '{pasta}' falhou: {type(e).__name__}: {str(e)[:80]}")
                continue
        return []

    def _adicionar_email(self, msg, apenas_novo: bool = False) -> bool:
        uid = str(msg.uid) if msg.uid else None
        if apenas_novo and uid and uid in self.uids_vistos:
            return False
        content = f"{msg.subject or ''} {msg.text or ''} {msg.html or ''}"
        key = uid or hashlib.md5(content.encode()).hexdigest()
        norm = _normalize(content)
        valores = {
            "valor": _extrair_valor(content),
            "banco": _detectar_banco(content)
        }
        with self.lock:
            self.emails[key] = norm
            self.valores[key] = valores
            if uid:
                self.uids_vistos.add(uid)
            self.stats.total_emails = len(self.emails)
        return True

    def _loop(self):
        while not self._stop:
            try:
                mb = MailBox(self.config["imap_server"], timeout=30)
                mb.login(self.config["email_user"], self.config["email_pass"], initial_folder="INBOX")
                self._log_msg("\u2705 Login IMAP OK")

                msgs = self._fetch_emails(mb)
                for msg in msgs:
                    self._adicionar_email(msg)
                self._log_msg(f"\U0001f4e7 Cache pronto: {self.stats.total_emails} emails")

                while not self._stop:
                    time.sleep(30)
                    if self._stop:
                        break
                    try:
                        novos_msgs = self._fetch_emails(mb)
                        novos = 0
                        for msg in novos_msgs:
                            if self._adicionar_email(msg, apenas_novo=True):
                                novos += 1
                                content = f"{msg.subject or ''} {msg.text or ''} {msg.html or ''}"
                                uid = str(msg.uid) if msg.uid else None
                                key = uid or hashlib.md5(content.encode()).hexdigest()
                                self._log_msg(_log_transferencia(content, msg.subject, self.valores.get(key, {})))
                        if novos:
                            self._log_msg(f"\u2705 {novos} nova(s) transferencia(s) no cache")
                    except Exception:
                        break

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
        return {
            "total_emails": self.stats.total_emails,
            "hit_rate": "N/A",
            "last_update": None,
            "update_duration": "0s"
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
            self.caches.pop(user_id, None)

    def get_global_stats(self) -> dict:
        return {"active_caches": len(self.caches)}


imap_manager = IMAPCacheManager()
