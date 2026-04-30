import re
import threading
import time
import unicodedata
import json
import os
import logging
from datetime import datetime, timedelta, date
from typing import Optional
from imap_tools import MailBox, AND

logger = logging.getLogger(__name__)

CACHE_DIR = os.path.join(os.path.dirname(__file__), "imap_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

BANCOS_PATTERNS = {
    "Nubank":        [r"nubank"],
    "PicPay":        [r"picpay"],
    "Itau":          [r"ita[uú]"],
    "Bradesco":      [r"bradesco"],
    "Santander":     [r"santander"],
    "Caixa":         [r"caixa"],
    "Inter":         [r"banco\s*inter|bancointer"],
    "Mercado Pago":  [r"mercado\s*pago"],
    "PagSeguro":     [r"pagseguro|pagbank"],
    "C6 Bank":       [r"c6\s*bank"],
    "Next":          [r"\bnext\b"],
    "Neon":          [r"\bneon\b"],
    "BTG":           [r"\bbtg\b"],
    "Stone":         [r"\bstone\b"],
    "Sicoob":        [r"sicoob"],
    "Sicredi":       [r"sicredi"],
    "Banco do Brasil": [r"banco\s*do\s*brasil"],
    "Original":      [r"banco\s*original"],
    "Pan":           [r"banco\s*pan"],
    "Agibank":       [r"agibank"],
    "Will Bank":     [r"will\s*bank"],
    "XP":            [r"\bxp\b.*invest"],
}

VALOR_RE = re.compile(
    r"R\$\s*(\d{1,3}(?:\.\d{3})*(?:,\d{2})?|\d+(?:,\d{2})?)"
    r"|(\d{1,3}(?:\.\d{3})*,\d{2})\s*reais",
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
    cl = content.lower()
    for banco, patterns in BANCOS_PATTERNS.items():
        for p in patterns:
            if re.search(p, cl):
                return banco
    return "Desconhecido"


def _extrair_valor(content: str) -> str:
    m = VALOR_RE.search(content)
    return (m.group(1) or m.group(2)) if m else "N/A"


def _match_nome(content_norm: str, partes: list) -> bool:
    partes_sig = [p for p in partes if len(p) >= 3] or partes
    matches = sum(1 for p in partes_sig if re.search(rf"\b{re.escape(p)}\b", content_norm))
    return matches >= min(2, len(partes_sig))


class IMAPCache:
    """Cache persistente em disco + memória, incremental por UID."""

    def __init__(self, user_id: int):
        self.user_id = user_id
        self.path = os.path.join(CACHE_DIR, f"user_{user_id}.json")
        self.data: dict = {}   # uid -> {norm, valor, banco, ts}
        self._load()

    def _load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
                logger.info(f"User {self.user_id}: cache carregado ({len(self.data)} entradas)")
        except Exception:
            logger.warning(f"User {self.user_id}: cache corrompido, recriando")
            self.data = {}

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False)
        except Exception as e:
            logger.error(f"User {self.user_id}: erro ao salvar cache: {e}")

    def add(self, uid: str, content: str, subject: str) -> bool:
        """Adiciona email ao cache. Retorna True se era novo."""
        if uid in self.data:
            return False
        self.data[uid] = {
            "norm": _normalize(content),
            "valor": _extrair_valor(content),
            "banco": _detectar_banco(content),
            "subject": (subject or "")[:100],
            "ts": datetime.now().isoformat()
        }
        return True

    def cleanup(self):
        """Remove entradas com mais de 3 dias."""
        cutoff = (datetime.now() - timedelta(days=3)).isoformat()
        antes = len(self.data)
        self.data = {k: v for k, v in self.data.items() if v.get("ts", "") >= cutoff}
        removidos = antes - len(self.data)
        if removidos:
            logger.info(f"User {self.user_id}: {removidos} entradas antigas removidas do cache")

    def search(self, nome: str) -> Optional[dict]:
        nome_norm = _normalize(nome)
        partes = nome_norm.split()
        for entry in self.data.values():
            if _match_nome(entry["norm"], partes):
                return {"valor": entry["valor"], "banco": entry["banco"]}
        return None

    @property
    def total(self) -> int:
        return len(self.data)

    @property
    def uids(self) -> set:
        return set(self.data.keys())


class OptimizedIMAPCache:
    def __init__(self, user_id: int, config: dict):
        self.user_id = user_id
        self.config = config
        self.cache = IMAPCache(user_id)
        self._stop = False
        self._log = None
        self._lock = threading.Lock()
        self.stats = type('S', (), {
            'total_emails': self.cache.total,
            'cache_hits': 0,
            'cache_misses': 0
        })()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _log_msg(self, msg: str):
        if self._log:
            self._log(self.user_id, msg)
        logger.info(f"User {self.user_id}: {msg}")

    def _sincronizar(self, mb) -> int:
        """Busca apenas emails novos comparando UIDs."""
        since = date.today() - timedelta(days=1)
        pastas = ["[Gmail]/All Mail", "INBOX"]
        msgs = []
        for pasta in pastas:
            try:
                mb.folder.set(pasta)
                msgs = list(mb.fetch(AND(date_gte=since), mark_seen=False, limit=500))
                break
            except Exception as e:
                self._log_msg(f"\u26a0\ufe0f '{pasta}' falhou: {type(e).__name__}: {str(e)[:80]}")
                continue

        novos = 0
        for msg in msgs:
            uid = str(msg.uid) if msg.uid else None
            if not uid or uid in self.cache.uids:
                continue
            content = f"{msg.subject or ''} {msg.text or ''} {msg.html or ''}"
            if self.cache.add(uid, content, msg.subject):
                novos += 1
                entry = self.cache.data[uid]
                nome_match = NOME_RE.search(msg.subject or '') or NOME_RE.search(content[:500])
                nome = nome_match.group(1).strip() if nome_match else 'Desconhecido'
                self._log_msg(
                    f"\U0001f4e9 Novo e-mail: {msg.subject or 'sem assunto'} | "
                    f"{nome} | R$ {entry['valor']} | {entry['banco']}"
                )

        if novos:
            self.cache.cleanup()
            self.cache._save()
            self.stats.total_emails = self.cache.total

        return novos

    def _loop(self):
        while not self._stop:
            try:
                mb = MailBox(self.config["imap_server"], timeout=30)
                mb.login(self.config["email_user"], self.config["email_pass"], initial_folder="INBOX")
                self._log_msg("\u2705 Login IMAP OK")

                # Sincronização inicial
                novos = self._sincronizar(mb)
                self._log_msg(f"\U0001f4e7 Cache pronto: {self.cache.total} emails ({novos} novos)")

                # Loop incremental
                while not self._stop:
                    time.sleep(60)
                    if self._stop:
                        break
                    try:
                        novos = self._sincronizar(mb)
                        if novos:
                            self._log_msg(f"\u2705 {novos} nova(s) transferencia(s) adicionada(s)")
                    except Exception:
                        break  # reconecta

                mb.logout()
            except Exception as e:
                self._log_msg(f"\u26a0\ufe0f ERRO IMAP: {type(e).__name__}: {str(e)[:150]} — reconectando em 10s...")
                time.sleep(10)

    def search_payment(self, nome: str) -> Optional[dict]:
        with self._lock:
            resultado = self.cache.search(nome)
        if resultado:
            self.stats.cache_hits += 1
        else:
            self.stats.cache_misses += 1
        return resultado

    def search_payment_optimized(self, nome: str) -> Optional[dict]:
        return self.search_payment(nome)

    def get_stats(self) -> dict:
        return {
            "total_emails": self.cache.total,
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
