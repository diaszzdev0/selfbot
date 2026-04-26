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
    with open(path, "a", encoding="utf-8", errors="replace") as f:
        f.write(f"[{ts}] {text}\n")
        f.flush()


def _normalizar(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii").lower().strip()


def _extrair_nome(conteudo: str):
    c = conteudo.strip()
    cl = c.lower()
    prefixos = ["pg ", "pago ", "paguei "]
    sufixos = [" pg", " pago", " paguei"]
    for p in prefixos:
        if cl.startswith(p):
            return c[len(p):].strip()
    for s in sufixos:
        if cl.endswith(s):
            return c[:-len(s)].strip()
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
        with engine.begin() as con:
            con.execute(text("CREATE TABLE IF NOT EXISTS threads_enviadas (user_id INTEGER, thread_id BIGINT, PRIMARY KEY(user_id, thread_id))"))
            rows = con.execute(text("SELECT thread_id FROM threads_enviadas WHERE user_id=:uid"), {"uid": user_id}).fetchall()
        return {row[0] for row in rows}
    except Exception:
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
    log_msg(user_id, f"🔍 Busca otimizada: {nome}")
    
    # Rate limiting por usuário
    now = datetime.now()
    rate_limiter = _rate_limiters.setdefault(user_id, {"last_request": now, "count": 0})
    
    # Permite até 10 requests por segundo por usuário
    if (now - rate_limiter["last_request"]).total_seconds() < 0.1:
        rate_limiter["count"] += 1
        if rate_limiter["count"] > 10:
            log_msg(user_id, "⚠️ Rate limit atingido, aguardando...")
            return None
    else:
        rate_limiter["count"] = 0
        rate_limiter["last_request"] = now
    
    try:
        # Obtém cache otimizado
        cache = imap_manager.get_cache(user_id, cfg)
        
        # Busca no cache otimizado
        resultado = cache.search_payment_optimized(nome)
        
        if resultado:
            log_msg(user_id, f"✅ Pagamento encontrado (cache): {nome} - {resultado['banco']} - R$ {resultado['valor']}")
            return {
                "valor": resultado["valor"],
                "banco": resultado["banco"]
            }
        else:
            log_msg(user_id, f"❌ Pagamento não encontrado: {nome}")
            return None
            
    except Exception as e:
        log_msg(user_id, f"❌ Erro na busca otimizada: {e}")
        return None


async def _criar_sala_api(salaid: str = "") -> dict:
    if not salaid:
        salaid = SALA_PADRAO
    url = API_CRIAR_URL.format(salaid=salaid)
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(url) as resp:
                texto = await resp.text()
                try:
                    data = json.loads(texto)
                except Exception as e:
                    return {"_erro": f"JSON invalido: {e} | resposta: {texto[:200]}"}
            tentativas = 0
            while data.get("status") == 2 and tentativas < 12:
                await asyncio.sleep(5)
                tentativas += 1
                pedido_id = data.get("pedidoid", "")
                ts = int(asyncio.get_event_loop().time() * 1000)
                url_info = API_INFO_URL.format(pedidoid=pedido_id, ts=ts)
                async with sess.get(url_info) as resp2:
                    texto2 = await resp2.text()
                    try:
                        data = json.loads(texto2)
                    except Exception as e:
                        return {"_erro": f"JSON info invalido: {e} | resposta: {texto2[:200]}"}
        return data
    except Exception as e:
        return {"_erro": str(e)}


def parar_selfbot(user_id: int):
    _stop_flags[user_id] = True
    
    # Para o cache otimizado
    imap_manager.stop_cache(user_id)
    
    client = _clientes.get(user_id)
    loop = _loops.get(user_id)
    print(f"[parar_selfbot] user={user_id} client={client} loop={loop}")
    if client and loop and not client.is_closed():
        try:
            future = asyncio.run_coroutine_threadsafe(client.close(), loop)
            future.result(timeout=5)
            print(f"[parar_selfbot] client fechado com sucesso")
        except Exception as e:
            print(f"[parar_selfbot] erro ao fechar: {e}")
    
    # Limpa dados do usuário
    _clientes.pop(user_id, None)
    _loops.pop(user_id, None)
    _stop_flags.pop(user_id, None)
    _pagamentos_usados.pop(user_id, None)
    _rate_limiters.pop(user_id, None)


def run_selfbot(config: dict, user_id: int):
    log_msg(user_id, "🚀 Iniciando selfbot...")
    
    TOKEN = config.get("discord_token", "").strip()
    if not TOKEN:
        log_msg(user_id, "Token vazio.")
        return
    
    log_msg(user_id, "🔑 Token configurado")

    SERVER_ID = int(config["server_id"])
    CATEGORIA_ID = int(config["categoria_id"])
    log_msg(user_id, f"🏠 Servidor: {SERVER_ID}, Categoria: {CATEGORIA_ID}")
    
    MENSAGEM_ENTRADA = config.get("mensagem_entrada", "Ola! Use pg Nome Sobrenome para verificar seu pagamento.")
    IMAGEM_ENTRADA = config.get("imagem_entrada", "").strip()

    # Configurações para reduzir desconexões
    log_msg(user_id, "⚙️ Configurando cliente Discord...")
    
    try:
        # Configuração compatível com versões antigas do discord.py
        client = discord.Client(
            chunk_guilds_at_startup=False,
            heartbeat_timeout=60.0,
            max_messages=1000
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

    _monitor_iniciado = False

    @client.event
    async def on_disconnect():
        log_msg(user_id, "⚠️ Conexão perdida com Discord")
    
    @client.event
    async def on_resumed():
        log_msg(user_id, "✅ Conexão restaurada com Discord")
    
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
            asyncio.ensure_future(monitorar_threads())
            asyncio.ensure_future(atualizar_cache_imap())
            asyncio.ensure_future(health_check())  # Inicia health check

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
        """Monitora a saúde da conexão"""
        last_heartbeat = datetime.now()
        
        while not _stop_flags.get(user_id, False):
            try:
                await asyncio.sleep(30)  # Verifica a cada 30s
                
                if client.is_closed():
                    log_msg(user_id, "⚠️ Cliente desconectado detectado")
                    break
                
                # Verifica se o heartbeat está funcionando
                if client.latency > 5.0:  # Latencia muito alta
                    log_msg(user_id, f"⚠️ Latência alta: {client.latency:.2f}s")
                
                # Atualiza timestamp do health check
                last_heartbeat = datetime.now()
                
            except Exception as e:
                log_msg(user_id, f"⚠️ Erro no health check: {e}")
                break
    
    async def monitorar_threads():
        em_envio: set[int] = set()
        while True:
            try:
                guild = client.get_guild(SERVER_ID)
                if guild:
                    cat = guild.get_channel(CATEGORIA_ID)
                    if cat:
                        vistas = set()
                        for canal in cat.channels:
                            for thread in getattr(canal, "threads", []):
                                if thread.id in vistas:
                                    continue
                                vistas.add(thread.id)
                                if thread.id not in threads_com_mensagem and thread.id not in em_envio:
                                    em_envio.add(thread.id)
                                    threads_com_mensagem.add(thread.id)
                                    _salvar_thread(user_id, thread.id)
                                    log_msg(user_id, f"🧵 Nova thread: '{thread.name}'")
                                    async def _enviar(t=thread):
                                        await asyncio.sleep(2)
                                        await _enviar_mensagem_entrada(t)
                                        em_envio.discard(t.id)
                                    asyncio.ensure_future(_enviar())
            except Exception as e:
                log_msg(user_id, f"Erro monitorar: {e}")
            await asyncio.sleep(5)

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
        
        # Função async para reconexão
        async def conectar_com_retry():
            log_msg(user_id, "🔗 Iniciando conexão com Discord...")
            
            # Verificação básica do token
            if len(TOKEN) < 50:
                log_msg(user_id, "❌ Token muito curto - provavelmente inválido")
                return
            
            if not TOKEN.startswith(("MTA", "MTB", "MTC", "MTD", "MTE", "MTF", "MTG", "MTH", "MTI", "MTJ", "MTk", "MTl", "MTm", "MTn", "MTo", "MTp", "MTq", "MTr", "MTs", "MTt", "MTu", "MTv", "MTw", "MTx", "MTy", "MTz", "MjA", "MjB", "MjC", "MjD", "MjE", "MjF", "MjG", "MjH", "MjI", "MjJ", "Mjk", "Mjl", "Mjm", "Mjn", "Mjo", "Mjp", "Mjq", "Mjr", "Mjs", "Mjt", "Mju", "Mjv", "Mjw", "Mjx", "Mjy", "Mjz")):
                log_msg(user_id, "⚠️ Token não parece ser válido (formato incorreto)")
            
            try:
                log_msg(user_id, "🔗 Tentativa 1: Conectando ao Discord...")
                # Timeout de 30 segundos para conexão
                await asyncio.wait_for(client.start(TOKEN), timeout=30.0)
                    
            except asyncio.TimeoutError:
                log_msg(user_id, "⏰ Timeout na conexão (30s) - Token pode estar inválido")
            except discord.LoginFailure as e:
                log_msg(user_id, f"❌ Token inválido ou expirado: {e}")
            except discord.PrivilegedIntentsRequired as e:
                log_msg(user_id, f"❌ Intents privilegiadas não habilitadas: {e}")
            except discord.HTTPException as e:
                log_msg(user_id, f"❌ Erro HTTP Discord: {e}")
            except Exception as e:
                log_msg(user_id, f"⚠️ Erro de conexão: {type(e).__name__}: {e}")
        
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
