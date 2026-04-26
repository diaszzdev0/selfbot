import asyncio
import threading
import time
from datetime import datetime, timedelta, date
from typing import Optional
from dataclasses import dataclass
from collections import defaultdict, deque
import hashlib
import re
import unicodedata
from imap_tools import MailBox, AND
import logging

# Configuração de logging otimizada
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@dataclass
class EmailData:
    """Estrutura otimizada para dados de email"""
    content: str
    timestamp: datetime
    hash_id: str
    valor: Optional[str] = None
    banco: Optional[str] = None
    nome_encontrado: Optional[str] = None

@dataclass
class CacheStats:
    """Estatísticas do cache para monitoramento"""
    total_emails: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    last_update: datetime = None
    update_duration: float = 0.0

class OptimizedIMAPCache:
    """Sistema de cache IMAP otimizado para alta performance"""
    
    def __init__(self, user_id: int, config: dict):
        self.user_id = user_id
        self.config = config
        self.emails: dict[str, EmailData] = {}
        self.nome_index: dict[str, set[str]] = defaultdict(set)
        self.valor_index: dict[str, set[str]] = defaultdict(set)
        self.banco_index: dict[str, set[str]] = defaultdict(set)
        self.stats = CacheStats()
        self.lock = threading.RLock()
        self.last_full_update = datetime.now() - timedelta(hours=1)
        self.incremental_updates = deque(maxlen=1000)
        self.connection_pool = []
        self.max_pool_size = 3
        self.update_in_progress = False
        
        # Cache de nomes processados para evitar reprocessamento
        self.processed_names: dict[str, datetime] = {}
        self.name_cache_duration = timedelta(minutes=5)
        
        # Padrões pré-compilados para performance
        self.valor_pattern = re.compile(r"R\$\s?([\d.,]+)", re.IGNORECASE)
        self.bancos_patterns = {
            banco.lower(): re.compile(rf"\b{re.escape(banco)}\b", re.IGNORECASE)
            for banco in ["Nubank", "PicPay", "Itau", "Bradesco", "Caixa", "Santander", 
                         "Inter", "C6 Bank", "Mercado Pago", "Next", "BTG", "Stone"]
        }
        
    def _get_connection(self) -> Optional[MailBox]:
        """Pool de conexões IMAP para melhor performance"""
        try:
            if self.connection_pool:
                return self.connection_pool.pop()
            
            if len(self.connection_pool) < self.max_pool_size:
                mb = MailBox(self.config["imap_server"])
                mb.login(self.config["email_user"], self.config["email_pass"], initial_folder="INBOX")
                return mb
        except Exception:
            logger.error(f"Erro ao criar conexão IMAP para user {self.user_id}")
        return None
    
    def _return_connection(self, mb: MailBox):
        """Retorna conexão para o pool"""
        try:
            if len(self.connection_pool) < self.max_pool_size:
                self.connection_pool.append(mb)
            else:
                mb.logout()
        except Exception:
            pass
    
    def _normalize_name(self, name: str) -> str:
        """Normalização otimizada de nomes"""
        return unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower().strip()
    
    def _extract_email_data(self, content: str) -> EmailData:
        """Extração otimizada de dados do email"""
        content_lower = content.lower()
        hash_id = hashlib.md5(content.encode()).hexdigest()
        
        # Extração de valor
        valor_match = self.valor_pattern.search(content)
        valor = valor_match.group(1) if valor_match else None
        
        # Detecção de banco
        banco = "Desconhecido"
        for banco_nome, pattern in self.bancos_patterns.items():
            if pattern.search(content_lower):
                banco = banco_nome.title()
                break
        
        return EmailData(
            content=content,
            timestamp=datetime.now(),
            hash_id=hash_id,
            valor=valor,
            banco=banco
        )
    
    def _build_indexes(self):
        """Constrói índices otimizados para busca rápida"""
        self.nome_index.clear()
        self.valor_index.clear()
        self.banco_index.clear()
        
        for hash_id, email_data in self.emails.items():
            content_norm = self._normalize_name(email_data.content)
            words = content_norm.split()
            
            # Índice por palavras do conteúdo
            for word in words:
                if len(word) >= 2:  # Ignora palavras muito pequenas
                    self.nome_index[word].add(hash_id)
            
            # Índice por valor
            if email_data.valor:
                self.valor_index[email_data.valor].add(hash_id)
            
            # Índice por banco
            if email_data.banco:
                self.banco_index[email_data.banco.lower()].add(hash_id)
    
    async def update_cache_incremental(self) -> bool:
        """Atualização incremental do cache (últimos emails)"""
        if self.update_in_progress:
            return False
            
        self.update_in_progress = True
        start_time = time.time()
        
        try:
            mb = self._get_connection()
            if not mb:
                return False
            
            # Busca apenas emails dos últimos 30 minutos para atualização incremental
            since_time = datetime.now() - timedelta(minutes=30)
            criterio = AND(date_gte=since_time.date())
            
            msgs = list(mb.fetch(criterio, mark_seen=False, limit=50))
            new_emails = 0
            
            with self.lock:
                for msg in msgs:
                    content = f"{msg.subject or ''} {msg.text or ''} {msg.html or ''}"
                    email_data = self._extract_email_data(content)
                    
                    if email_data.hash_id not in self.emails:
                        self.emails[email_data.hash_id] = email_data
                        new_emails += 1
                
                if new_emails > 0:
                    self._build_indexes()
                    self.stats.total_emails = len(self.emails)
                    self.stats.last_update = datetime.now()
            
            self._return_connection(mb)
            
            duration = time.time() - start_time
            self.stats.update_duration = duration
            
            if new_emails > 0:
                logger.info(f"User {self.user_id}: Cache incremental - {new_emails} novos emails em {duration:.2f}s")
            
            return True
            
        except Exception as exc:
            logger.error(f"Erro na atualização incremental para user {self.user_id}: {exc}")
            return False
        finally:
            self.update_in_progress = False
    
    async def update_cache_full(self) -> bool:
        """Atualização completa do cache"""
        if self.update_in_progress:
            return False
            
        self.update_in_progress = True
        start_time = time.time()
        
        try:
            mb = self._get_connection()
            if not mb:
                return False
            
            # Busca emails dos últimos 2 dias
            since_date = date.today() - timedelta(days=2)
            criterio = AND(date_gte=since_date)
            
            msgs = list(mb.fetch(criterio, mark_seen=False, limit=500))
            
            with self.lock:
                self.emails.clear()
                
                for msg in msgs:
                    content = f"{msg.subject or ''} {msg.text or ''} {msg.html or ''}"
                    email_data = self._extract_email_data(content)
                    self.emails[email_data.hash_id] = email_data
                
                self._build_indexes()
                self.stats.total_emails = len(self.emails)
                self.stats.last_update = datetime.now()
                self.last_full_update = datetime.now()
            
            self._return_connection(mb)
            
            duration = time.time() - start_time
            self.stats.update_duration = duration
            
            logger.info(f"User {self.user_id}: Cache completo - {len(self.emails)} emails em {duration:.2f}s")
            return True
            
        except Exception as exc:
            logger.error(f"Erro na atualização completa para user {self.user_id}: {exc}")
            return False
        finally:
            self.update_in_progress = False
    
    def search_payment_optimized(self, nome: str) -> Optional[dict]:
        """Busca otimizada de pagamento com múltiplas estratégias"""
        nome_norm = self._normalize_name(nome)
        
        # Cache de nomes recentemente processados
        if nome_norm in self.processed_names:
            last_check = self.processed_names[nome_norm]
            if datetime.now() - last_check < self.name_cache_duration:
                self.stats.cache_hits += 1
                return None  # Evita spam de verificações
        
        self.processed_names[nome_norm] = datetime.now()
        
        with self.lock:
            if not self.emails:
                self.stats.cache_misses += 1
                return None
            
            # Estratégia 1: Busca por nome completo
            if nome_norm in self.nome_index:
                for hash_id in self.nome_index[nome_norm]:
                    email_data = self.emails[hash_id]
                    if self._verify_name_match(email_data.content, nome, nome_norm):
                        self.stats.cache_hits += 1
                        return {
                            "valor": email_data.valor or "N/A",
                            "banco": email_data.banco,
                            "timestamp": email_data.timestamp
                        }
            
            # Estratégia 2: Busca por partes do nome
            partes = nome_norm.split()
            if len(partes) >= 2:
                primeiro, ultimo = partes[0], partes[-1]
                
                # Intersecção de índices para busca eficiente
                candidatos_primeiro = self.nome_index.get(primeiro, set())
                candidatos_ultimo = self.nome_index.get(ultimo, set())
                candidatos = candidatos_primeiro.intersection(candidatos_ultimo)
                
                for hash_id in candidatos:
                    email_data = self.emails[hash_id]
                    if self._verify_name_match(email_data.content, nome, nome_norm):
                        self.stats.cache_hits += 1
                        return {
                            "valor": email_data.valor or "N/A",
                            "banco": email_data.banco,
                            "timestamp": email_data.timestamp
                        }
            
            # Estratégia 3: Busca fuzzy (mais lenta, apenas se necessário)
            if len(partes) == 1 or len(nome_norm) >= 8:
                for hash_id, email_data in self.emails.items():
                    if self._fuzzy_name_match(email_data.content, nome, nome_norm):
                        self.stats.cache_hits += 1
                        return {
                            "valor": email_data.valor or "N/A",
                            "banco": email_data.banco,
                            "timestamp": email_data.timestamp
                        }
        
        self.stats.cache_misses += 1
        return None
    
    def _verify_name_match(self, content: str, nome_original: str, nome_norm: str) -> bool:
        """Verificação otimizada de correspondência de nome"""
        content_norm = self._normalize_name(content)
        content_lower = content.lower()
        nome_lower = nome_original.lower()
        
        return (
            nome_norm in content_norm or
            nome_lower in content_lower or
            self._check_name_parts(content_norm, nome_norm) or
            self._check_name_parts(content_lower, nome_lower)
        )
    
    def _check_name_parts(self, content: str, nome: str) -> bool:
        """Verifica partes do nome no conteúdo"""
        partes = nome.split()
        if len(partes) < 2:
            return False
        
        primeiro, ultimo = partes[0], partes[-1]
        return primeiro in content and ultimo in content
    
    def _fuzzy_name_match(self, content: str, nome_original: str, nome_norm: str) -> bool:
        """Correspondência fuzzy para nomes similares"""
        content_norm = self._normalize_name(content)
        
        # Verifica se pelo menos 70% das palavras do nome estão no conteúdo
        palavras_nome = nome_norm.split()
        if len(palavras_nome) < 2:
            return False
        
        palavras_encontradas = sum(1 for palavra in palavras_nome if palavra in content_norm)
        return (palavras_encontradas / len(palavras_nome)) >= 0.7
    
    def get_stats(self) -> dict:
        """Retorna estatísticas do cache"""
        with self.lock:
            hit_rate = 0
            if self.stats.cache_hits + self.stats.cache_misses > 0:
                hit_rate = self.stats.cache_hits / (self.stats.cache_hits + self.stats.cache_misses)
            
            return {
                "total_emails": self.stats.total_emails,
                "cache_hits": self.stats.cache_hits,
                "cache_misses": self.stats.cache_misses,
                "hit_rate": f"{hit_rate:.2%}",
                "last_update": self.stats.last_update.isoformat() if self.stats.last_update else None,
                "update_duration": f"{self.stats.update_duration:.2f}s",
                "processed_names": len(self.processed_names),
                "indexes_size": {
                    "nome": len(self.nome_index),
                    "valor": len(self.valor_index),
                    "banco": len(self.banco_index)
                }
            }
    
    def cleanup_old_data(self):
        """Limpeza de dados antigos para manter performance"""
        cutoff_time = datetime.now() - timedelta(hours=24)
        
        with self.lock:
            # Remove emails antigos
            old_hashes = [
                hash_id for hash_id, email_data in self.emails.items()
                if email_data.timestamp < cutoff_time
            ]
            
            for hash_id in old_hashes:
                del self.emails[hash_id]
            
            # Remove nomes processados antigos
            old_names = [
                nome for nome, timestamp in self.processed_names.items()
                if datetime.now() - timestamp > self.name_cache_duration * 2
            ]
            
            for nome in old_names:
                del self.processed_names[nome]
            
            # Reconstrói índices se removeu dados
            if old_hashes or old_names:
                self._build_indexes()
                logger.info(f"User {self.user_id}: Limpeza - removidos {len(old_hashes)} emails e {len(old_names)} nomes")

class IMAPCacheManager:
    """Gerenciador global de caches IMAP otimizados"""
    
    def __init__(self):
        self.caches: dict[int, OptimizedIMAPCache] = {}
        self.update_tasks: dict[int, asyncio.Task] = {}
        self.cleanup_task: Optional[asyncio.Task] = None
        
    def get_cache(self, user_id: int, config: dict) -> OptimizedIMAPCache:
        """Obtém ou cria cache para usuário"""
        if user_id not in self.caches:
            self.caches[user_id] = OptimizedIMAPCache(user_id, config)
            # Inicia tarefa de atualização automática
            self.start_auto_update(user_id)
        
        return self.caches[user_id]
    
    def start_auto_update(self, user_id: int):
        """Inicia atualização automática do cache"""
        async def auto_update():
            cache = self.caches.get(user_id)
            if not cache:
                return
            
            # Atualização completa inicial
            await cache.update_cache_full()
            
            # Loop de atualizações incrementais
            while user_id in self.caches:
                try:
                    # Atualização incremental a cada 10 segundos
                    await asyncio.sleep(10)
                    await cache.update_cache_incremental()
                    
                    # Atualização completa a cada 30 minutos
                    if datetime.now() - cache.last_full_update > timedelta(minutes=30):
                        await cache.update_cache_full()
                    
                    # Limpeza a cada 5 minutos
                    if datetime.now().minute % 5 == 0:
                        cache.cleanup_old_data()
                        
                except Exception as exc:
                    logger.error(f"Erro na atualização automática para user {user_id}: {exc}")
                    await asyncio.sleep(30)  # Espera mais tempo em caso de erro
        
        if user_id not in self.update_tasks:
            self.update_tasks[user_id] = asyncio.create_task(auto_update())
    
    def stop_cache(self, user_id: int):
        """Para e remove cache do usuário"""
        if user_id in self.update_tasks:
            self.update_tasks[user_id].cancel()
            del self.update_tasks[user_id]
        
        if user_id in self.caches:
            # Fecha conexões do pool
            cache = self.caches[user_id]
            for mb in cache.connection_pool:
                try:
                    mb.logout()
                except Exception:
                    pass
            del self.caches[user_id]
    
    def get_global_stats(self) -> dict:
        """Estatísticas globais de todos os caches"""
        total_emails = sum(cache.stats.total_emails for cache in self.caches.values())
        total_hits = sum(cache.stats.cache_hits for cache in self.caches.values())
        total_misses = sum(cache.stats.cache_misses for cache in self.caches.values())
        
        global_hit_rate = 0
        if total_hits + total_misses > 0:
            global_hit_rate = total_hits / (total_hits + total_misses)
        
        return {
            "active_caches": len(self.caches),
            "total_emails": total_emails,
            "global_hit_rate": f"{global_hit_rate:.2%}",
            "total_requests": total_hits + total_misses,
            "caches": {
                user_id: cache.get_stats() 
                for user_id, cache in self.caches.items()
            }
        }

# Instância global do gerenciador
imap_manager = IMAPCacheManager()