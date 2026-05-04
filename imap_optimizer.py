import re
import threading
import time
import unicodedata
import json
import os
import logging
from datetime import datetime, timedelta, date, timezone
from typing import Optional
from imap_tools import MailBox, AND

logger = logging.getLogger(__name__)

CACHE_DIR = os.path.join(os.path.dirname(__file__), "imap_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

BANCOS_PATTERNS = {
    "Nubank":          [r"nubank"],
    "PicPay":          [r"picpay"],
    "Itau":            [r"ita[u\u00fa]"],
    "Bradesco":        [r"bradesco"],
    "Santander":       [r"santander"],
    "Caixa":           [r"caixa", r"caixa\s*econ\u00f4mica", r"cef\.gov\.br",
                        r"cr\u00e9dito\s*recebido", r"pix\s*recebido",
                        r"transfer\u00eancia\s*realizada\s*via\s*pix"],
    "Inter":           [r"banco\s*inter|bancointer"],
    "Mercado Pago":    [r"mercado\s*pago"],
    "PagSeguro":       [r"pagseguro|pagbank"],
    "C6 Bank":         [r"c6\s*bank"],
    "Next":            [r"\bnext\b"],
    "Neon":            [r"\bneon\b"],
    "BTG":             [r"\bbtg\b"],
    "Stone":           [r"\bstone\b"],
    "Sicoob":          [r"sicoob"],
    "Sicredi":         [r"sicredi"],
    "Banco do Brasil": [r"banco\s*do\s*brasil"],
    "Original":        [r"banco\s*original"],
    "Pan":             [r"banco\s*pan"],
    "Agibank":         [r"agibank"],
    "Will Bank":       [r"will\s*bank"],
    "XP":              [r"\bxp\b.*invest"],
}

VALOR_RE = re.compile(
    r"valor\s*creditado\s*[:\s]*R\$\s*([\d.,]+)"
    r"|valor\s*[:\-]\s*R\$\s*([\d.,]+)"
    r"|R\$\s*(\d{1,3}(?:\.\d{3})*(?:,\d{2})?|\d+(?:,\d{2})?)"
    r"|(\d{1,3}(?:\.\d{3})*,\d{2})\s*reais",
    re.IGNORECASE
)

NOME_PADROES = [
    r"transfer[e\u00ea]ncia\s+de\s+([A-Za-z\u00C0-\u00FF][A-Za-z\u00C0-\u00FF\-\'\s]{2,60})",
    r"voc[e\u00ea]\s+recebeu.*?de\s+([A-Za-z\u00C0-\u00FF][A-Za-z\u00C0-\u00FF\-\'\s]{2,60})",
    r"pix\s+de\s+([A-Za-z\u00C0-\u00FF][A-Za-z\u00C0-\u00FF\-\'\s]{2,60})",
    r"recebido\s+de\s+([A-Za-z\u00C0-\u00FF][A-Za-z\u00C0-\u00FF\-\'\s]{2,60})",
    r"origem\s*[:\s]+([A-Za-z\u00C0-\u00FF][A-Za-z\u00C0-\u00FF\-\'\s]{2,60})",
    r"favorecido\s*[:\s]+([A-Za-z\u00C0-\u00FF][A-Za-z\u00C0-\u00FF\-\'\s]{2,60})",
    r"pagador\s*[:\s]+([A-Za-z\u00C0-\u00FF][A-Za-z\u00C0-\u00FF\-\'\s]{2,60})",
    r"remetente\s*[:\s]+([A-Za-z\u00C0-\u00FF][A-Za-z\u00C0-\u00FF\-\'\s]{2,60})",
]


def _normalize(text: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&[a-zA-Z0-9#]+;', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower().strip()


def _detectar_banco(content: str) -> str:
    cl = content.lower()
    for banco, patterns in BANCOS_PATTERNS.items():
        for p in patterns:
            if re.search(p, cl, re.IGNORECASE):
                return banco
    return "Desconhecido"


def _extrair_valor(content: str) -> str:
    padroes = [
        r'valor\s*creditado\s*[:\s]*R\$\s*([0-9]+(?:[\.,][0-9]{1,2})?)',
        r'valor\s*[:\-]\s*R\$\s*([0-9]+(?:[\.,][0-9]{1,2})?)',
        r'R\$\s*([0-9]+(?:[\.,][0-9]{1,2})?)',
        r'([0-9]+(?:[\.,][0-9]{1,2})?)\s*(?:reais|R\$)',
        r'\b0[\.,]\d{1,2}\b',
    ]
    for padrao in padroes:
        m = re.search(padrao, content, re.IGNORECASE)
        if m:
            valor = (m.group(1) if m.lastindex else m.group(0)).strip()
            valor = valor.replace('.', ',')
            if ',' not in valor:
                valor += ',00'
            elif len(valor.split(',')[1]) == 1:
                valor += '0'
            return valor
    return "N/A"


def _extrair_pagador(content: str) -> str:
    # Limpa HTML mas mantém o texto original com acentos e maiusculas
    corpo = re.sub(r'<[^>]+>', ' ', content)
    corpo = re.sub(r'&[a-zA-Z0-9#]+;', ' ', corpo)
    corpo = re.sub(r'\s+', ' ', corpo)
    for padrao in NOME_PADROES:
        m = re.search(padrao, corpo, flags=re.IGNORECASE)
        if m:
            nome = m.group(1).strip()
            # Remove lixo no final (ex: "e o valor", "via Pix", pontuacao)
            nome = re.split(r'\s+e\s+o\s+|\s+via\s+|\s+no\s+valor|[,;\.]', nome, flags=re.IGNORECASE)[0].strip()
            palavras = nome.split()
            # Filtra palavras validas (so letras, minimo 2 chars)
            palavras = [p for p in palavras if re.match(r'^[A-Za-z\u00C0-\u00FF\-]+$', p) and len(p) >= 2]
            if len(palavras) >= 2:
                return ' '.join(palavras).title()
    return "Desconhecido"


SOBRENOMES_COMUNS = {
    'silva', 'santos', 'oliveira', 'souza', 'sousa', 'pereira', 'costa',
    'ferreira', 'alves', 'lima', 'gomes', 'ribeiro', 'carvalho', 'martins',
    'rodrigues', 'almeida', 'nascimento', 'araujo', 'melo', 'barbosa',
    'rocha', 'dias', 'moura', 'nunes', 'lopes', 'cardoso', 'mendes',
    'maria', 'jose', 'joao', 'ana', 'da', 'de', 'do', 'dos', 'das'
}


def _match_nome(content_norm: str, partes: list) -> bool:
    # Filtra partes significativas (>= 4 chars e nao sao palavras comuns)
    partes_sig = [p for p in partes if len(p) >= 4 and p not in SOBRENOMES_COMUNS]
    partes_todas = [p for p in partes if len(p) >= 3]

    if not partes_sig:
        # Se nao tem partes fortes, exige 2 partes quaisquer
        matches = sum(1 for p in partes_todas if re.search(rf"\b{re.escape(p)}\b", content_norm))
        return matches >= 2

    # Precisa achar pelo menos 1 parte forte
    matches_sig = sum(1 for p in partes_sig if re.search(rf"\b{re.escape(p)}\b", content_norm))
    if matches_sig == 0:
        return False

    # E pelo menos 2 partes no total
    matches_total = sum(1 for p in partes_todas if re.search(rf"\b{re.escape(p)}\b", content_norm))
    return matches_total >= 2


class IMAPCache:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.path = os.path.join(CACHE_DIR, f"user_{user_id}.json")
        self.data: dict = {}
        self._load()

    def _load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
                for uid in self.data:
                    self.data[uid].setdefault("usado", False)
                    self.data[uid].setdefault("ts", "")
                self.cleanup()
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

    def add(self, uid: str, content: str, subject: str, date_obj=None) -> bool:
        if uid in self.data:
            return False
        # Usa a data real do email em UTC
        if date_obj:
            if hasattr(date_obj, 'tzinfo') and date_obj.tzinfo:
                ts = date_obj.astimezone(timezone.utc).replace(tzinfo=None).isoformat()
            else:
                ts = date_obj.isoformat()
        else:
            ts = datetime.utcnow().isoformat()
        self.data[uid] = {
            "norm": _normalize(content),
            "valor": _extrair_valor(content),
            "banco": _detectar_banco(content),
            "subject": (subject or "")[:100],
            "ts": ts,
            "usado": False
        }
        return True

    def cleanup(self):
        cutoff = (datetime.utcnow() - timedelta(days=3)).isoformat()
        antes = len(self.data)
        self.data = {k: v for k, v in self.data.items() if v.get("ts", "") >= cutoff}
        removidos = antes - len(self.data)
        if removidos:
            logger.info(f"User {self.user_id}: {removidos} entradas antigas removidas (3-day window)")

    def search(self, nome: str) -> Optional[dict]:
        nome_norm = _normalize(nome)
        partes = nome_norm.split()
        # Busca em todos os emails nao usados (sem restricao de janela)
        # ordenado do mais recente pro mais antigo
        matches = []
        for uid, entry in self.data.items():
            if entry.get("usado"):
                continue
            if _match_nome(entry["norm"], partes):
                matches.append((entry.get("ts", ""), uid, entry))
        if matches:
            matches.sort(key=lambda x: x[0], reverse=True)
            _, uid, entry = matches[0]
            self.data[uid]["usado"] = True
            self._save()
            return {"valor": entry["valor"], "banco": entry["banco"]}
        return None

    def search_debug(self, nome: str) -> list:
        nome_norm = _normalize(nome)
        partes = [p for p in nome_norm.split() if len(p) >= 3]
        trechos = []
        for uid, entry in list(self.data.items()):
            for parte in partes:
                idx = entry["norm"].find(parte)
                if idx != -1:
                    trecho = entry["norm"][max(0, idx-30):idx+60]
                    trechos.append(f"[{entry['banco']}|usado={entry.get('usado')}|ts={entry['ts'][:16]}] ...{trecho}...")
                    break
        return trechos[:5]

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

    def _sincronizar(self, mb=None) -> int:
        since = date.today() - timedelta(days=3)
        pastas_tentar = ["[Gmail]/All Mail", "INBOX"]

        uids_novos_global = set()
        msgs_novas = []

        # Reconecta sempre para evitar conexão morta
        try:
            mb = MailBox(self.config["imap_server"], timeout=30)
            mb.login(self.config["email_user"], self.config["email_pass"], initial_folder="INBOX")
            self._log_msg("\u2705 IMAP conectado")
        except Exception as e:
            self._log_msg(f"\u26a0\ufe0f Falha ao conectar IMAP: {type(e).__name__}: {str(e)[:150]}")
            return 0

        try:
            todas = [f.name for f in mb.folder.list()]
            for p in todas:
                if p not in pastas_tentar:
                    pastas_tentar.append(p)
        except Exception:
            pass

        for pasta in pastas_tentar:
            try:
                mb.folder.set(pasta)
                msgs = list(mb.fetch(AND(date_gte=since), mark_seen=False, limit=500))
                for msg in msgs:
                    uid = str(msg.uid) if msg.uid else None
                    if uid and uid not in self.cache.uids and uid not in uids_novos_global:
                        uids_novos_global.add(uid)
                        msgs_novas.append(msg)
                break
            except Exception as e:
                self._log_msg(f"\u26a0\ufe0f '{pasta}' falhou: {type(e).__name__}: {str(e)[:60]}")
                continue

        try:
            mb.logout()
        except Exception:
            pass

        novos = 0
        for msg in msgs_novas:
            uid = str(msg.uid)
            content = f"{msg.subject or ''} {msg.text or ''} {msg.html or ''}"
            date_obj = msg.date if hasattr(msg, 'date') and msg.date else None
            if self.cache.add(uid, content, msg.subject, date_obj):
                novos += 1
                entry = self.cache.data[uid]
                nome = _extrair_pagador(content)
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
        primeiro = True
        while not self._stop:
            try:
                novos = self._sincronizar()
                self.stats.total_emails = self.cache.total
                if primeiro:
                    primeiro = False
                    self._log_msg(f"\U0001f4e7 Cache pronto: {self.cache.total} emails ({novos} novos)")
                elif novos:
                    self._log_msg(f"\u2705 {novos} nova(s) transferencia(s) adicionada(s)")
            except Exception as e:
                self._log_msg(f"\u26a0\ufe0f ERRO loop IMAP: {type(e).__name__}: {str(e)[:150]}")
            time.sleep(10)

    def search_payment(self, nome: str) -> Optional[dict]:
        with self._lock:
            resultado = self.cache.search(nome)
        if resultado:
            self.stats.cache_hits += 1
        else:
            self.stats.cache_misses += 1
            # Debug: mostra trechos dos emails que contem partes do nome
            with self._lock:
                trechos = self.cache.search_debug(nome)
            if trechos:
                for t in trechos:
                    self._log_msg(f"\U0001f50d Debug match: {t}")
            else:
                self._log_msg(f"\U0001f50d Debug: nenhuma parte de '{nome}' encontrada nos emails")
        return resultado

    def search_payment_optimized(self, nome: str) -> Optional[dict]:
        return self.search_payment(nome)

    def search_debug(self, nome: str) -> list:
        with self._lock:
            return self.cache.search_debug(nome)

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
