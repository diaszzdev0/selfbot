import asyncio
import re
import os
import traceback
import aiohttp
import json
import random
import unicodedata
from datetime import datetime, date, timedelta
import discord
from imap_tools import MailBox, AND
from imap_optimizer import imap_manager

# Limites para prevenir vazamento de memória
MAX_THREADS_CACHE = 10000
MAX_PAGAMENTOS_CACHE = 1000
MAX_RATE_LIMITERS = 100

_clientes: dict[int, discord.Client] = {}
_loops: dict[int, asyncio.AbstractEventLoop] = {}
_stop_flags: dict[int, bool] = {}
_pagamentos_usados: dict[int, dict] = {}  # user_id -> {nome_norm: timestamp}
_rate_limiters: dict[int, dict] = {}  # user_id -> {"last_request": timestamp, "count": int}

API_KEY = "266vq0badxid7jpcf96t"
API_MODOS_URL = f"https://salasff.com/modos?key={API_KEY}"
API_CRIAR_URL = f"https://salasff.com/criar?key={API_KEY}&salaid={{salaid}}"
API_INICIAR_URL = f"https://salasff.com/iniciar?key={API_KEY}&pedidoid={{pedidoid}}"
API_INFO_URL = f"https://salasff.com/info?pedidoid={{pedidoid}}&timenow={{ts}}"

SALA_GN = "826526295161871655"
SALA_INF = "654411323287636213"
SALA_PADRAO = "826526295161871655"

LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOG_DIR, exist_ok=True)


def log_msg(user_id: int, text: str):
    ts = datetime.now().strftime("%H:%M:%S")
    path = os.path.join(LOG_DIR, f"user_{user_id}.log")
    
    # Remove informações sensíveis dos logs
    safe_text = text
    # Remove tokens (qualquer string com mais de 50 chars alfanuméricos)
    import re
    safe_text = re.sub(r'\b[A-Za-z0-9._-]{50,}\b', '[TOKEN_REDACTED]', safe_text)
    # Remove emails
    safe_text = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[EMAIL_REDACTED]', safe_text)
    # Remove senhas (após "senha", "password", "pass")
    safe_text = re.sub(r'(senha|password|pass)\s*[:=]\s*\S+', r'\1: [REDACTED]', safe_text, flags=re.IGNORECASE)
    
    with open(path, "a", encoding="utf-8", errors="replace") as f:
        f.write(f"[{ts}] {safe_text}\n")
        f.flush()


def _normalizar(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii").lower().strip()


def _extrair_nome(conteudo: str):
    """Extrai nome de comandos de pagamento com múltiplas variações"""
    c = conteudo.strip()
    cl = c.lower()
    
    # Padrões mais flexíveis para detectar comandos de pagamento
    prefixos = [
        "pg ", "pago ", "paguei ", "pagou ", "pag ", "p ",
        "pg: ", "pago: ", "paguei: ", "pagou: ",
        "pg- ", "pago- ", "paguei- ", "pagou- ",
        "pg.", "pago.", "paguei.", "pagou.",
        "verificar ", "check ", "buscar ", "consultar "
    ]
    
    sufixos = [
        " pg", " pago", " paguei", " pagou", " pag",
        " :pg", " :pago", " :paguei", " :pagou",
        " -pg", " -pago", " -paguei", " -pagou"
    ]
    
    # Verifica prefixos
    for p in prefixos:
        if cl.startswith(p):
            nome = c[len(p):].strip()
            # Remove caracteres especiais do início
            nome = re.sub(r'^[:\-.,;!?\s]+', '', nome)
            if nome and len(nome) >= 2:
                return nome
    
    # Verifica sufixos
    for s in sufixos:
        if cl.endswith(s):
            nome = c[:-len(s)].strip()
            # Remove caracteres especiais do final
            nome = re.sub(r'[:\-.,;!?\s]+$', '', nome)
            if nome and len(nome) >= 2:
                return nome
    
    # Verifica padrões no meio da mensagem (ex: "verificar pagamento de João Silva")
    patterns = [
        r'(?:verificar|check|buscar|consultar)\s+(?:pagamento\s+(?:de|do|da)\s+)?([a-záàâãéêíóôõúç\s]{2,})',
        r'(?:pg|pago|paguei|pagou)\s*[:\-.,;]?\s*([a-záàâãéêíóôõúç\s]{2,})',
        r'([a-záàâãéêíóôõúç\s]{2,})\s+(?:pg|pago|paguei|pagou)\s*$'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, cl, re.IGNORECASE)
        if match:
            nome = match.group(1).strip()
            # Remove palavras comuns que não são nomes
            palavras_ignorar = ['pagamento', 'de', 'do', 'da', 'para', 'por', 'em', 'no', 'na', 'o', 'a']
            palavras = nome.split()
            palavras_filtradas = [p for p in palavras if p.lower() not in palavras_ignorar and len(p) > 1]
            
            if len(palavras_filtradas) >= 1:
                nome_final = ' '.join(palavras_filtradas)
                if len(nome_final) >= 2:
                    return nome_final
    
    return None


_db_engine = None

def _get_db_engine():
    global _db_engine
    if _db_engine is not None:
        return _db_engine
    from sqlalchemy import create_engine
    url = os.getenv("DATABASE_URL", "")
    if not url:
        _db_path = os.path.join(os.path.dirname(__file__), "selfbot.db")
        url = f"sqlite:///{_db_path}"
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    _db_engine = create_engine(url, pool_pre_ping=True)
    return _db_engine


def _get_salas_info(user_id: int):
    try:
        from sqlalchemy import text
        engine = _get_db_engine()
        with engine.connect() as con:
            row = con.execute(text("SELECT salas_usadas, limite_salas FROM bot_status WHERE user_id=:uid"), {"uid": user_id}).fetchone()
        return (row[0], row[1]) if row else (0, 10)
    except Exception:
        return (0, 10)


def _incrementar_sala(user_id: int):
    try:
        from sqlalchemy import text
        engine = _get_db_engine()
        with engine.begin() as con:
            con.execute(text("UPDATE bot_status SET salas_usadas = salas_usadas + 1 WHERE user_id=:uid"), {"uid": user_id})
    except Exception:
        pass


def _carregar_threads(user_id: int) -> set:
    try:
        from sqlalchemy import text
        engine = _get_db_engine()
        with engine.connect() as con:
            con.execute(text("CREATE TABLE IF NOT EXISTS threads_enviadas (user_id INTEGER, thread_id BIGINT, PRIMARY KEY(user_id, thread_id))"))
            # Limita a quantidade de threads carregadas para evitar uso excessivo de memória
            rows = con.execute(
                text("SELECT thread_id FROM threads_enviadas WHERE user_id=:uid ORDER BY thread_id DESC LIMIT :limit"), 
                {"uid": user_id, "limit": MAX_THREADS_CACHE}
            ).fetchall()
        return {row[0] for row in rows}
    except Exception as e:
        log_msg(user_id, f"Erro ao carregar threads: {type(e).__name__}: {str(e)[:100]}")
        return set()


def _salvar_thread(user_id: int, thread_id: int):
    try:
        from sqlalchemy import text
        engine = _get_db_engine()
        with engine.begin() as con:
            con.execute(text("CREATE TABLE IF NOT EXISTS threads_enviadas (user_id INTEGER, thread_id BIGINT, PRIMARY KEY(user_id, thread_id))"))
            con.execute(text("INSERT INTO threads_enviadas (user_id, thread_id) VALUES (:uid, :tid) ON CONFLICT DO NOTHING"), {"uid": user_id, "tid": thread_id})
    except Exception:
        pass


def _buscar_pagamento_otimizado(cfg: dict, nome: str, user_id: int):
    """Busca otimizada de pagamento usando o sistema de cache avançado"""
    if not nome or not isinstance(nome, str):
        log_msg(user_id, "Nome inválido para busca")
        return None
        
    nome = nome.strip()
    if len(nome) < 2:
        log_msg(user_id, "Nome muito curto para busca")
        return None
    
    log_msg(user_id, f"Busca otimizada: {nome[:50]}...")  # Trunca nome longo
    
    # Rate limiting por usuário
    now = datetime.now()
    rate_limiter = _rate_limiters.setdefault(user_id, {"last_request": now, "count": 0})
    
    # Permite até 5 requests por segundo por usuário (reduzido)
    if (now - rate_limiter["last_request"]).total_seconds() < 0.2:
        rate_limiter["count"] += 1
        if rate_limiter["count"] > 5:
            log_msg(user_id, "Rate limit atingido, aguardando...")
            return None
    else:
        rate_limiter["count"] = 0
        rate_limiter["last_request"] = now
    
    try:
        # Obtém cache otimizado com timeout
        cache = imap_manager.get_cache(user_id, cfg)
        if not cache:
            log_msg(user_id, "Cache não disponível")
            return None
        
        # Busca no cache otimizado
        resultado = cache.search_payment_optimized(nome)
        
        if resultado:
            # Valida resultado
            if not isinstance(resultado, dict) or 'banco' not in resultado or 'valor' not in resultado:
                log_msg(user_id, "Resultado de busca inválido")
                return None
                
            log_msg(user_id, f"Pagamento encontrado (cache): {nome[:30]} - {resultado['banco'][:20]}")
            return {
                "valor": str(resultado["valor"])[:20],  # Limita tamanho
                "banco": str(resultado["banco"])[:50]   # Limita tamanho
            }
        else:
            log_msg(user_id, f"Pagamento não encontrado: {nome[:30]}")
            return None
            
    except Exception as e:
        log_msg(user_id, f"Erro na busca otimizada: {type(e).__name__}: {str(e)[:100]}")
        return None


async def _criar_sala_api(salaid: str = "") -> dict:
    """Cria sala via API com validação e timeout adequado"""
    if not salaid:
        salaid = SALA_PADRAO
    
    # Validação de entrada
    if not isinstance(salaid, str) or len(salaid) < 5:
        return {"_erro": "ID de sala inválido"}
    
    url = API_CRIAR_URL.format(salaid=salaid)
    
    try:
        # Timeout mais conservador
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(url) as resp:
                if resp.status != 200:
                    return {"_erro": f"HTTP {resp.status}: {resp.reason}"}
                    
                texto = await resp.text()
                if not texto:
                    return {"_erro": "Resposta vazia da API"}
                    
                try:
                    data = json.loads(texto)
                except json.JSONDecodeError as e:
                    return {"_erro": f"JSON inválido: {str(e)[:100]} | resposta: {texto[:200]}"}
            
            # Aguarda processamento com limite de tentativas
            tentativas = 0
            max_tentativas = 10  # Reduzido de 12
            
            while data.get("status") == 2 and tentativas < max_tentativas:
                await asyncio.sleep(3)  # Reduzido de 5s
                tentativas += 1
                
                pedido_id = data.get("pedidoid")
                if not pedido_id:
                    return {"_erro": "ID do pedido não encontrado"}
                
                ts = int(asyncio.get_event_loop().time() * 1000)
                url_info = API_INFO_URL.format(pedidoid=pedido_id, ts=ts)
                
                try:
                    async with sess.get(url_info) as resp2:
                        if resp2.status != 200:
                            return {"_erro": f"Erro ao verificar status: HTTP {resp2.status}"}
                            
                        texto2 = await resp2.text()
                        if not texto2:
                            return {"_erro": "Resposta vazia ao verificar status"}
                            
                        try:
                            data = json.loads(texto2)
                        except json.JSONDecodeError as e:
                            return {"_erro": f"JSON inválido no status: {str(e)[:100]}"}
                except Exception as e:
                    return {"_erro": f"Erro na verificação: {type(e).__name__}: {str(e)[:100]}"}
            
            if tentativas >= max_tentativas:
                return {"_erro": f"Timeout após {max_tentativas} tentativas"}
                
        return data
        
    except asyncio.TimeoutError:
        return {"_erro": "Timeout na conexão com API"}
    except aiohttp.ClientError as e:
        return {"_erro": f"Erro de conexão: {type(e).__name__}: {str(e)[:100]}"}
    except Exception as e:
        return {"_erro": f"Erro inesperado: {type(e).__name__}: {str(e)[:100]}"}


def parar_selfbot(user_id: int):
    """Para o selfbot e limpa recursos adequadamente"""
    log_msg(user_id, "🛑 Iniciando parada do selfbot...")
    
    _stop_flags[user_id] = True
    
    # Para o cache otimizado
    try:
        imap_manager.stop_cache(user_id)
        log_msg(user_id, "✅ Cache IMAP parado")
    except Exception as e:
        log_msg(user_id, f"⚠️ Erro ao parar cache: {type(e).__name__}")
    
    client = _clientes.get(user_id)
    loop = _loops.get(user_id)
    
    if client and loop and not client.is_closed():
        try:
            # Timeout mais longo para garantir fechamento adequado
            future = asyncio.run_coroutine_threadsafe(client.close(), loop)
            future.result(timeout=10)
            log_msg(user_id, "✅ Cliente Discord fechado")
        except asyncio.TimeoutError:
            log_msg(user_id, "⚠️ Timeout ao fechar cliente Discord")
        except Exception as e:
            log_msg(user_id, f"⚠️ Erro ao fechar cliente: {type(e).__name__}")
    
    # Limpa dados do usuário com verificação de limites
    _clientes.pop(user_id, None)
    _loops.pop(user_id, None)
    _stop_flags.pop(user_id, None)
    
    # Limpa caches com limite de memória
    if user_id in _pagamentos_usados:
        pagamentos = _pagamentos_usados[user_id]
        if len(pagamentos) > MAX_PAGAMENTOS_CACHE:
            # Mantém apenas os mais recentes
            sorted_items = sorted(pagamentos.items(), key=lambda x: x[1], reverse=True)
            _pagamentos_usados[user_id] = dict(sorted_items[:MAX_PAGAMENTOS_CACHE])
        else:
            _pagamentos_usados.pop(user_id, None)
    
    _rate_limiters.pop(user_id, None)
    
    # Limpa caches globais se ficaram muito grandes
    if len(_rate_limiters) > MAX_RATE_LIMITERS:
        # Remove entradas mais antigas
        users_to_remove = list(_rate_limiters.keys())[MAX_RATE_LIMITERS:]
        for uid in users_to_remove:
            _rate_limiters.pop(uid, None)
    
    log_msg(user_id, "✅ Selfbot parado e recursos limpos")


def run_selfbot(config: dict, user_id: int):
    log_msg(user_id, "🚀 Iniciando selfbot...")
    
    TOKEN = config.get("discord_token", "").strip()
    if not TOKEN:
        log_msg(user_id, "Token vazio.")
        return
    
    # Valida o token antes de tentar conectar
    from token_validator import validate_discord_token, check_token_expiry
    
    validation = validate_discord_token(TOKEN)
    if not validation["valid"]:
        log_msg(user_id, f"❌ Token inválido: {validation['error']}")
        return
    
    expiry_check = check_token_expiry(TOKEN)
    log_msg(user_id, f"🔑 Token válido (User ID: {validation['user_id']}, Tipo: {expiry_check['type']})")

    SERVER_ID = int(config["server_id"])
    CATEGORIA_ID = int(config["categoria_id"])
    log_msg(user_id, f"🏠 Servidor: {SERVER_ID}, Categoria: {CATEGORIA_ID}")
    
    MENSAGEM_ENTRADA = config.get("mensagem_entrada", "Ola! Use pg Nome Sobrenome para verificar seu pagamento.")
    IMAGEM_ENTRADA = config.get("imagem_entrada", "").strip()

    # Configurações para reduzir desconexões
    log_msg(user_id, "⚙️ Configurando cliente Discord...")
    
    try:
        # Configuração otimizada para estabilidade
        client = discord.Client(
            chunk_guilds_at_startup=False,
            heartbeat_timeout=120.0,  # Aumentado para 2 minutos
            max_messages=500,  # Reduzido para economizar memória
            guild_ready_timeout=30.0  # Timeout para guilds
        )
        log_msg(user_id, "✅ Cliente Discord criado")
        
    except Exception as e:
        log_msg(user_id, f"❌ Erro ao criar cliente: {e}")
        # Fallback com configurações mínimas
        try:
            log_msg(user_id, "🔄 Tentando configuração mínima...")
            client = discord.Client()
            log_msg(user_id, "✅ Cliente Discord criado (modo básico)")
        except Exception as e2:
            log_msg(user_id, f"❌ Erro crítico ao criar cliente: {e2}")
            return

    _clientes[user_id] = client

    threads_com_mensagem: set[int] = _carregar_threads(user_id)
    log_msg(user_id, f"🧵 {len(threads_com_mensagem)} thread(s) carregada(s).")
    pagamentos_por_thread: dict[int, int] = {}
    salas_ativas: dict[int, str] = {}
    go_por_thread: dict[int, set] = {}
    pg_em_processamento: set[str] = set()
    
    log_msg(user_id, "📝 Definindo eventos do Discord...")

    async def _enviar_mensagem_entrada(canal):
        if IMAGEM_ENTRADA:
            try:
                async with aiohttp.ClientSession() as sess:
                    async with sess.get(IMAGEM_ENTRADA) as resp:
                        dados = await resp.read()
                        ext = resp.headers.get("Content-Type", "image/png").split("/")[-1].split(";")[0] or "png"
                import io
                arquivo = discord.File(io.BytesIO(dados), filename=f"imagem.{ext}")
                await canal.send(MENSAGEM_ENTRADA, file=arquivo)
            except Exception as e:
                log_msg(user_id, f"Erro imagem: {e}")
                await canal.send(MENSAGEM_ENTRADA)
        else:
            await canal.send(MENSAGEM_ENTRADA)

    async def _dar_go(channel, pedidoid: str):
        try:
            async with aiohttp.ClientSession() as sess:
                url = API_INICIAR_URL.format(pedidoid=pedidoid)
                async with sess.get(url) as resp:
                    texto = await resp.text()
                    try:
                        data = json.loads(texto)
                    except Exception:
                        data = {}
            if data.get("success"):
                await channel.send("Iniciando sala, a sala foi iniciada!")
                log_msg(user_id, f"🎮 Go dado! pedidoid: {pedidoid}")
            else:
                log_msg(user_id, f"Erro go: {data}")
        except Exception as e:
            log_msg(user_id, f"Erro go: {e}")
        finally:
            salas_ativas.pop(channel.id, None)
            go_por_thread.pop(channel.id, None)

    async def _enviar_sala(channel, salaid: str = ""):
        data = await _criar_sala_api(salaid or SALA_PADRAO)
        if "_erro" in data:
            log_msg(user_id, f"🎮 Erro API sala: {data['_erro']}")
            await channel.send("Nao foi possivel criar a sala. Erro na API.")
            return False
        if data.get("success") and data.get("sala"):
            sala = data["sala"]
            prefixo = config.get("prefixo_sala", "").strip()
            msg_sala = f"{prefixo} {sala['id']} {sala['senha']}" if prefixo else f"{sala['id']} {sala['senha']}"
            _incrementar_sala(user_id)
            pedidoid = data.get("pedidoid", "")
            salas_ativas[channel.id] = pedidoid
            go_por_thread[channel.id] = set()
            log_msg(user_id, f"🎮 Sala enviada: {msg_sala}")
            await channel.send(msg_sala)
            await channel.send("⚡ **IMPORTANTE:** Após ambos entrarem, digitem `go` aqui no chat para iniciar!")

            async def go_auto(ch=channel, pid=pedidoid):
                await asyncio.sleep(300)
                if salas_ativas.get(ch.id) == pid:
                    log_msg(user_id, f"🎮 Go automatico...")
                    await _dar_go(ch, pid)
            asyncio.ensure_future(go_auto())
            return True
        else:
            log_msg(user_id, f"🎮 Erro criar sala: {data}")
            await channel.send("Nao foi possivel criar a sala.")
            return False

    async def _tentar_restaurar_heartbeat(client, user_id):
        """Tenta restaurar o heartbeat sem reconectar completamente"""
        log_msg(user_id, "🔧 Tentando restaurar heartbeat...")
        
        tentativas_ping = [
            # Tentativa 1: Mudança de presença simples
            lambda: client.change_presence(),
            # Tentativa 2: Mudança de status
            lambda: client.change_presence(status=discord.Status.online),
            # Tentativa 3: Atividade temporária
            lambda: client.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="conexao")),
            # Tentativa 4: Reset para padrão
            lambda: client.change_presence(status=discord.Status.online, activity=None)
        ]
        
        for i, ping_func in enumerate(tentativas_ping, 1):
            try:
                log_msg(user_id, f"🏓 Ping #{i}/4...")
                await asyncio.wait_for(ping_func(), timeout=10)
                await asyncio.sleep(3)
                
                # Verifica se melhorou
                if hasattr(client, 'latency') and client.latency != float('inf'):
                    log_msg(user_id, f"✅ Heartbeat restaurado com ping #{i} - latência: {client.latency:.3f}s")
                    return True
                    
            except asyncio.TimeoutError:
                log_msg(user_id, f"⏰ Timeout no ping #{i}")
            except Exception as e:
                log_msg(user_id, f"❌ Erro no ping #{i}: {type(e).__name__}")
        
        log_msg(user_id, "❌ Todas as tentativas de ping falharam")
        return False

    _monitor_iniciado = False

    @client.event
    async def on_disconnect():
        # Só loga se não foi parada intencional
        if not _stop_flags.get(user_id, False):
            log_msg(user_id, "Conexão perdida com Discord")
            log_msg(user_id, f"Estado: closed={client.is_closed()}, latency={getattr(client, 'latency', 'N/A')}")
    
    @client.event
    async def on_resumed():
        log_msg(user_id, "Conexão restaurada com Discord")
        if hasattr(client, 'latency') and client.latency != float('inf'):
            log_msg(user_id, f"Latência atual: {client.latency:.3f}s")
    
    @client.event
    async def on_ready():
        nonlocal _monitor_iniciado
        log_msg(user_id, f"✅ Sessao: {client.user} (ID: {client.user.id})")
        guild = client.get_guild(SERVER_ID)
        if guild:
            cat = guild.get_channel(CATEGORIA_ID)
            log_msg(user_id, f"🌐 Servidor: {guild.name}")
            log_msg(user_id, f"📂 Categoria: {cat.name if cat else 'NAO ENCONTRADA'}")
        else:
            log_msg(user_id, f"❌ Servidor {SERVER_ID} nao encontrado.")
        if not _monitor_iniciado:
            _monitor_iniciado = True
            await asyncio.sleep(3)
            asyncio.ensure_future(verificar_threads_iniciais())  # Nova função
            asyncio.ensure_future(monitorar_threads())
            asyncio.ensure_future(atualizar_cache_imap())
            asyncio.ensure_future(health_check())  # Inicia health check

    async def verificar_threads_iniciais():
        """Verifica threads existentes na inicialização para não perder nenhuma"""
        log_msg(user_id, "🔍 Verificando threads existentes na inicialização...")
        
        try:
            guild = client.get_guild(SERVER_ID)
            if not guild:
                log_msg(user_id, "⚠️ Servidor não encontrado na verificação inicial")
                return
                
            cat = guild.get_channel(CATEGORIA_ID)
            if not cat:
                log_msg(user_id, "⚠️ Categoria não encontrada na verificação inicial")
                return
            
            threads_processadas = 0
            threads_novas = 0
            
            for canal in cat.channels:
                try:
                    # Threads ativas
                    for thread in getattr(canal, "threads", []):
                        threads_processadas += 1
                        if thread.id not in threads_com_mensagem:
                            threads_novas += 1
                            threads_com_mensagem.add(thread.id)
                            _salvar_thread(user_id, thread.id)
                            
                            log_msg(user_id, f"🔄 Thread existente processada: '{thread.name}'")
                            
                            # Envia mensagem com delay para não sobrecarregar
                            async def _enviar_inicial(t=thread, delay=threads_novas):
                                try:
                                    await asyncio.sleep(delay * 2)  # Delay escalonado
                                    await _enviar_mensagem_entrada(t)
                                    log_msg(user_id, f"✅ Mensagem inicial enviada para: {t.name}")
                                except Exception as e:
                                    log_msg(user_id, f"❌ Erro ao enviar mensagem inicial: {e}")
                            
                            asyncio.ensure_future(_enviar_inicial())
                    
                    # Threads arquivadas recentes (apenas últimas 6 horas)
                    try:
                        async for thread in canal.archived_threads(limit=20):
                            if thread.created_at and (datetime.now(thread.created_at.tzinfo) - thread.created_at).total_seconds() < 21600:  # 6 horas
                                threads_processadas += 1
                                if thread.id not in threads_com_mensagem:
                                    threads_novas += 1
                                    threads_com_mensagem.add(thread.id)
                                    _salvar_thread(user_id, thread.id)
                                    log_msg(user_id, f"📁 Thread arquivada processada: '{thread.name}'")
                    except Exception as e:
                        log_msg(user_id, f"⚠️ Erro ao verificar threads arquivadas em {canal.name}: {e}")
                        
                except Exception as e:
                    log_msg(user_id, f"⚠️ Erro ao processar canal {canal.name}: {e}")
            
            log_msg(user_id, f"✅ Verificação inicial concluída: {threads_processadas} threads encontradas, {threads_novas} novas")
            
        except Exception as e:
            log_msg(user_id, f"❌ Erro na verificação inicial de threads: {e}")
    
    async def atualizar_cache_imap():
        """Task removida - agora usa sistema otimizado global"""
        log_msg(user_id, "🚀 Sistema de cache otimizado ativado!")
        
        # Inicializa o cache otimizado
        cache = imap_manager.get_cache(user_id, config)
        
        # Aguarda inicialização do cache
        await asyncio.sleep(2)
        
        # Log das estatísticas iniciais
        stats = cache.get_stats()
        log_msg(user_id, f"📊 Cache stats: {stats['total_emails']} emails, hit rate: {stats['hit_rate']}")

    async def health_check():
        """Monitora a saúde da conexão com reconexão automática melhorada"""
        reconnect_attempts = 0
        max_attempts = 5  # Aumentado para 5 tentativas
        last_ping = datetime.now()
        consecutive_failures = 0
        
        while not _stop_flags.get(user_id, False):
            try:
                await asyncio.sleep(20)  # Verifica a cada 20s (mais frequente)
                current_time = datetime.now()
                
                # Verifica se o cliente está realmente conectado
                is_connected = not client.is_closed()
                has_good_latency = hasattr(client, 'latency') and client.latency != float('inf') and client.latency < 30.0
                
                # Tratamento específico para latência infinita
                if hasattr(client, 'latency') and client.latency == float('inf'):
                    consecutive_failures += 1
                    log_msg(user_id, f"⚠️ Latência infinita detectada (falha #{consecutive_failures})")
                    
                    # Se latência infinita por mais de 1 minuto, tenta restaurar heartbeat
                    if (current_time - last_ping).total_seconds() > 60:
                        log_msg(user_id, "🔄 Tentando restaurar heartbeat sem reconectar...")
                        
                        heartbeat_restored = await _tentar_restaurar_heartbeat(client, user_id)
                        
                        if heartbeat_restored:
                            consecutive_failures = 0
                            last_ping = current_time
                            needs_reconnect = False
                        else:
                            # Se não conseguiu restaurar e já faz mais de 3 minutos
                            if (current_time - last_ping).total_seconds() > 180:
                                log_msg(user_id, "❌ Heartbeat não restaurado há 3+ minutos - forçando reconexão")
                                needs_reconnect = True
                            else:
                                needs_reconnect = False
                    else:
                        # Latência infinita recente, aguarda mais um pouco
                        needs_reconnect = False
                else:
                    # Latência normal ou cliente desconectado
                    needs_reconnect = False
                
                # Testa conectividade real tentando acessar o servidor
                connection_test_passed = False
                if is_connected and not needs_reconnect:
                    try:
                        guild = client.get_guild(SERVER_ID)
                        if guild and guild.name:  # Verifica se consegue acessar dados do servidor
                            connection_test_passed = True
                            if consecutive_failures > 0:
                                consecutive_failures = 0
                                log_msg(user_id, "✅ Conectividade com servidor confirmada")
                    except Exception as e:
                        log_msg(user_id, f"⚠️ Erro ao testar servidor: {type(e).__name__}")
                        connection_test_passed = False
                
                # Determina se precisa reconectar
                if not needs_reconnect:
                    needs_reconnect = (
                        not is_connected or 
                        not has_good_latency or 
                        not connection_test_passed or
                        (current_time - last_ping).total_seconds() > 600  # 10 minutos sem ping válido
                    )
                
                if needs_reconnect:
                    consecutive_failures += 1
                    log_msg(user_id, f"Problema de conexão detectado (falha #{consecutive_failures})")
                    log_msg(user_id, f"Estado: closed={client.is_closed()}, latency={getattr(client, 'latency', 'N/A')}, test_passed={connection_test_passed}")
                    
                    if reconnect_attempts < max_attempts:
                        reconnect_attempts += 1
                        log_msg(user_id, f"Tentativa de reconexão {reconnect_attempts}/{max_attempts}")
                        
                        try:
                            # Para o cliente atual se ainda estiver rodando
                            if not client.is_closed():
                                log_msg(user_id, "Fechando conexão atual...")
                                await asyncio.wait_for(client.close(), timeout=10)
                                await asyncio.sleep(3)
                            
                            # Não tenta reconectar se foi parado intencionalmente
                            if _stop_flags.get(user_id, False):
                                break
                            
                            # Aguarda antes de tentar reconectar
                            wait_time = min(10 * reconnect_attempts, 60)  # Máximo 60s
                            log_msg(user_id, f"Aguardando {wait_time}s antes de reconectar...")
                            await asyncio.sleep(wait_time)
                            
                            # Tenta reconectar
                            log_msg(user_id, "Iniciando reconexão...")
                            await asyncio.wait_for(client.connect(reconnect=True), timeout=30)
                            
                            # Verifica se a reconexão foi bem-sucedida
                            await asyncio.sleep(5)
                            if not client.is_closed():
                                reconnect_attempts = 0
                                consecutive_failures = 0
                                last_ping = current_time
                                log_msg(user_id, "✅ Reconexão bem-sucedida")
                            else:
                                log_msg(user_id, "❌ Reconexão falhou - cliente ainda fechado")
                                
                        except asyncio.TimeoutError:
                            log_msg(user_id, f"⏰ Timeout na reconexão (tentativa {reconnect_attempts})")
                        except Exception as e:
                            log_msg(user_id, f"❌ Erro na reconexão: {type(e).__name__}: {str(e)[:50]}")
                    else:
                        log_msg(user_id, f"❌ Máximo de tentativas de reconexão atingido ({max_attempts})")
                        log_msg(user_id, "🔄 Reiniciando cliente completamente...")
                        
                        # Última tentativa: reinicia completamente
                        try:
                            if not client.is_closed():
                                await client.close()
                            await asyncio.sleep(10)
                            
                            # Cria novo cliente se necessário
                            if _stop_flags.get(user_id, False):
                                break
                                
                            await client.start(TOKEN)
                            reconnect_attempts = 0
                            consecutive_failures = 0
                            log_msg(user_id, "✅ Cliente reiniciado com sucesso")
                        except Exception as e:
                            log_msg(user_id, f"❌ Falha crítica no reinício: {type(e).__name__}")
                            break
                else:
                    # Conexão está boa
                    if reconnect_attempts > 0:
                        log_msg(user_id, "✅ Conexão estabilizada")
                        reconnect_attempts = 0
                    last_ping = current_time
                
                # Log periódico de status (a cada 5 minutos)
                if current_time.minute % 5 == 0 and current_time.second < 20:
                    latency_str = f"{client.latency:.3f}s" if hasattr(client, 'latency') and client.latency != float('inf') else "N/A"
                    log_msg(user_id, f"📊 Status: conectado={not client.is_closed()}, latência={latency_str}, falhas={consecutive_failures}")
                
            except Exception as e:
                log_msg(user_id, f"❌ Erro no health check: {type(e).__name__}: {str(e)[:50]}")
                await asyncio.sleep(30)  # Aguarda mais tempo em caso de erro
    
    async def monitorar_threads():
        em_envio: set[int] = set()
        ultima_verificacao = datetime.now()
        
        while not _stop_flags.get(user_id, False):
            try:
                guild = client.get_guild(SERVER_ID)
                if not guild:
                    log_msg(user_id, "⚠️ Servidor não encontrado no monitoramento")
                    await asyncio.sleep(10)
                    continue
                    
                cat = guild.get_channel(CATEGORIA_ID)
                if not cat:
                    log_msg(user_id, "⚠️ Categoria não encontrada no monitoramento")
                    await asyncio.sleep(10)
                    continue
                
                threads_encontradas = set()
                novas_threads = []
                
                # Verifica threads ativas em todos os canais da categoria
                for canal in cat.channels:
                    try:
                        # Threads ativas (visíveis)
                        for thread in getattr(canal, "threads", []):
                            threads_encontradas.add(thread.id)
                            if thread.id not in threads_com_mensagem and thread.id not in em_envio:
                                novas_threads.append(thread)
                        
                        # Busca threads arquivadas recentes (últimas 24h)
                        try:
                            async for thread in canal.archived_threads(limit=50):
                                threads_encontradas.add(thread.id)
                                # Só processa threads criadas nas últimas 2 horas
                                if thread.created_at and (datetime.now(thread.created_at.tzinfo) - thread.created_at).total_seconds() < 7200:
                                    if thread.id not in threads_com_mensagem and thread.id not in em_envio:
                                        novas_threads.append(thread)
                        except Exception as e:
                            log_msg(user_id, f"⚠️ Erro ao buscar threads arquivadas em {canal.name}: {e}")
                            
                    except Exception as e:
                        log_msg(user_id, f"⚠️ Erro ao processar canal {canal.name}: {e}")
                
                # Processa novas threads encontradas
                for thread in novas_threads:
                    if thread.id not in em_envio:  # Dupla verificação
                        em_envio.add(thread.id)
                        threads_com_mensagem.add(thread.id)
                        _salvar_thread(user_id, thread.id)
                        
                        # Log detalhado da nova thread
                        thread_age = "N/A"
                        if thread.created_at:
                            age_seconds = (datetime.now(thread.created_at.tzinfo) - thread.created_at).total_seconds()
                            thread_age = f"{int(age_seconds/60)}min atrás"
                        
                        log_msg(user_id, f"🧵 Nova thread detectada: '{thread.name}' (ID: {thread.id}, Criada: {thread_age})")
                        
                        # Envia mensagem com delay aleatório para parecer mais natural
                        async def _enviar_com_delay(t=thread):
                            try:
                                delay = random.randint(2, 8)  # Delay entre 2-8 segundos
                                await asyncio.sleep(delay)
                                
                                # Verifica se a thread ainda existe antes de enviar
                                try:
                                    await t.fetch()  # Tenta buscar a thread para verificar se ainda existe
                                    await _enviar_mensagem_entrada(t)
                                    log_msg(user_id, f"✅ Mensagem enviada para thread: {t.name}")
                                except discord.NotFound:
                                    log_msg(user_id, f"⚠️ Thread {t.name} foi deletada antes do envio")
                                except discord.Forbidden:
                                    log_msg(user_id, f"⚠️ Sem permissão para enviar em {t.name}")
                                except Exception as e:
                                    log_msg(user_id, f"❌ Erro ao enviar mensagem para {t.name}: {e}")
                            except Exception as e:
                                log_msg(user_id, f"❌ Erro no envio com delay: {e}")
                            finally:
                                em_envio.discard(t.id)
                        
                        asyncio.ensure_future(_enviar_com_delay())
                
                # Log periódico de estatísticas (a cada 5 minutos)
                agora = datetime.now()
                if (agora - ultima_verificacao).total_seconds() > 300:  # 5 minutos
                    log_msg(user_id, f"📊 Monitoramento: {len(threads_encontradas)} threads ativas, {len(threads_com_mensagem)} processadas")
                    ultima_verificacao = agora
                    
            except Exception as e:
                log_msg(user_id, f"❌ Erro crítico no monitoramento: {e}")
                log_msg(user_id, traceback.format_exc())
            
            # Intervalo de verificação mais frequente
            await asyncio.sleep(3)  # Reduzido de 5s para 3s

    # Proteção contra processamento duplo de threads
    _thread_processing_lock = asyncio.Lock()
    
    @client.event
    async def on_thread_create(thread: discord.Thread):
        """Evento disparado quando uma nova thread é criada"""
        async with _thread_processing_lock:
            try:
                # Verifica se a thread está no servidor e categoria corretos
                if not thread.guild or thread.guild.id != SERVER_ID:
                    return
                
                parent = thread.parent
                if not parent or getattr(parent, "category_id", None) != CATEGORIA_ID:
                    return
                
                # Verifica se já foi processada (dupla verificação)
                if thread.id in threads_com_mensagem:
                    log_msg(user_id, f"⚠️ Thread {thread.id} já processada, ignorando")
                    return
                
                # Adiciona à lista de threads processadas
                threads_com_mensagem.add(thread.id)
                _salvar_thread(user_id, thread.id)
                
                log_msg(user_id, f"🎆 Thread criada em tempo real: '{thread.name}' (ID: {thread.id})")
                
                # Envia mensagem após um pequeno delay
                async def _enviar_imediato():
                    try:
                        await asyncio.sleep(random.randint(1, 3))  # Delay de 1-3 segundos
                        await _enviar_mensagem_entrada(thread)
                        log_msg(user_id, f"✅ Mensagem enviada imediatamente para: {thread.name}")
                    except discord.NotFound:
                        log_msg(user_id, f"⚠️ Thread {thread.name} foi deletada antes do envio")
                    except discord.Forbidden:
                        log_msg(user_id, f"⚠️ Sem permissão para enviar em {thread.name}")
                    except Exception as e:
                        log_msg(user_id, f"❌ Erro ao enviar mensagem imediata: {type(e).__name__}: {str(e)[:100]}")
                
                asyncio.ensure_future(_enviar_imediato())
                
            except Exception as e:
                log_msg(user_id, f"❌ Erro no evento on_thread_create: {type(e).__name__}: {str(e)[:100]}")
    
    @client.event
    async def on_thread_join(thread: discord.Thread):
        """Evento disparado quando o bot entra em uma thread"""
        async with _thread_processing_lock:
            try:
                # Verifica se a thread está no servidor e categoria corretos
                if not thread.guild or thread.guild.id != SERVER_ID:
                    return
                
                parent = thread.parent
                if not parent or getattr(parent, "category_id", None) != CATEGORIA_ID:
                    return
                
                # Verifica se já foi processada (dupla verificação)
                if thread.id in threads_com_mensagem:
                    log_msg(user_id, f"⚠️ Thread {thread.id} já processada no join, ignorando")
                    return
                
                # Adiciona à lista de threads processadas
                threads_com_mensagem.add(thread.id)
                _salvar_thread(user_id, thread.id)
                
                log_msg(user_id, f"🔗 Bot entrou na thread: '{thread.name}' (ID: {thread.id})")
                
                # Envia mensagem após um pequeno delay
                async def _enviar_join():
                    try:
                        await asyncio.sleep(random.randint(1, 4))  # Delay de 1-4 segundos
                        await _enviar_mensagem_entrada(thread)
                        log_msg(user_id, f"✅ Mensagem enviada após join: {thread.name}")
                    except discord.NotFound:
                        log_msg(user_id, f"⚠️ Thread {thread.name} foi deletada antes do envio")
                    except discord.Forbidden:
                        log_msg(user_id, f"⚠️ Sem permissão para enviar em {thread.name}")
                    except Exception as e:
                        log_msg(user_id, f"❌ Erro ao enviar mensagem após join: {type(e).__name__}: {str(e)[:100]}")
                
                asyncio.ensure_future(_enviar_join())
                
            except Exception as e:
                log_msg(user_id, f"❌ Erro no evento on_thread_join: {type(e).__name__}: {str(e)[:100]}")
    
    @client.event
    async def on_message(message: discord.Message):
        if not message.guild or message.guild.id != SERVER_ID:
            return
        channel = message.channel
        parent = getattr(channel, "parent", None)
        if parent is None or getattr(parent, "category_id", None) != CATEGORIA_ID:
            return

        conteudo = message.content.strip()
        cmd = conteudo.lower()

        if cmd in ("!gn", "!inf") and message.author == client.user:
            salaid = SALA_GN if cmd == "!gn" else SALA_INF
            log_msg(user_id, f"Comando {cmd} detectado")
            msg_req = await channel.send("Criando sala manualmente...")
            await _enviar_sala(channel, salaid)
            await msg_req.delete()
            return

        if re.fullmatch(r"go+", cmd) and channel.id in salas_ativas:
            if message.author != client.user:
                go_por_thread.setdefault(channel.id, set()).add(message.author.id)
                log_msg(user_id, f"🎮 Go de {message.author} ({len(go_por_thread[channel.id])}/2)")
                if len(go_por_thread[channel.id]) >= 2:
                    log_msg(user_id, "🎮 Dois go - iniciando sala...")
                    await message.reply("⚡ **Sala deu go!** Tentando iniciar a partida.")
                    await _dar_go(channel, salas_ativas[channel.id])
            return

        if message.author == client.user:
            return

        nome_busca = _extrair_nome(conteudo)
        if not nome_busca:
            # Log para debug quando não consegue extrair nome
            if any(palavra in conteudo.lower() for palavra in ['pg', 'pago', 'paguei', 'pagou', 'verificar', 'check']):
                log_msg(user_id, f"⚠️ Comando detectado mas nome não extraído: '{conteudo[:50]}...'")
            return

        chave_pg = f"{channel.id}_{nome_busca.lower()}"
        if chave_pg in pg_em_processamento:
            return
        pg_em_processamento.add(chave_pg)
        log_msg(user_id, f"💰 pg detectado: {nome_busca} | {message.author}")

        msg_fila = await message.reply("⏳ **Verificando Pagamento…** aguarde!")

        try:
            resultado = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, _buscar_pagamento_otimizado, config, nome_busca, user_id),
                timeout=5  # Timeout reduzido para 5s devido à otimização
            )
        except asyncio.TimeoutError:
            log_msg(user_id, "Timeout na busca otimizada.")
            resultado = None

        await msg_fila.delete()
        pg_em_processamento.discard(chave_pg)

        if resultado:
            # verifica se pagamento ja foi usado nos ultimos 2 minutos (otimizado)
            usados = _pagamentos_usados.setdefault(user_id, {})
            chave_pag = _normalizar(nome_busca)
            agora = datetime.now()
            if chave_pag in usados:
                segundos = (agora - usados[chave_pag]).total_seconds()
                if segundos < 120:  # 2 minutos
                    await message.reply("⚠️ Atenção esse pagamento já foi utilizado em outro tópico, faça um pagamento e tente novamente!")
                    log_msg(user_id, f"⚠️ Pagamento duplicado bloqueado: {nome_busca} ({int(segundos)}s atras)")
                    return
            # registra uso
            usados[chave_pag] = agora
            
            # Limpa pagamentos antigos (otimização de memória)
            cutoff = agora - timedelta(minutes=10)
            usados_limpos = {k: v for k, v in usados.items() if v > cutoff}
            _pagamentos_usados[user_id] = usados_limpos

            await message.reply(
                f"**Pagamento confirmado** ({resultado['banco']}) para {nome_busca}!\n"
                f"Valor: {resultado['valor']} (BRL)\n"
                f"ID: {random.randint(100, 999)}"
            )
            pagamentos_por_thread[channel.id] = pagamentos_por_thread.get(channel.id, 0) + 1
            log_msg(user_id, f"💰 Pagamentos: {pagamentos_por_thread[channel.id]}/2")

            if pagamentos_por_thread[channel.id] >= 2:
                pagamentos_por_thread[channel.id] = 0
                usadas, limite = _get_salas_info(user_id)
                if usadas >= limite:
                    await channel.send(f"Limite de salas atingido ({usadas}/{limite}).")
                    log_msg(user_id, f"⛔ Limite: {usadas}/{limite}")
                    return
                msg_req = await channel.send("Solicitando Sala...")
                await _enviar_sala(channel, SALA_PADRAO)
                await msg_req.delete()
        else:
            await message.reply(f"Pagamento nao confirmado para {nome_busca}.")

    @client.event
    async def on_error(event, *args, **kwargs):
        log_msg(user_id, f"Erro evento '{event}':")
        log_msg(user_id, traceback.format_exc())

    try:
        log_msg(user_id, "🔄 Criando loop asyncio...")
        loop = asyncio.new_event_loop()
        loop.set_exception_handler(lambda l, c: None)
        asyncio.set_event_loop(loop)
        _loops[user_id] = loop
        _stop_flags[user_id] = False
        log_msg(user_id, "✅ Loop asyncio criado")
        
        # Função async para reconexão com diagnóstico
        async def conectar_com_retry():
            log_msg(user_id, "Iniciando conexão com Discord...")
            
            # Verificação básica do token
            if len(TOKEN) < 50:
                log_msg(user_id, "Token muito curto - provavelmente inválido")
                return
            
            max_retries = 3
            for attempt in range(1, max_retries + 1):
                try:
                    log_msg(user_id, f"Tentativa {attempt}/{max_retries}: Conectando ao Discord...")
                    
                    # Timeout progressivo: 45s, 60s, 90s
                    timeout = 30 + (15 * attempt)
                    await asyncio.wait_for(client.start(TOKEN), timeout=timeout)
                    
                    # Se chegou aqui, conexão foi bem-sucedida
                    log_msg(user_id, "Conexão estabelecida com sucesso!")
                    return
                        
                except asyncio.TimeoutError:
                    log_msg(user_id, f"Timeout na tentativa {attempt} ({timeout}s)")
                    if attempt < max_retries:
                        wait_time = 10 * attempt
                        log_msg(user_id, f"Aguardando {wait_time}s antes da próxima tentativa...")
                        await asyncio.sleep(wait_time)
                except discord.LoginFailure as e:
                    log_msg(user_id, f"Token inválido ou expirado: {e}")
                    return  # Não tenta novamente se o token está inválido
                except discord.PrivilegedIntentsRequired as e:
                    log_msg(user_id, f"Intents privilegiadas não habilitadas: {e}")
                    return
                except discord.HTTPException as e:
                    log_msg(user_id, f"Erro HTTP Discord (tentativa {attempt}): {e}")
                    if attempt < max_retries:
                        await asyncio.sleep(15)  # Aguarda mais tempo para erros HTTP
                except Exception as e:
                    log_msg(user_id, f"Erro na tentativa {attempt}: {type(e).__name__}: {str(e)[:50]}")
                    if attempt < max_retries:
                        await asyncio.sleep(10)
            
            log_msg(user_id, f"Falha em todas as {max_retries} tentativas de conexão")
        
        log_msg(user_id, "🚀 Executando loop principal...")
        try:
            loop.run_until_complete(conectar_com_retry())
        except KeyboardInterrupt:
            log_msg(user_id, "🔴 Interrompido pelo usuário")
        except Exception as e:
            log_msg(user_id, f"❌ Erro no loop principal: {e}")
                    
    except discord.LoginFailure:
        log_msg(user_id, "Token invalido ou expirado.")
    except discord.PrivilegedIntentsRequired:
        log_msg(user_id, "Intents privilegiadas nao habilitadas.")
    except OSError as e:
        log_msg(user_id, f"Erro de rede: {e}")
    except Exception as e:
        log_msg(user_id, f"❌ Erro geral na inicialização: {e}")
        log_msg(user_id, traceback.format_exc())
    finally:
        try:
            loop.close()
        except Exception:
            pass
        _clientes.pop(user_id, None)
        _loops.pop(user_id, None)
        _stop_flags.pop(user_id, None)
        log_msg(user_id, "🔴 Selfbot encerrado.")
