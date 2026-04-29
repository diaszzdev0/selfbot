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


def _extrair_log_transferencia(content: str, subject: str, valores: dict) -> str:
    """Monta a linha de log no formato padrao para o parser do frontend."""
    nome_match = re.search(r'transfer[e\u00ea]ncia de ([\w\s]+?)(?:\s+e o valor|\s+R\$|\||$)', content, re.IGNORECASE)
    nome_log = nome_match.group(1).strip() if nome_match else (subject or 'desconhecido')
    valor_match = re.search(r'R\$\s?[\d.,]+', content)
    valor_log = valor_match.group(0) if valor_match else f"R$ {valores.get('valor', '?')}"
    data_match = re.search(r'(\d{1,2}\s+[A-Z]{3}\s+\u00e0s\s+\d{2}:\d{2})', content, re.IGNORECASE)
    data_log = data_match.group(1) if data_match else datetime.now().strftime('%d/%m \u00e0s %H:%M')
    return f"\U0001f4ec transfer\u00eancia de {nome_log} | {valor_log} | {data_log} | banco: {valores.get('banco', '?')}"


class OptimizedIMAPCache:
    def __init__(self, user_id: int, config: dict):
        self.user_id = user_id
        self.config = config
        self.emails: dict[str, str] = {}   # hash -> content_norm
        self.valores: dict[str, dict] = {} # hash -> {valor, banco}
        self.uids_vistos: set = set()       # UIDs ja processados
        self.lock = threading.RLock()
        self._stop = False
        self.stats = type('S', (), {'total_emails': 0, 'cache_hits': 0, 'cache_misses': 0})()
        self._log = None
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _conectar(self):
        mb = MailBox(self.config["imap_server"], timeout=30)
        mb.login(self.config["email_user"], self.config["email_pass"], initial_folder="INBOX")
        return mb

    def _carregar_hoje(self, mb):
        """Carrega todos os e-mails de hoje na primeira conexao."""
        msgs = list(mb.fetch(AND(date_gte=date.today()), mark_seen=False, bulk=True))
        with self.lock:
            for msg in msgs:
                content = f"{msg.subject or ''} {msg.text or ''}"
                h = hashlib.md5(content.encode()).hexdigest()
                self.emails[h] = _normalize(content)
                self.valores[h] = _extrair_banco_valor(content)
                if msg.uid:
                    self.uids_vistos.add(msg.uid)
            self.stats.total_emails = len(self.emails)
        if self._log:
            self._log(self.user_id, f"\U0001f4e7 IMAP pronto: {len(self.emails)} e-mails carregados hoje")

    def _checar_novos(self, mb):
        """
        Detecta novos e-mails pelo UID (lidos ou nao lidos).
        Todos os e-mails do dia ficam no cache para busca de pagamentos.
        Apenas os que ainda nao foram vistos disparam o log de transferencia.
        """
        try:
            # Busca TODOS os e-mails de hoje (lidos e nao lidos)
            msgs = list(mb.fetch(AND(date_gte=date.today()), mark_seen=False, bulk=True))
            novos = 0
            for msg in msgs:
                content = f"{msg.subject or ''} {msg.text or ''}"
                h = hashlib.md5(content.encode()).hexdigest()
                with self.lock:
                    # Sempre mantém no cache para busca de pagamentos
                    if h not in self.emails:
                        self.emails[h] = _normalize(content)
                        self.valores[h] = _extrair_banco_valor(content)
                        self.stats.total_emails = len(self.emails)
                    # Só loga como nova transferência se o UID ainda não foi visto
                    if msg.uid and msg.uid not in self.uids_vistos:
                        self.uids_vistos.add(msg.uid)
                        novos += 1
                        if self._log:
                            linha = _extrair_log_transferencia(content, msg.subject, self.valores[h])
                            self._log(self.user_id, linha)
            return novos
        except Exception as exc:
            raise exc

    def _loop(self):
        """Loop principal: conecta, carrega hoje, depois polling a cada 30s."""
        while not self._stop:
            try:
                if self._log:
                    self._log(self.user_id, f"📧 Conectando IMAP: {self.config.get('imap_server')} | {self.config.get('email_user')}")
                mb = self._conectar()
                if self._log:
                    self._log(self.user_id, "✅ Login IMAP OK")
                self._carregar_hoje(mb)
                while not self._stop:
                    time.sleep(30)
                    if self._stop:
                        break
                    novos = self._checar_novos(mb)
                    if novos and self._log:
                        self._log(self.user_id, f"✅ {novos} nova(s) transferência(s) detectada(s)")
                mb.logout()
            except Exception as exc:
                logger.error(f"User {self.user_id}: Erro IMAP [{type(exc).__name__}]: {exc}")
                if self._log:
                    self._log(self.user_id, f"⚠️ IMAP erro: [{type(exc).__name__}] {str(exc)[:200]}")
                time.sleep(10)

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
