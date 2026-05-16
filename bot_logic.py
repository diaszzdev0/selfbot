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
from imap_optimizer import _match_nomes
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
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    path = os.path.join(LOG_DIR, f"user_{user_id}.log")
    
    # Remove informações sensíveis dos logs
    safe_text = text
    safe_text = re.sub(r'\b[A-Za-z0-9._-]{59,}\b', '[TOKEN_REDACTED]', safe_text)
    safe_text = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[EMAIL_REDACTED]', safe_text)
    safe_text = re.sub(r'(senha|password|pass)\s*[:=]\s*\S+', r'\1: [REDACTED]', safe_text, flags=re.IGNORECASE)
    
    # Categoriza e formata a mensagem
    categoria = "INFO"
    if "❌" in text or "Erro" in text or "erro" in text:
        categoria = "ERRO"
    elif "⚠️" in text or "Timeout" in text:
        categoria = "AVISO"
    elif "✅" in text or "confirmado" in text.lower() or "sucesso" in text.lower():
        categoria = "OK"
    elif "🔍" in text or "Buscando" in text:
        categoria = "BUSCA"
    elif "💰" in text or "Pagamento" in text:
        categoria = "PGTO"
    elif "🎮" in text or "Sala" in text:
        categoria = "SALA"
    elif "🧵" in text or "Thread" in text:
        categoria = "THREAD"
    elif "🚀" in text or "Iniciando" in text:
        categoria = "INIT"
    elif "🔒" in text or "🚨" in text:
        categoria = "SEGUR"
    
    # Formata linha com separador visual
    linha = f"[{ts}] [{categoria:6}] {safe_text}"
    
    with open(path, "a", encoding="utf-8", errors="replace") as f:
        f.write(f"{linha}\n")
        f.flush()


def _normalizar(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii").lower().strip()


# Compatibilidade: algumas rotinas chamam _normalizar_cat
# (manter _normalizar_cat como alias de _normalizar)
def _normalizar_cat(texto: str) -> str:
    return _normalizar(texto)



def _extrair_nome(conteudo: str):
    c = conteudo.strip()
    cl = c.lower()

    prefixos = [
        "pago ", "pago: ", "pago- ", "pago.",
        "pg ", "pg: ", "pg- ", "pg.",
        "paguei ", "paguei: ", "paguei- ",
        "pagou ", "pagou: ", "pagou- ",
        "pag ", "verificar ", "check ", "buscar ", "consultar "
    ]
    sufixos = [
        " pago", " pg", " paguei", " pagou", " pag",
        " :pago", " :pg", " :paguei", " :pagou",
        " -pago", " -pg", " -paguei", " -pagou"
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

    palavras = [p for p in nome.split() if len(p) >= 2 and re.match(r'^[a-zA-ZÀ-ÿ]+$', p)]
    if not palavras:
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

    # Alguns ambientes (SquareCloud) podem injetar valores "sujos"/quebrados em DATABASE_URL.
    # Se não parecer uma URL válida do SQLAlchemy, cai para SQLite local.
    def _normalize_database_url(val: str) -> str:
        if not val:
            return f"sqlite:///{os.path.join(os.path.dirname(__file__), 'selfbot.db')}"
        val = str(val).strip()
        val = val.strip('"\'')
        if val.startswith("postgres://"):
            val = val.replace("postgres://", "postgresql://", 1)
        return val

    url = _normalize_database_url(url)

    if not (url.startswith("sqlite:///")) and not (url.startswith("postgresql://")):
        url = f"sqlite:///{os.path.join(os.path.dirname(__file__), 'selfbot.db')}"

    _db_engine = create_engine(url, pool_pre_ping=True)
    return _db_engine


# Default limit is 10 if not set (allows new users to have some rooms without manual config)
_DEFAULT_LIMITE_SALAS = 10

def _get_salas_info(user_id: int):
    try:
        from sqlalchemy import text
        engine = _get_db_engine()
        with engine.connect() as con:
            row = con.execute(text("SELECT salas_usadas, limite_salas FROM bot_status WHERE user_id=:uid"), {"uid": user_id}).fetchone()
        # If limite_salas is 0, use default of 10
        limite = row[1] if row and row[1] > 0 else _DEFAULT_LIMITE_SALAS
        return (row[0], limite) if row else (0, _DEFAULT_LIMITE_SALAS)
    except Exception:
        return (0, _DEFAULT_LIMITE_SALAS)


def _incrementar_sala(user_id: int):
    try:
        from sqlalchemy import text
        engine = _get_db_engine()
        with engine.begin() as con:
            # Apenas incrementa salas_usadas — nunca altera limite_salas
            con.execute(text(
                "INSERT INTO bot_status (user_id, ativo, salas_usadas, limite_salas) "
                "VALUES (:uid, false, 1, :default_limite) "
                "ON CONFLICT (user_id) DO UPDATE SET salas_usadas = bot_status.salas_usadas + 1"
            ), {"uid": user_id, "default_limite": _DEFAULT_LIMITE_SALAS})
            con.execute(text(
                "CREATE TABLE IF NOT EXISTS sala_historico "
                "(id SERIAL PRIMARY KEY, user_id INTEGER, criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            ))
            con.execute(text(
                "INSERT INTO sala_historico (user_id) VALUES (:uid)"
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


def _gerar_hash_pagamento(nome: str, valor: str, banco: str, uid: str = None) -> str:
    import hashlib
    if uid:
        return hashlib.md5(f"uid_{uid}".encode()).hexdigest()
    nome_norm = _normalizar(nome)
    valor_norm = valor.replace(',', '.').replace('R$', '').strip()
    banco_norm = _normalizar(banco)
    hoje = __import__('datetime').date.today().strftime("%Y-%m-%d")
    chave = f"{nome_norm}_{valor_norm}_{banco_norm}_{hoje}"
    return hashlib.md5(chave.encode()).hexdigest()

_pix_recentes: dict[str, float] = {}  # hash_sem_uid -> timestamp

def _is_pix_duplicado_recente(nome: str, valor: str, banco: str, uid: str = None) -> bool:
    """Retorna True se o mesmo UID (ou mesmo nome+valor+banco sem UID) chegou nos últimos 60s."""
    import hashlib, time
    if uid:
        chave = f"uid_{uid}"
    else:
        nome_norm = _normalizar(nome)
        valor_norm = valor.replace(',', '.').replace('R$', '').strip()
        banco_norm = _normalizar(banco)
        chave = hashlib.md5(f"{nome_norm}_{valor_norm}_{banco_norm}".encode()).hexdigest()
    agora = time.time()
    expirados = [k for k, t in _pix_recentes.items() if agora - t > 60]
    for k in expirados:
        del _pix_recentes[k]
    if chave in _pix_recentes:
        return True
    _pix_recentes[chave] = agora
    return False

def _verificar_pagamento_usado(hash_pag: str, user_id: int) -> dict:
    """Verifica se pagamento já foi usado no banco de dados"""
    try:
        from sqlalchemy import text
        engine = _get_db_engine()
        with engine.connect() as con:
            con.execute(text(
                "CREATE TABLE IF NOT EXISTS pagamentos_usados ("
                "hash TEXT PRIMARY KEY, "
                "user_id INTEGER, "
                "thread_id BIGINT, "
                "discord_user_id BIGINT, "
                "nome TEXT, "
                "valor TEXT, "
                "usado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            ))
            row = con.execute(
                text("SELECT thread_id, discord_user_id, usado_em FROM pagamentos_usados WHERE hash=:hash"),
                {"hash": hash_pag}
            ).fetchone()
        if row:
            return {"usado": True, "thread_id": row[0], "discord_user_id": row[1], "timestamp": row[2]}
        return {"usado": False}
    except Exception as e:
        log_msg(user_id, f"⚠️ Erro ao verificar pagamento: {type(e).__name__}")
        return {"usado": False}

def _registrar_pagamento_usado(hash_pag: str, user_id: int, thread_id: int, discord_user_id: int, nome: str, valor: str, uid: str = None):
    """Registra pagamento como usado no banco de dados"""
    try:
        from sqlalchemy import text
        engine = _get_db_engine()
        with engine.begin() as con:
            con.execute(text(
                "INSERT INTO pagamentos_usados (hash, user_id, thread_id, discord_user_id, nome, valor) "
                "VALUES (:hash, :uid, :tid, :duid, :nome, :valor) ON CONFLICT DO NOTHING"
            ), {"hash": hash_pag, "uid": user_id, "tid": thread_id, "duid": discord_user_id, "nome": nome, "valor": valor})
        # Marca o UID do email como usado no cache IMAP
        if uid:
            for conn in imap_manager.connections.values():
                conn.marcar_uid_usado(uid)
        log_msg(user_id, f"🔒 Pagamento registrado: {hash_pag[:8]}...")
    except Exception as e:
        log_msg(user_id, f"⚠️ Erro ao registrar pagamento: {type(e).__name__}")

def _buscar_pagamento_otimizado(cfg: dict, nome: str, user_id: int):
    if not nome or len(nome.strip()) < 2:
        return None
    from imap_optimizer import buscar_pagamento_imap
    import time
    def log_fn(msg):
        log_msg(user_id, msg)
    t0 = time.time()
    log_msg(user_id, f"🔍 Buscando: '{nome}'")
    resultado = buscar_pagamento_imap(cfg, nome, log_fn, user_id)
    dt = time.time() - t0
    if resultado:
        log_msg(user_id, f"✅ Encontrado: {resultado['pagador']} | R${resultado['valor']} | {resultado['banco']} | {dt:.2f}s")
    else:
        log_msg(user_id, f"❌ Não encontrado: '{nome}' | {dt:.2f}s")
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
    lock_path = os.path.join(LOG_DIR, f"user_{user_id}.lock")
    try:
        if os.path.exists(lock_path):
            with open(lock_path, 'r') as f:
                pid = f.read().strip()
            if pid:
                import psutil
                try:
                    if psutil.pid_exists(int(pid)):
                        log_msg(user_id, f"⚠️ Instância já rodando (PID {pid}). Abortando.")
                        return
                except Exception:
                    pass
        with open(lock_path, 'w') as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

    log_msg(user_id, "="*70)
    log_msg(user_id, "🚀 INICIANDO SELFBOT")
    log_msg(user_id, "="*70)
    
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
    log_msg(user_id, f"🔑 Token válido | User ID: {validation['user_id']} | Tipo: {expiry_check['type']}")

    SERVER_ID = int(config["server_id"])
    CATEGORIA_ID = int(config["categoria_id"]) if config.get("categoria_id", "0") not in ("", "0") else 0
    CANAL_ALVO_ID = CATEGORIA_ID  # compat: pode ser categoria OU canal
    import json as _json
    _rl_raw = config.get("rate_limit_categorias", "")
    try:
        _rl_raw_parsed = _json.loads(_rl_raw) if _rl_raw else []
        CATEGORIAS_EXTRA = set(_normalizar(s) for s in _rl_raw_parsed if s.strip() and not s.strip().isdigit())
        CATEGORIAS_EXTRA_IDS = set(int(s.strip()) for s in _rl_raw_parsed if s.strip().isdigit())
    except Exception:
        CATEGORIAS_EXTRA = set()
        CATEGORIAS_EXTRA_IDS = set()
    log_msg(user_id, f"📂 Categorias extras (nomes): {list(CATEGORIAS_EXTRA) if CATEGORIAS_EXTRA else 'nenhuma'}")
    log_msg(user_id, f"📂 Canais extras (IDs): {list(CATEGORIAS_EXTRA_IDS) if CATEGORIAS_EXTRA_IDS else 'nenhum'}")
    MAX_THREADS_SIMULTANEAS = max(1, min(10, int(config.get("max_threads", 3))))
    _semaforo_threads = asyncio.Semaphore(MAX_THREADS_SIMULTANEAS)
    log_msg(user_id, f"🏠 Servidor ID: {SERVER_ID}")
    log_msg(user_id, f"📂 Categoria/Canal alvo ID: {CATEGORIA_ID}")
    
    _MSG_PADRAO = (
        "🤖 **INSTRUÇÕES DE PAGAMENTO**\n\n"
        "⚠️ **ATENÇÃO:** Bancos como **Inter, PicPay e Mercado Pago** podem atrasar. "
        "Se usar um deles, envie **1 centavo a mais** (ex: R$ 10,01).\n\n"
        "⚡ **AUTO-VERIFICAÇÃO:** Nosso sistema é 100% automático. "
        "Basta enviar a foto do comprovante ou digitar `pg Nome Completo`.\n\n"
        "──────────────────────────────\n"
        "🔍 *Aguardando seu pagamento para liberar a sala...*"
    )
    _msg_raw = config.get("mensagem_entrada", "").strip()
    _legados = ["", "Ola! Use pg Nome Sobrenome para verificar seu pagamento.", "Ol\u00e1! Use pg Nome Sobrenome para verificar pagamento."]

    # SquareCloud pode quebrar encoding em valores vindos do .env/config.
    # Se o texto vier “estranho”/quebrado, usa o padrão seguro.
    def _safe_decode(s: str) -> str:
        try:
            if not isinstance(s, str):
                return s
            # tenta normalizar como utf-8, removendo bytes inválidos
            return s.encode("utf-8", "ignore").decode("utf-8", "ignore").strip()
        except Exception:
            return s

    _msg_raw = _safe_decode(_msg_raw)
    MENSAGEM_ENTRADA = _msg_raw if _msg_raw and _msg_raw not in _legados else _MSG_PADRAO
    IMAGEM_ENTRADA = config.get("imagem_entrada", "").strip()


    # Configurações para reduzir desconexões
    log_msg(user_id, "⚙️ Configurando cliente Discord...")
    
    try:
        client = discord.Client(
            chunk_guilds_at_startup=False,
            heartbeat_timeout=150.0,
            max_messages=100,
            self_bot=True,
        )
    except Exception as e:
        log_msg(user_id, f"❌ Erro ao criar cliente: {e}")
        return

    _clientes[user_id] = client

    threads_com_mensagem: set[int] = _carregar_threads(user_id)
    threads_em_processamento: set[int] = set()  # guard contra race condition
    log_msg(user_id, "-"*70)
    log_msg(user_id, f"🧵 Threads carregadas: {len(threads_com_mensagem)}")
    log_msg(user_id, "-"*70)
    pagamentos_por_thread: dict[int, int] = {}
    salas_ativas: dict[int, str] = {}       # channel_id -> pedidoid
    sala_em_criacao: set[int] = set()       # channel_id que já está criando sala (evita duplicar)
    salas_concluidas: set[int] = set()      # channel_id onde sala já foi enviada — ignora comprovantes
    go_por_thread: dict[int, set] = {}       # channel_id -> set de user_ids

    go_auto_tasks: dict[int, asyncio.Task] = {}  # channel_id -> task do timer
    pg_em_processamento: set[str] = set()
    valores_thread: dict[int, float] = {}   # channel_id -> valor esperado
    valores_pagos: dict[int, float] = {}    # channel_id -> valor já pago acumulado
    pagamentos_usados_global: dict[str, dict] = {}

    async def _get_cat_id_name(channel):
        """Retorna (cat_id, cat_name_norm, parent) de qualquer canal ou thread."""
        guild = channel.guild
        parent_id = getattr(channel, 'parent_id', None)
        if parent_id:
            parent = guild.get_channel(parent_id)
            if parent is None:
                try:
                    parent = await guild.fetch_channel(parent_id)
                except Exception:
                    return parent_id, "", None
        else:
            parent = channel

        cat_id = getattr(parent, 'category_id', None)
        if not cat_id:
            # canal pai nao tem categoria — retorna o proprio canal pai como referencia
            return getattr(parent, 'id', 0), _normalizar_cat(getattr(parent, 'name', '') or ''), parent

        cat_ch = guild.get_channel(cat_id)
        if cat_ch is None:
            try:
                cat_ch = await guild.fetch_channel(cat_id)
            except Exception:
                cat_ch = None

        cat_name = _normalizar_cat(cat_ch.name) if cat_ch else ""
        return cat_id, cat_name, parent

    def _canal_monitorado(channel) -> bool:
        """Aceita categoria OU canal alvo (e threads do canal alvo)."""
        guild = getattr(channel, 'guild', None)
        parent_id = getattr(channel, 'parent_id', None)

        if parent_id and guild:
            if CANAL_ALVO_ID and parent_id == CANAL_ALVO_ID:
                return True
            if parent_id in CATEGORIAS_EXTRA_IDS:
                return True
            parent = guild.get_channel(parent_id)
            if parent is None:
                return False
            # Compara nome do canal pai com categorias extras
            if bool(CATEGORIAS_EXTRA) and _normalizar_cat(getattr(parent, 'name', '') or '') in CATEGORIAS_EXTRA:
                return True
            cat_id = getattr(parent, 'category_id', None)
            if CATEGORIA_ID and cat_id == CATEGORIA_ID:
                return True
            if cat_id in CATEGORIAS_EXTRA_IDS:
                return True
            if cat_id and bool(CATEGORIAS_EXTRA):
                cat_ch = guild.get_channel(cat_id)
                cat_name = _normalizar_cat(cat_ch.name) if cat_ch else ""
                if cat_name in CATEGORIAS_EXTRA:
                    return True
            return False

        if CANAL_ALVO_ID and getattr(channel, "id", None) == CANAL_ALVO_ID:
            return True
        if getattr(channel, "id", None) in CATEGORIAS_EXTRA_IDS:
            return True
        cat = getattr(channel, 'category', None)
        if cat:
            return (
                (CATEGORIA_ID != 0 and cat.id == CATEGORIA_ID)
                or cat.id in CATEGORIAS_EXTRA_IDS
                or (bool(CATEGORIAS_EXTRA) and _normalizar_cat(cat.name) in CATEGORIAS_EXTRA)
            )
        return False

    async def _digitar_e_enviar(canal, texto: str, **kwargs):
        for tentativa in range(3):
            try:
                return await canal.send(texto, **kwargs)
            except discord.HTTPException as e:
                if e.status == 429:
                    await asyncio.sleep(e.retry_after if hasattr(e, 'retry_after') else 5)
                else:
                    raise

    async def _digitar_e_reply(message, texto: str, **kwargs):
        for tentativa in range(3):
            try:
                return await message.reply(texto, allowed_mentions=discord.AllowedMentions.none(), **kwargs)
            except discord.HTTPException as e:
                if e.status == 429:
                    await asyncio.sleep(e.retry_after if hasattr(e, 'retry_after') else 5)
                elif e.code == 200000 or e.code == 50035:
                    return await message.channel.send(texto, allowed_mentions=discord.AllowedMentions.none(), **kwargs)
                else:
                    raise

    def _extrair_valor_mensagem(texto: str) -> float:
        """Extrai valor no formato 'Valor: R$1,50' ou '⤷ R$1,50'"""
        match = re.search(r'[⤷\s]*(?:Valor[:\s]*)?R?\$?\s*([\d]+[.,][\d]{2})', texto, re.IGNORECASE)
        if match:
            valor_str = match.group(1).replace(',', '.')
            try:
                return float(valor_str)
            except ValueError:
                pass
        return 0.0

    async def _ler_valor_thread(canal):
        pass  # desabilitado - causava falsos positivos com mensagens do bot

    async def _enviar_mensagem_entrada(canal):
        import io
        import aiohttp as _aiohttp

        # Delay de sincronizacao apos join na thread
        await asyncio.sleep(7)

        # Leitura dinamica do slowmode: thread primeiro, depois canal pai
        slowmode = getattr(canal, 'slowmode_delay', 0) or 0
        if not slowmode:
            parent = getattr(canal, 'parent', None)
            slowmode = getattr(parent, 'slowmode_delay', 0) or 0
        if slowmode > 0:
            log_msg(user_id, f"⏳ Slowmode detectado: {slowmode}s — aguardando antes de enviar")
            await asyncio.sleep(slowmode)

        if IMAGEM_ENTRADA:
            try:
                async with _aiohttp.ClientSession() as sess:
                    async with sess.get(IMAGEM_ENTRADA, timeout=_aiohttp.ClientTimeout(total=15)) as resp:
                        if resp.status == 200:
                            dados = await resp.read()
                            arquivo = discord.File(io.BytesIO(dados), filename="imagem.png")
                            await _digitar_e_enviar(canal, MENSAGEM_ENTRADA, file=arquivo)
                            log_msg(user_id, "Imagem enviada com sucesso")
                            return
                        else:
                            log_msg(user_id, f"Imagem indisponivel (HTTP {resp.status}), enviando so texto")
            except Exception as exc:
                log_msg(user_id, f"Erro ao enviar imagem: {type(exc).__name__}: {exc}")
        await _digitar_e_enviar(canal, MENSAGEM_ENTRADA)

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
                await _digitar_e_enviar(channel, "✅ **Sala iniciou! Tentando dar go…**")
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
            if 'inf' in nome_canal.lower() or 'infinito' in nome_canal.lower():
                modo_config = SALA_INF
                log_msg(user_id, f"🎮 Modo: Infinito ({nome_canal})")
            else:
                modo_config = SALA_GN  # SALA_PADRAO == SALA_GN
                log_msg(user_id, f"🎮 Modo: Gel Normal ({nome_canal})")

        data = await _criar_sala_api(modo_config)
        if "_erro" in data:
            log_msg(user_id, f"🎮 Erro API sala: {data['_erro']}")
            await _digitar_e_enviar(channel, "Nao foi possivel criar a sala. Erro na API.")
            return False
        if data.get("success") and data.get("sala"):
            sala = data["sala"]
            prefixo = config.get("prefixo_sala", "").strip()
            msg_sala = f"{prefixo} {sala['id']} {sala['senha']}" if prefixo else f"{sala['id']} {sala['senha']}"
            formato_sala = str(config.get("formato_sala", "junto")).strip().lower()
            pedidoid = data.get("pedidoid", "")
            salas_ativas[channel.id] = pedidoid
            go_por_thread[channel.id] = set()
            log_msg(user_id, "="*70)
            log_msg(user_id, f"🎮 SALA CRIADA E ENVIADA")
            log_msg(user_id, f"   └─ Formato: {formato_sala}")
            log_msg(user_id, f"   └─ ID e Senha: {msg_sala}")
            log_msg(user_id, f"   └─ Modo: {modo_config}")
            log_msg(user_id, "="*70)

            if formato_sala == "separado":
                msg_id = f"{prefixo} {sala['id']}" if prefixo else f"{sala['id']}"
                await _digitar_e_enviar(channel, msg_id)
                await asyncio.sleep(1)
                await _digitar_e_enviar(channel, str(sala['senha']))
            else:
                await _digitar_e_enviar(channel, msg_sala)
            _incrementar_sala(user_id)  # contabiliza só após envio confirmado
            salas_concluidas.add(channel.id)  # para de ler comprovantes nesta thread
            await asyncio.sleep(4)
            await _digitar_e_enviar(channel, "⚡ **IMPORTANTE:** Após ambos entrarem, digitem `go` aqui no chat para iniciar! A sala dá go automático em **5 minutos**.")

            async def go_auto(ch=channel, pid=pedidoid):
                await asyncio.sleep(300)
                if salas_ativas.get(ch.id) == pid:
                    log_msg(user_id, "🎮 Go automático (5 min)")
                    await _digitar_e_enviar(ch, "⏰ Tempo esgotado! Iniciando sala automaticamente...")
                    await _dar_go(ch, pid)

            task = asyncio.ensure_future(go_auto())
            go_auto_tasks[channel.id] = task
            return True
        log_msg(user_id, f"🎮 Erro criar sala: {data}")
        await _digitar_e_enviar(channel, "Nao foi possivel criar a sala.")
        return False

    def _thread_bloqueada_por_nome_entrada(thread_name: str) -> bool:
        """Regra de liberação da mensagem de entrada.

        Ajuste para modo por canal:
        - Bloqueia apenas padrão explícito de espera (aguardando-<numero>)
        - Fora isso, permite enviar normalmente.
        """
        if not thread_name:
            return False
        tn = thread_name.lower()
        if re.search(r"\baguardando-\d+\b", tn):
            return True
        return False


    async def _enviar_em_thread(thread: discord.Thread):
        if _thread_bloqueada_por_nome_entrada(getattr(thread, "name", "")):
            return

        cat_id, cat_name, parent = await _get_cat_id_name(thread)
        parent_id = getattr(parent, "id", None) or getattr(thread, 'parent_id', None)
        parent_name = _normalizar_cat(getattr(parent, 'name', '') or '')

        monitorada = (
            (CANAL_ALVO_ID and parent_id == CANAL_ALVO_ID)
            or parent_id in CATEGORIAS_EXTRA_IDS
            or (CATEGORIA_ID != 0 and cat_id == CATEGORIA_ID)
            or cat_id in CATEGORIAS_EXTRA_IDS
            or (bool(CATEGORIAS_EXTRA) and cat_name in CATEGORIAS_EXTRA)
            or (bool(CATEGORIAS_EXTRA) and parent_name in CATEGORIAS_EXTRA)
        )

        if not monitorada:
            return

        log_msg(user_id, f"🧵 Thread: '{thread.name}' | canal_pai='{parent_name}' | cat='{cat_name}' | monitorada=True")


        if thread.id in threads_com_mensagem or thread.id in threads_em_processamento:
            return
        threads_em_processamento.add(thread.id)
        threads_com_mensagem.add(thread.id)
        _salvar_thread(user_id, thread.id)
        log_msg(user_id, "-"*70)
        log_msg(user_id, f"🧵 NOVA THREAD DETECTADA")
        log_msg(user_id, f"   └─ Nome: {thread.name}")
        log_msg(user_id, f"   └─ ID: {thread.id}")
        async with _semaforo_threads:
            try:
                await _enviar_mensagem_entrada(thread)
                log_msg(user_id, f"✅ Mensagem de entrada enviada")
                log_msg(user_id, "-"*70)
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
        log_msg(user_id, "="*70)
        log_msg(user_id, f"✅ CONECTADO | {client.user} (ID: {client.user.id})")
        log_msg(user_id, "="*70)

        # Força cache dos canais via HTTP
        guild = client.get_guild(SERVER_ID)
        if guild:
            try:
                canais = await guild.fetch_channels()
                log_msg(user_id, f"📡 {len(canais)} canais carregados no cache")
            except Exception as e:
                log_msg(user_id, f"⚠️ fetch_channels falhou: {e}")

        guild = client.get_guild(SERVER_ID)
        if guild:
            cat = None
            if CATEGORIA_ID:
                cat = guild.get_channel(CATEGORIA_ID)
                if cat is None:
                    try:
                        cat = await client.fetch_channel(CATEGORIA_ID)
                    except Exception:
                        pass
            log_msg(user_id, f"🌐 Servidor: {guild.name}")
            if CATEGORIA_ID:
                log_msg(user_id, f"📂 Categoria principal: {cat.name if cat else 'NAO ENCONTRADA'}")
            else:
                log_msg(user_id, "📂 Categoria principal: não configurada (usando apenas extras)")
            for nome_extra in CATEGORIAS_EXTRA:
                encontrada = next((c for c in guild.channels if _normalizar(c.name) == nome_extra), None)
                log_msg(user_id, f"📂 Extra '{nome_extra}': {'✅ ' + encontrada.name if encontrada else '❌ NAO ENCONTRADA'}")
        else:
            log_msg(user_id, f"❌ Servidor {SERVER_ID} nao encontrado.")

        if not _monitor_iniciado:
            _monitor_iniciado = True
            await asyncio.sleep(3)
            asyncio.ensure_future(verificar_threads_iniciais())
            asyncio.ensure_future(monitorar_threads())
            asyncio.ensure_future(iniciar_imap())
            asyncio.ensure_future(health_check())
            asyncio.ensure_future(reset_diario())

    async def iniciar_imap():
        log_msg(user_id, "📬 Iniciando conexão IMAP persistente...")
        imap_manager.get_cache(user_id, config)
        imap_manager.set_log(user_id, log_msg)

        # Restaura UIDs já usados do banco para evitar reuso após restart
        try:
            from sqlalchemy import text
            engine = _get_db_engine()
            with engine.connect() as con:
                rows = con.execute(text(
                    "SELECT hash FROM pagamentos_usados WHERE user_id=:uid"
                ), {"uid": user_id}).fetchall()
            conn = imap_manager.connections.get(user_id)
            if conn:
                for row in rows:
                    h = row[0]
                    if h.startswith("uid_"):
                        conn._uids_usados.add(h[4:])  # remove prefixo "uid_"
            log_msg(user_id, f"🔒 {len(rows)} pagamentos anteriores carregados")
        except Exception as e:
            log_msg(user_id, f"⚠️ Erro ao restaurar UIDs: {e}")

        def _on_novo_pix(entry: dict):
            """Chamado pela thread do monitor IMAP quando novo PIX chega."""
            pagador = entry.get("pagador", "")
            valor = entry.get("valor", "N/A")
            banco = entry.get("banco", "")
            uid = entry.get("uid", "")
            log_msg(user_id, f"🔔 PIX em tempo real: {pagador} | R${valor} | {banco}")

            loop = _loops.get(user_id)
            if not loop or loop.is_closed():
                return

            async def _notificar():
                guild = client.get_guild(SERVER_ID)
                if not guild:
                    return
                pagador_norm = _normalizar(pagador)
                # Percorre todas as threads monitoradas abertas
                for canal in guild.channels:
                    for thread in getattr(canal, "threads", []):
                        if not _canal_monitorado(thread):
                            continue
                        if thread.id not in threads_com_mensagem:
                            continue
                        # Verifica se já foi usado (por UID) e se é duplicata recente do mesmo banco
                        hash_pag = _gerar_hash_pagamento(pagador, valor, banco, uid)
                        if _is_pix_duplicado_recente(pagador, valor, banco, uid):
                            continue
                        if _verificar_pagamento_usado(hash_pag, user_id)["usado"]:
                            continue
                        # Busca mensagens recentes da thread para tentar associar ao nome
                        # Só considera comandos pg dos últimos 2 minutos
                        try:
                            msgs_recentes = [m async for m in thread.history(limit=20)]
                        except Exception:
                            continue
                        nome_encontrado = None
                        autor_encontrado = None
                        agora_ts = datetime.utcnow()
                        for m in msgs_recentes:
                            if m.author == client.user:
                                continue
                            idade = (agora_ts - m.created_at.replace(tzinfo=None)).total_seconds()
                            if idade > 120:
                                continue
                            nome_cmd = _extrair_nome(m.content)
                            if nome_cmd and _match_nomes(_normalizar(nome_cmd), pagador_norm):
                                nome_encontrado = nome_cmd
                                autor_encontrado = m.author
                                break
                        if not nome_encontrado:
                            continue
                        # Notifica a thread sem registrar o pagamento como usado
                        # O registro ocorre apenas quando o usuário usa pg/comprovante
                        log_msg(user_id, f"🔔 Notificando thread {thread.name} | {pagador} | R${valor}")
                        try:
                            mention = autor_encontrado.mention if autor_encontrado else ""
                            await _digitar_e_enviar(thread,
                                f"💰 **PIX DETECTADO!**\n\n"
                                f"👤 **Pagador:** `{pagador}`\n"
                                f"💵 **Valor:** `R$ {valor}`\n"
                                f"🏦 **Banco:** `{banco}`\n\n"
                                f"{mention} Digite `pg {pagador}` para confirmar!"
                            )
                        except Exception as exc:
                            log_msg(user_id, f"⚠️ Erro ao notificar thread: {exc}")

            asyncio.run_coroutine_threadsafe(_notificar(), loop)

        imap_manager.set_pix_callback(user_id, _on_novo_pix)
        log_msg(user_id, "✅ IMAP persistente ativo com notificação em tempo real")

    async def reset_diario():
        import pytz
        tz = pytz.timezone("America/Sao_Paulo")
        while not _stop_flags.get(user_id, False):
            agora = datetime.now(tz)
            # Calcula segundos até meia-noite
            meia_noite = agora.replace(hour=0, minute=0, second=0, microsecond=0)
            from datetime import timedelta
            proximo = meia_noite + timedelta(days=1)
            espera = (proximo - agora).total_seconds()
            await asyncio.sleep(espera)
            if _stop_flags.get(user_id, False):
                break
            threads_com_mensagem.clear()
            try:
                from sqlalchemy import text
                engine = _get_db_engine()
                with engine.begin() as con:
                    con.execute(text("DELETE FROM threads_enviadas WHERE user_id=:uid"), {"uid": user_id})
                    con.execute(text("DELETE FROM pagamentos_usados WHERE user_id=:uid"), {"uid": user_id})
            except Exception as e:
                log_msg(user_id, f"⚠️ Erro ao limpar threads no banco: {type(e).__name__}")
            # Limpa UIDs usados no cache IMAP
            for conn in imap_manager.connections.values():
                if conn.config.get("email_user") == config.get("email_user"):
                    conn._uids_usados.clear()
                    with conn._cache_lock:
                        conn._cache.clear()
            log_msg(user_id, "🔄 Threads e pagamentos resetados à meia-noite (horário de SP)")

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
            # Busca threads ativas via API (não depende do cache local)
            try:
                active = await guild.active_threads()
                for thread in active:
                    if thread.id not in threads_com_mensagem:
                        asyncio.ensure_future(_enviar_em_thread(thread))
                log_msg(user_id, f"✅ Verificação inicial: {len(active)} threads ativas")
            except Exception:
                # Fallback para cache local
                for canal in guild.channels:
                    for thread in getattr(canal, "threads", []):
                        if thread.id not in threads_com_mensagem:
                            asyncio.ensure_future(_enviar_em_thread(thread))
                log_msg(user_id, "✅ Verificação inicial concluída (cache)")
        except Exception as exc:
            log_msg(user_id, f"❌ Erro na verificação inicial: {exc}")

    async def monitorar_threads():
        ultima_verificacao = datetime.now()
        ultimo_fetch = datetime.now()
        while not _stop_flags.get(user_id, False):
            try:
                guild = client.get_guild(SERVER_ID)
                if guild:
                    agora = datetime.now()
                    # A cada 60s faz fetch ativo das threads via API
                    if (agora - ultimo_fetch).total_seconds() > 60:
                        try:
                            active = await guild.active_threads()
                            for thread in active:
                                if thread.id not in threads_com_mensagem:
                                    asyncio.ensure_future(_enviar_em_thread(thread))
                            ultimo_fetch = agora
                        except Exception:
                            pass
                    else:
                        for canal in guild.channels:
                            for thread in getattr(canal, "threads", []):
                                if thread.id not in threads_com_mensagem:
                                    asyncio.ensure_future(_enviar_em_thread(thread))
                    if (agora - ultima_verificacao).total_seconds() > 300:
                        log_msg(user_id, f"📊 Monitoramento: {len(threads_com_mensagem)} threads processadas")
                        ultima_verificacao = agora
            except Exception as exc:
                log_msg(user_id, f"❌ Erro no monitoramento: {exc}")
            await asyncio.sleep(5)

    @client.event
    async def on_thread_create(thread: discord.Thread):
        asyncio.ensure_future(_enviar_em_thread(thread))

    @client.event
    async def on_thread_join(thread: discord.Thread):
        asyncio.ensure_future(_enviar_em_thread(thread))

    @client.event
    async def on_message(message: discord.Message):
        if not message.guild or message.guild.id != SERVER_ID:
            return
        channel = message.channel

        # Para threads nao processadas, usa fetch para garantir deteccao
        if isinstance(channel, discord.Thread):
            if channel.id not in threads_com_mensagem and channel.id not in threads_em_processamento:
                cat_id, cat_name, parent = await _get_cat_id_name(channel)
                parent_id = getattr(parent, "id", None) or getattr(channel, 'parent_id', None)
                monitorada = (
                    (CANAL_ALVO_ID and parent_id == CANAL_ALVO_ID)
                    or parent_id in CATEGORIAS_EXTRA_IDS
                    or (CATEGORIA_ID != 0 and cat_id == CATEGORIA_ID)
                    or cat_id in CATEGORIAS_EXTRA_IDS
                    or (bool(CATEGORIAS_EXTRA) and cat_name in CATEGORIAS_EXTRA)
                    or (bool(CATEGORIAS_EXTRA) and _normalizar_cat(getattr(parent, 'name', '') or '') in CATEGORIAS_EXTRA)
                )
                if monitorada:
                    log_msg(user_id, f"\U0001f9f5 Nova thread: {channel.name} | cat: {cat_name} | parent_id: {parent_id}")
                    asyncio.ensure_future(_enviar_em_thread(channel))
                else:
                    return
            elif not _canal_monitorado(channel):
                return
        elif not _canal_monitorado(channel):
            return
        # Verifica se a mensagem contém o valor esperado e atualiza
        if channel.id not in valores_thread:
            valor = _extrair_valor_mensagem(message.content)
            if valor > 0:
                valores_thread[channel.id] = valor
                log_msg(user_id, f"💰 Valor esperado detectado: R${valor:.2f}")

        conteudo = message.content.strip()
        cmd = conteudo.lower()

# Comandos de sala - aceita de qualquer mensagem (selfbot nao dispara on_message para si mesmo)
        if cmd == ".help":
            if message.author != client.user:
                return
            try:
                await message.delete()
            except Exception:
                pass
            await _digitar_e_enviar(channel,
                "**🤖 Comandos do Selfbot**\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "**💰 Pagamentos**\n"
                "`pg Nome Sobrenome` — Verifica se o pagamento chegou no e-mail\n"
                "`pg Nome` — Busca por primeiro nome (ex: pg Matheus)\n\n"
                "**🎮 Salas**\n"
                "`.gn` — Cria uma sala de Gel Normal\n"
                "`.gi` — Cria uma sala de Gel Infinito\n"
                "`.go` — Força o go da sala ativa na thread\n"
                "`go` — Confirma entrada na sala (precisa de 2 jogadores)\n\n"
                "**📊 Informações**\n"
                "`!salas` — Mostra quantas salas foram criadas (hoje, semana, mês, total)\n\n"
                "**🔍 Verificação automática**\n"
                "Envie uma imagem/comprovante — O bot lê e confirma o pagamento automaticamente\n\n"
                "**🛠️ Admin**\n"
                "`.scan` — Varre todas as threads e envia mensagem de entrada nas novas\n"
                "`.v Nome` — Força verificação de pagamento sem precisar do cliente digitar"
            )
            return

        if cmd == ".go":
            if channel.id in salas_ativas:
                pedidoid = salas_ativas[channel.id]
                try:
                    await message.delete()
                except Exception:
                    pass
                log_msg(user_id, f"🎮 .go forçado na thread {channel.id}")
                await _dar_go(channel, pedidoid)
            else:
                await _digitar_e_enviar(channel, "⚠️ Nenhuma sala ativa nesta thread.")
            return

        if cmd in (".gn", ".gi"):
            log_msg(user_id, f"Comando {cmd} detectado de {message.author}")
            # Check limit before creating room
            usadas, limite = _get_salas_info(user_id)
            if usadas >= limite:
                await _digitar_e_enviar(channel, f"⚠️ Limite de salas atingido ({usadas}/{limite}). Aguarde ou peça mais salas ao admin.")
                log_msg(user_id, f"⛔ Limite: {usadas}/{limite}")
                return
            salaid = SALA_INF if cmd == ".gi" else SALA_GN
            msg_req = await _digitar_e_enviar(channel, "Criando sala...")
            await _enviar_sala(channel, salaid)
            await msg_req.delete()
            return

        if cmd == "!salas":
            usadas, limite = _get_salas_info(user_id)
            disponiveis = max(limite - usadas, 0)
            try:
                from sqlalchemy import text
                from datetime import datetime, timedelta
                engine = _get_db_engine()
                with engine.connect() as con:
                    con.execute(text("CREATE TABLE IF NOT EXISTS sala_historico (id SERIAL PRIMARY KEY, user_id INTEGER, criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"))
                    agora = datetime.utcnow()
                    hoje = con.execute(text("SELECT COUNT(*) FROM sala_historico WHERE user_id=:uid AND criado_em >= :dt"), {"uid": user_id, "dt": agora.replace(hour=0, minute=0, second=0, microsecond=0)}).scalar()
                    semana = con.execute(text("SELECT COUNT(*) FROM sala_historico WHERE user_id=:uid AND criado_em >= :dt"), {"uid": user_id, "dt": agora - timedelta(weeks=1)}).scalar()
                    mes = con.execute(text("SELECT COUNT(*) FROM sala_historico WHERE user_id=:uid AND criado_em >= :dt"), {"uid": user_id, "dt": agora - timedelta(days=30)}).scalar()
                    total = con.execute(text("SELECT COUNT(*) FROM sala_historico WHERE user_id=:uid"), {"uid": user_id}).scalar()
            except Exception:
                hoje = semana = mes = total = 0
            await _digitar_e_enviar(channel,
                f"🎮 **Salas FF — Resumo**\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ Disponíveis : **{disponiveis}/{limite}**\n"
                f"📅 Hoje        : **{hoje}**\n"
                f"📆 Esta semana : **{semana}**\n"
                f"🗓️ Este mês    : **{mes}**\n"
                f"🏆 Total geral : **{total}**"
            )
            return

        if message.author == client.user:
            return

        # Sistema de GO para iniciar sala
        if re.fullmatch(r"go+[!.]*", cmd.strip()) and channel.id in salas_ativas:
            go_set = go_por_thread.setdefault(channel.id, set())
            go_set.add(message.author.id)
            count = len(go_set)
            try:
                await message.add_reaction("✅")
            except Exception:
                pass
            log_msg(user_id, f"🎮 Go de {message.author.name} ({count}/2)")
            if count >= 2:
                log_msg(user_id, "🎮 Dois usuários deram go - iniciando sala...")
                await _dar_go(channel, salas_ativas[channel.id])
            return

        # Verifica se e um comprovante (imagem anexada)
        if message.attachments and not _extrair_nome(conteudo):
            if channel.id in salas_concluidas:
                return
            for attachment in message.attachments:
                ct = getattr(attachment, 'content_type', '') or ''
                ext = attachment.filename.split('.')[-1].lower()
                if any(t in ct for t in ['image', 'pdf']) or ext in ('png', 'jpg', 'jpeg', 'gif', 'webp', 'pdf'):
                    msg_ocr = await _digitar_e_reply(message, "🔍 **Verificando comprovante… Aguarde!**")
                    try:
                        resultado_ocr = await asyncio.wait_for(
                            asyncio.get_running_loop().run_in_executor(
                                None, lambda a=attachment: __import__('ocr_comprovante').ler_comprovante_url(a.url, "")
                            ),
                            timeout=20
                        )
                    except asyncio.TimeoutError:
                        resultado_ocr = {"encontrado": False, "erro": "Timeout"}
                    try:
                        await msg_ocr.delete()
                    except Exception:
                        pass

                    if resultado_ocr.get("erro"):
                        if resultado_ocr.get("fake"):
                            await _digitar_e_reply(message, f"🚨 **Comprovante inválido!**\n{resultado_ocr['erro']}")
                            log_msg(user_id, f"🚨 Comprovante fake detectado: {message.author}")
                        else:
                            await _digitar_e_reply(message, f"⚠️ Erro ao ler comprovante: {resultado_ocr['erro']}")
                    elif not resultado_ocr.get("nome_encontrado") and resultado_ocr.get("valor") == "N/A":
                        await _digitar_e_reply(message, "❌ Não foi possível identificar o comprovante.")
                    else:
                        valor_str = resultado_ocr.get('valor', 'N/A')
                        banco = resultado_ocr.get('banco', 'Comprovante')
                        pagador = resultado_ocr.get('pagador', 'Desconhecido')
                        
                        # Gera hash do pagamento para verificar duplicação
                        hash_pag = _gerar_hash_pagamento(pagador, valor_str, banco)
                        verificacao = _verificar_pagamento_usado(hash_pag, user_id)
                        
                        if verificacao["usado"]:
                            await _digitar_e_reply(message,
                                f"🚨 **PAGAMENTO JÁ UTILIZADO!**\n\n"
                                f"❌ Este pagamento já foi usado anteriormente.\n"
                                f"🕒 **Usado em:** {verificacao['timestamp']}\n\n"
                                f"⚠️ Cada pagamento só pode ser usado uma vez.\n"
                                f"Por favor, envie um novo comprovante."
                            )
                            log_msg(user_id, "="*70)
                            log_msg(user_id, f"🚨 PAGAMENTO DUPLICADO BLOQUEADO")
                            log_msg(user_id, f"   └─ Nome: {pagador}")
                            log_msg(user_id, f"   └─ User: {message.author}")
                            log_msg(user_id, f"   └─ Hash: {hash_pag[:16]}...")
                            log_msg(user_id, "="*70)
                            return
                        
                        # Verifica se o valor está correto
                        if channel.id in valores_thread and valor_str != 'N/A':
                            try:
                                valor_pago = float(valor_str.replace(',', '.'))
                                valor_esperado = valores_thread[channel.id]
                                valor_acumulado = valores_pagos.get(channel.id, 0.0) + valor_pago
                                
                                if valor_acumulado < valor_esperado:
                                    valores_pagos[channel.id] = valor_acumulado
                                    diferenca = valor_esperado - valor_acumulado
                                    await _digitar_e_reply(message,
                                        f"⚠️ **VALOR INSUFICIENTE**\n\n"
                                        f"💰 **Valor esperado:** R$ {valor_esperado:.2f}\n"
                                        f"💸 **Valor enviado agora:** R$ {valor_pago:.2f}\n"
                                        f"📊 **Total pago:** R$ {valor_acumulado:.2f}\n"
                                        f"❌ **Faltam:** R$ {diferenca:.2f}\n\n"
                                        f"Por favor, envie o restante do pagamento."
                                    )
                                    log_msg(user_id, f"⚠️ Valor insuficiente: R${valor_acumulado:.2f} < R${valor_esperado:.2f}")
                                    return
                                else:
                                    valores_pagos.pop(channel.id, None)
                                    log_msg(user_id, f"✅ Valor completo atingido: R${valor_acumulado:.2f}")
                            except ValueError:
                                pass
                        
                        _registrar_pagamento_usado(hash_pag, user_id, channel.id, message.author.id, pagador, valor_str, resultado_ocr.get('uid'))
                        pagamentos_por_thread[channel.id] = pagamentos_por_thread.get(channel.id, 0) + 1
                        log_msg(user_id, "-"*70)
                        log_msg(user_id, f"💰 COMPROVANTE CONFIRMADO")
                        log_msg(user_id, f"   └─ Nome: {pagador}")
                        log_msg(user_id, f"   └─ Valor: R${valor_str}")
                        log_msg(user_id, f"   └─ User: {message.author}")
                        log_msg(user_id, f"   └─ Progresso: {pagamentos_por_thread[channel.id]}/2 pagamentos")
                        log_msg(user_id, "-"*70)
                        await _digitar_e_reply(message,
                            f"✅ **PAGAMENTO CONFIRMADO**\n\n"
                            f"👤 **Cliente:** {message.author.mention}\n"
                            f"📝 **Nome:** `{pagador}`\n"
                            f"💰 **Valor:** `R$ {valor_str} (BRL)`\n"
                            f"🔍 **Destino:** `e-mail {banco}`\n"
                            f"🎉 **Sua vaga está garantida! A sala será enviada aqui.**"
                        )
            if pagamentos_por_thread.get(channel.id, 0) >= 2:

                # evita criar duas salas na mesma thread (race condition)
                if channel.id in sala_em_criacao:
                    return


                sala_em_criacao.add(channel.id)
                try:
                    pagamentos_por_thread[channel.id] = 0
                    usadas, limite = _get_salas_info(user_id)
                    if usadas >= limite:
                        await _digitar_e_enviar(channel, f"Limite de salas atingido ({usadas}/{limite}).")
                        log_msg(user_id, f"⛔ Limite: {usadas}/{limite}")
                        return

                    msg_req = await _digitar_e_enviar(channel, "Solicitando Sala...")
                    try:
                        ok = await _enviar_sala(channel)
                        if not ok:
                            log_msg(user_id, f"⚠️ _enviar_sala retornou False (thread={channel.id})")
                    finally:
                        await msg_req.delete()
                except Exception as exc:
                    log_msg(user_id, f"❌ Erro ao criar sala (thread={channel.id}): {type(exc).__name__}: {exc}")
                finally:
                    sala_em_criacao.discard(channel.id)


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
        log_msg(user_id, "-"*70)
        log_msg(user_id, f"💰 COMANDO PG | Nome: {nome_busca} | User: {message.author}")

        msg_fila = await _digitar_e_reply(message,
            "⏳ **Verificação na fila!**\n"
            "Sua posição: `1`\n"
            "Estimativa de espera: **35 segundos**."
        )

        try:
            resultado = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(None, lambda: _buscar_pagamento_otimizado(config, nome_busca, user_id)),
                timeout=90
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
            valor_str = resultado['valor']
            pagador = resultado.get('pagador', nome_busca)
            banco = resultado.get('banco', 'Email')
            
            # Se veio com UID, o imap_optimizer já marcou como usado ao retornar
            # Apenas registra no banco para histórico (ON CONFLICT DO NOTHING evita duplicata)
            hash_pag = _gerar_hash_pagamento(pagador, valor_str, banco, resultado.get('uid'))

            # Verifica se o valor está correto
            if channel.id in valores_thread and valor_str != 'N/A':
                try:
                    valor_pago = float(valor_str.replace(',', '.'))
                    valor_esperado = valores_thread[channel.id]
                    valor_acumulado = valores_pagos.get(channel.id, 0.0) + valor_pago
                    
                    if valor_acumulado < valor_esperado:
                        valores_pagos[channel.id] = valor_acumulado
                        diferenca = valor_esperado - valor_acumulado
                        await _digitar_e_reply(message,
                            f"⚠️ **VALOR INSUFICIENTE**\n\n"
                            f"💰 **Valor esperado:** R$ {valor_esperado:.2f}\n"
                            f"💸 **Valor enviado agora:** R$ {valor_pago:.2f}\n"
                            f"📊 **Total pago:** R$ {valor_acumulado:.2f}\n"
                            f"❌ **Faltam:** R$ {diferenca:.2f}\n\n"
                            f"Por favor, envie o restante do pagamento."
                        )
                        log_msg(user_id, f"⚠️ Valor insuficiente: R${valor_acumulado:.2f} < R${valor_esperado:.2f}")
                        return
                    else:
                        valores_pagos.pop(channel.id, None)
                        log_msg(user_id, f"✅ Valor completo atingido: R${valor_acumulado:.2f}")
                except ValueError:
                    pass
            
            _registrar_pagamento_usado(hash_pag, user_id, channel.id, message.author.id, pagador, valor_str, resultado.get('uid'))
            
            await _digitar_e_reply(message,
                f"✅ **PAGAMENTO CONFIRMADO**\n\n"
                f"👤 **Cliente:** {message.author.mention}\n"
                f"📝 **Nome:** `{resultado.get('pagador', nome_busca)}`\n"
                f"💰 **Valor:** `R$ {valor_str} (BRL)`\n"
                f"🔍 **Destino:** `e-mail {resultado['banco']}`\n"
                f"🎉 **Sua vaga está garantida! A sala será enviada aqui.**"
            )
            pagamentos_por_thread[channel.id] = pagamentos_por_thread.get(channel.id, 0) + 1
            log_msg(user_id, f"💰 Progresso: {pagamentos_por_thread[channel.id]}/2 pagamentos confirmados")
            log_msg(user_id, "-"*70)

            if pagamentos_por_thread.get(channel.id, 0) >= 2:
                pagamentos_por_thread[channel.id] = 0

                usadas, limite = _get_salas_info(user_id)
                if usadas >= limite:
                    await _digitar_e_enviar(channel, f"Limite de salas atingido ({usadas}/{limite}).")
                    log_msg(user_id, f"⛔ Limite: {usadas}/{limite}")
                    return
                msg_req = await _digitar_e_enviar(channel, "Solicitando Sala...")
                await _enviar_sala(channel)
                await msg_req.delete()
        else:
            await _digitar_e_reply(message, f"Pagamento nao confirmado para {nome_busca}.")

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
            while not _stop_flags.get(user_id, False):
                try:
                    await client.start(TOKEN)
                except discord.LoginFailure as e:
                    log_msg(user_id, f"Token inválido ou expirado: {e}")
                    return
                except discord.HTTPException as e:
                    log_msg(user_id, f"Erro HTTP Discord: {e}")
                except Exception as e:
                    log_msg(user_id, f"Erro na conexão: {type(e).__name__}: {str(e)[:100]}")
                if _stop_flags.get(user_id, False):
                    break
                log_msg(user_id, "🔄 Reconectando em 10s...")
                await asyncio.sleep(10)
                if not client.is_closed():
                    await client.close()
                await asyncio.sleep(2)
        
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
        lock_path = os.path.join(LOG_DIR, f"user_{user_id}.lock")
        try:
            os.remove(lock_path)
        except Exception:
            pass
        log_msg(user_id, "🔴 Selfbot encerrado.")
