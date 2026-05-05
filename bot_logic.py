import asyncio
import re
import os
import traceback
import aiohttp
import random
import unicodedata
from datetime import datetime
import discord
from imap_optimizer import imap_manager
MAX_THREADS_CACHE = 10000
MAX_PAGAMENTOS_CACHE = 1000
MAX_RATE_LIMITERS = 100

_clientes: dict[int, discord.Client] = {}
_loops: dict[int, asyncio.AbstractEventLoop] = {}
_stop_flags: dict[int, bool] = {}
_pagamentos_usados: dict[int, dict] = {}  # user_id -> {nome_norm: timestamp}
_rate_limiters: dict[int, dict] = {}  # user_id -> {"last_request": timestamp, "count": int}

API_KEY = "266vq0badxid7jpcf96t"
API_CRIAR_URL = f"https://salasff.com/criar?key={API_KEY}&salaid={{salaid}}"
API_INFO_URL = "https://salasff.com/info?pedidoid={pedidoid}"
API_INICIAR_URL = f"https://salasff.com/iniciar?key={API_KEY}&pedidoid={{pedidoid}}"
API_MODOS_URL = f"https://salasff.com/modos?key={API_KEY}"

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
    safe_text = re.sub(r'\b[A-Za-z0-9._-]{59,}\b', '[TOKEN_REDACTED]', safe_text)
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
    c = conteudo.strip()
    cl = c.lower()

    prefixos = [
        "pg ", "pago ", "paguei ", "pagou ", "pag ",
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

    nome = None

    for p in prefixos:
        if cl.startswith(p):
            nome = c[len(p):].strip()
            nome = re.sub(r'^[:\-.,;!?\s]+', '', nome)
            break

    if not nome:
        for s in sufixos:
            if cl.endswith(s):
                nome = c[:-len(s)].strip()
                nome = re.sub(r'[:\-.,;!?\s]+$', '', nome)
                break

    if not nome:
        return None

    # Validações: nome precisa ter pelo menos 2 palavras com 2+ letras cada
    palavras = [p for p in nome.split() if len(p) >= 2 and re.match(r'^[a-zA-ZÀ-ÿ]+$', p)]
    if len(palavras) < 2:
        return None

    return ' '.join(palavras)


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
        return (row[0], row[1]) if row else (0, 0)
    except Exception:
        return (0, 0)


def _incrementar_sala(user_id: int):
    try:
        from sqlalchemy import text
        engine = _get_db_engine()
        with engine.begin() as con:
            con.execute(text(
                "INSERT INTO bot_status (user_id, ativo, salas_usadas, limite_salas) "
                "VALUES (:uid, 0, 0, 0) ON CONFLICT (user_id) DO NOTHING"
            ), {"uid": user_id})
            con.execute(text(
                "UPDATE bot_status SET salas_usadas = salas_usadas + 1 WHERE user_id = :uid"
            ), {"uid": user_id})
        log_msg(user_id, "\U0001f3ae +1 sala contabilizada")
    except Exception as e:
        log_msg(user_id, f"\u26a0\ufe0f Erro ao incrementar sala: {type(e).__name__}: {str(e)[:80]}")


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
    if not nome or len(nome.strip()) < 2:
        return None
    from imap_optimizer import buscar_pagamento_imap
    def log_fn(msg):
        log_msg(user_id, msg)
    log_msg(user_id, f"🔍 Buscando: '{nome}'")
    resultado = buscar_pagamento_imap(cfg, nome, log_fn)
    if resultado:
        log_msg(user_id, f"✅ Encontrado: {resultado['pagador']} | R${resultado['valor']} | {resultado['banco']}")
    else:
        log_msg(user_id, f"❌ Não encontrado: '{nome}'")
    return resultado


async def _criar_sala_api(salaid: str = "") -> dict:
    if not salaid:
        salaid = SALA_PADRAO
    url = API_CRIAR_URL.format(salaid=salaid)
    try:
        timeout = aiohttp.ClientTimeout(total=60, connect=10)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(url) as resp:
                if resp.status != 200:
                    return {"_erro": f"HTTP {resp.status}"}
                data = await resp.json(content_type=None)

            if not data.get("success"):
                return {"_erro": data.get("msg", "Erro desconhecido")}

            # status 3 = sala criada imediatamente
            if data.get("status") == 3 and data.get("sala"):
                return data

            # status 2 = criando, faz polling
            pedidoid = data.get("pedidoid")
            if not pedidoid:
                return {"_erro": "pedidoid ausente"}

            for _ in range(12):  # até ~60s
                await asyncio.sleep(5)
                url_info = API_INFO_URL.format(pedidoid=pedidoid)
                async with sess.get(url_info) as r:
                    if r.status != 200:
                        continue
                    data = await r.json(content_type=None)
                if data.get("status") == 3 and data.get("sala"):
                    return data

            return {"_erro": "Timeout: sala não criada em 60s"}

    except asyncio.TimeoutError:
        return {"_erro": "Timeout na conexão com API"}
    except Exception as exc:
        return {"_erro": f"{type(exc).__name__}: {str(exc)[:100]}"}


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
        client = discord.Client(
            chunk_guilds_at_startup=False,
            heartbeat_timeout=60.0,
            max_messages=100,
        )
    except Exception as e:
        log_msg(user_id, f"❌ Erro ao criar cliente: {e}")
        return

    _clientes[user_id] = client

    threads_com_mensagem: set[int] = _carregar_threads(user_id)
    threads_em_processamento: set[int] = set()  # guard contra race condition
    log_msg(user_id, f"🧵 {len(threads_com_mensagem)} thread(s) carregada(s).")
    pagamentos_por_thread: dict[int, int] = {}
    salas_ativas: dict[int, str] = {}       # channel_id -> pedidoid
    go_por_thread: dict[int, set] = {}       # channel_id -> set de user_ids
    go_auto_tasks: dict[int, asyncio.Task] = {}  # channel_id -> task do timer
    pg_em_processamento: set[str] = set()
    
    log_msg(user_id, "📝 Definindo eventos do Discord...")

    async def _enviar_mensagem_entrada(canal):
        import io
        import aiohttp as _aiohttp
        if IMAGEM_ENTRADA:
            log_msg(user_id, "Tentando enviar imagem...")
            try:
                async with _aiohttp.ClientSession() as sess:
                    async with sess.get(IMAGEM_ENTRADA, timeout=_aiohttp.ClientTimeout(total=15)) as resp:
                        log_msg(user_id, f"Imagem status HTTP: {resp.status}")
                        if resp.status == 200:
                            dados = await resp.read()
                            log_msg(user_id, f"Imagem baixada: {len(dados)} bytes")
                            arquivo = discord.File(io.BytesIO(dados), filename="imagem.png")
                            await canal.send(MENSAGEM_ENTRADA, file=arquivo)
                            log_msg(user_id, "Imagem enviada com sucesso")
                            return
            except Exception as exc:
                log_msg(user_id, f"Erro ao enviar imagem: {type(exc).__name__}: {exc}")
        await canal.send(MENSAGEM_ENTRADA)

    async def _dar_go(channel, pedidoid: str):
        # Cancela o timer automático se ainda estiver rodando
        task = go_auto_tasks.pop(channel.id, None)
        if task and not task.done():
            task.cancel()
        try:
            url = API_INICIAR_URL.format(pedidoid=pedidoid)
            async with aiohttp.ClientSession() as sess:
                async with sess.get(url) as resp:
                    data = await resp.json(content_type=None)
            if data.get("success"):
                await channel.send("✅ Sala iniciada!")
                log_msg(user_id, f"🎮 Go dado! pedidoid: {pedidoid}")
            else:
                log_msg(user_id, f"Erro go: {data.get('msg', data)}")
        except Exception as exc:
            log_msg(user_id, f"Erro go: {exc}")
        finally:
            salas_ativas.pop(channel.id, None)
            go_por_thread.pop(channel.id, None)
            go_auto_tasks.pop(channel.id, None)

    async def _enviar_sala(channel, salaid: str = ""):
        # Prioridade: config.modo_sala_id → nome thread → default
        if salaid:
            modo_config = salaid
        elif "modo_sala_id" in config and config["modo_sala_id"]:
            modo_config = config["modo_sala_id"]
            log_msg(user_id, f"🎮 Modo config: {modo_config}")
        else:
            nome_canal = getattr(channel, 'name', '') or ''
            if '-inf' in nome_canal.lower() or 'infinito' in nome_canal.lower():
                modo_config = SALA_INF
                log_msg(user_id, f"🎮 Modo: Infinito ({nome_canal})")
            else:
                modo_config = SALA_GN  # SALA_PADRAO == SALA_GN
                log_msg(user_id, f"🎮 Modo: Gel Normal ({nome_canal})")

        data = await _criar_sala_api(modo_config)
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
            await channel.send("⚡ **IMPORTANTE:** Após ambos entrarem, digitem `go` aqui no chat para iniciar! A sala dá go automático em **5 minutos**.")

            async def go_auto(ch=channel, pid=pedidoid):
                await asyncio.sleep(300)
                if salas_ativas.get(ch.id) == pid:
                    log_msg(user_id, "🎮 Go automático (5 min)")
                    await ch.send("⏰ Tempo esgotado! Iniciando sala automaticamente...")
                    await _dar_go(ch, pid)

            task = asyncio.ensure_future(go_auto())
            go_auto_tasks[channel.id] = task
            return True
        log_msg(user_id, f"🎮 Erro criar sala: {data}")
        await channel.send("Nao foi possivel criar a sala.")
        return False

    async def _enviar_em_thread(thread: discord.Thread):
        """Ponto único de envio. Garante que nunca envia duas vezes na mesma thread."""
        if thread.id in threads_com_mensagem or thread.id in threads_em_processamento:
            return
        threads_em_processamento.add(thread.id)
        threads_com_mensagem.add(thread.id)
        _salvar_thread(user_id, thread.id)
        log_msg(user_id, f"🧵 Thread detectada: '{thread.name}' (ID: {thread.id})")
        try:
            await asyncio.sleep(9)
            await _enviar_mensagem_entrada(thread)
            log_msg(user_id, f"✅ Mensagem enviada: {thread.name}")
        except discord.NotFound:
            log_msg(user_id, f"⚠️ Thread {thread.name} deletada")
        except discord.Forbidden:
            log_msg(user_id, f"⚠️ Sem permissão em {thread.name}")
        except Exception as exc:
            log_msg(user_id, f"❌ Erro ao enviar em {thread.name}: {exc}")
        finally:
            threads_em_processamento.discard(thread.id)

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
            asyncio.ensure_future(verificar_threads_iniciais())
            asyncio.ensure_future(monitorar_threads())
            asyncio.ensure_future(health_check())

    async def health_check():
        while not _stop_flags.get(user_id, False):
            try:
                await asyncio.sleep(300)
                if _stop_flags.get(user_id, False):
                    break
                latency_str = f"{client.latency:.3f}s" if hasattr(client, 'latency') and client.latency != float('inf') else "inf"
                log_msg(user_id, f"📊 Status: conectado={not client.is_closed()}, latência={latency_str}")
            except Exception as e:
                log_msg(user_id, f"❌ Erro no health check: {type(e).__name__}")
                await asyncio.sleep(60)

    async def verificar_threads_iniciais():
        log_msg(user_id, "🔍 Verificando threads existentes...")
        try:
            guild = client.get_guild(SERVER_ID)
            if not guild:
                return
            cat = guild.get_channel(CATEGORIA_ID)
            if not cat:
                return
            for canal in cat.channels:
                for thread in getattr(canal, "threads", []):
                    if thread.id not in threads_com_mensagem:
                        asyncio.ensure_future(_enviar_em_thread(thread))
            log_msg(user_id, "✅ Verificação inicial concluída")
        except Exception as exc:
            log_msg(user_id, f"❌ Erro na verificação inicial: {exc}")

    async def monitorar_threads():
        ultima_verificacao = datetime.now()
        while not _stop_flags.get(user_id, False):
            try:
                guild = client.get_guild(SERVER_ID)
                if guild:
                    cat = guild.get_channel(CATEGORIA_ID)
                    if cat:
                        for canal in cat.channels:
                            for thread in getattr(canal, "threads", []):
                                if thread.id not in threads_com_mensagem:
                                    asyncio.ensure_future(_enviar_em_thread(thread))
                agora = datetime.now()
                if (agora - ultima_verificacao).total_seconds() > 300:
                    log_msg(user_id, f"📊 Monitoramento: {len(threads_com_mensagem)} threads processadas")
                    ultima_verificacao = agora
            except Exception as exc:
                log_msg(user_id, f"❌ Erro no monitoramento: {exc}")
            await asyncio.sleep(5)

    @client.event
    async def on_thread_create(thread: discord.Thread):
        if not thread.guild or thread.guild.id != SERVER_ID:
            return
        parent = thread.parent
        if not parent or getattr(parent, "category_id", None) != CATEGORIA_ID:
            return
        asyncio.ensure_future(_enviar_em_thread(thread))

    @client.event
    async def on_thread_join(thread: discord.Thread):
        if not thread.guild or thread.guild.id != SERVER_ID:
            return
        parent = thread.parent
        if not parent or getattr(parent, "category_id", None) != CATEGORIA_ID:
            return
        asyncio.ensure_future(_enviar_em_thread(thread))
    
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

        if message.author == client.user:
            if cmd in ("!gn", "!gi", "!normal", "!infinito"):
                salaid = SALA_INF if cmd in ("!gi", "!infinito") else SALA_GN
                log_msg(user_id, f"Comando {cmd} detectado")
                msg_req = await channel.send("Criando sala...")
                await _enviar_sala(channel, salaid)
                await msg_req.delete()
            elif cmd.startswith("!very "):
                nome_very = conteudo[6:].strip()
                palavras = [p for p in nome_very.split() if len(p) >= 2 and re.match(r'^[a-zA-Z\u00C0-\u00FF]+$', p)]
                if len(palavras) < 2:
                    await message.reply("⚠️ Use: `!very Nome Sobrenome`")
                    return
                nome_very = ' '.join(palavras)
                log_msg(user_id, f"🔍 !very: {nome_very}")
                msg_very = await channel.send(f"⏳ Verificando pagamento de **{nome_very}**...")
                try:
                    resultado = await asyncio.wait_for(
                        asyncio.get_running_loop().run_in_executor(None, lambda: _buscar_pagamento_otimizado(config, nome_very, user_id)),
                        timeout=120
                    )
                except asyncio.TimeoutError:
                    resultado = None
                try:
                    await msg_very.delete()
                except Exception:
                    pass
                if resultado:
                    await channel.send(
                        f"✅ **Pagamento confirmado** ({resultado['banco']}) para {nome_very}!\n"
                        f"Valor: {resultado['valor']} (BRL)\n"
                        f"ID: {random.randint(100, 999)}"
                    )
                    log_msg(user_id, f"✅ !very confirmado: {nome_very}")
                else:
                    await channel.send(f"❌ Pagamento não encontrado para **{nome_very}**.")
                    log_msg(user_id, f"❌ !very não encontrado: {nome_very}")
            return

        if re.fullmatch(r"go+", cmd) and channel.id in salas_ativas:
            if message.author != client.user:
                go_por_thread.setdefault(channel.id, set()).add(message.author.id)
                count = len(go_por_thread[channel.id])
                log_msg(user_id, f"🎮 Go de {message.author} ({count}/2)")
                if count >= 2:
                    log_msg(user_id, "🎮 Dois go - iniciando sala...")
                    await message.add_reaction("✅")
                    await message.reply("⚡ **Ambos deram go!** Iniciando a partida...")
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
                asyncio.get_running_loop().run_in_executor(None, lambda: _buscar_pagamento_otimizado(config, nome_busca, user_id)),
                timeout=30
            )
        except asyncio.TimeoutError:
            log_msg(user_id, "Timeout na busca")
            resultado = None

        try:
            await msg_fila.delete()
        except Exception:
            pass
        pg_em_processamento.discard(chave_pg)

        if resultado:
            await message.reply(
                f"✅ **PAGAMENTO CONFIRMADO PELO SISTEMA**\n\n"
                f"👤 Nome: {resultado.get('pagador', nome_busca)}\n"
                f"💰 Valor: R$ {resultado['valor']}\n"
                f"🏦 Origem: {resultado['banco']}\n"
                f"🆔 ID: {random.randint(1000, 9999)}"
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
                await _enviar_sala(channel)  # detecta modo automaticamente pelo nome da thread
                await msg_req.delete()
        else:
            await message.reply(f"Pagamento nao confirmado para {nome_busca}.")

    @client.event
    async def on_error(event, *args, **kwargs):
        # Ignora erro conhecido do discord.py-self no THREAD_MEMBERS_UPDATE
        if event == 'THREAD_MEMBERS_UPDATE':
            return
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
            if len(TOKEN) < 50:
                log_msg(user_id, "Token muito curto - provavelmente inválido")
                return
            try:
                await client.start(TOKEN)
            except discord.LoginFailure as e:
                log_msg(user_id, f"Token inválido ou expirado: {e}")
            except discord.HTTPException as e:
                log_msg(user_id, f"Erro HTTP Discord: {e}")
            except Exception as e:
                log_msg(user_id, f"Erro na conexão: {type(e).__name__}: {str(e)[:100]}")
        
        log_msg(user_id, "🚀 Executando loop principal...")
        try:
            loop.run_until_complete(conectar_com_retry())
        except KeyboardInterrupt:
            log_msg(user_id, "🔴 Interrompido pelo usuário")
        except Exception as e:
            log_msg(user_id, f"❌ Erro no loop principal: {e}")
                    
    except discord.LoginFailure:
        log_msg(user_id, "Token invalido ou expirado.")
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
