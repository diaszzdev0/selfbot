import os
import multiprocessing
import secrets
from dotenv import load_dotenv
load_dotenv()
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, make_response
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, LicenseKey, BotConfig
from bot_logic import run_selfbot

app = Flask(__name__)

# Configuração de segurança melhorada
secret_key = os.getenv("FLASK_SECRET_KEY")
if not secret_key:
    # Gera uma chave secreta aleatória se não estiver configurada
    secret_key = secrets.token_hex(32)
    print("AVISO: Usando chave secreta temporária. Configure FLASK_SECRET_KEY no .env")

app.secret_key = secret_key
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.config["SESSION_COOKIE_SECURE"] = False  # Mude para True em produção com HTTPS
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# Configuração do banco de dados
_db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selfbot.db")
_database_url = os.getenv("DATABASE_URL", f"sqlite:///{_db_path}")

# Alguns provedores colocam valores "sujos" em variáveis.
# Se o valor não for uma URL válida do SQLAlchemy, cai para SQLite.
def _normalize_database_url(val: str) -> str:
    if not val:
        return f"sqlite:///{_db_path}"
    val = str(val).strip()
    # Remove possíveis aspas/linhas extras
    val = val.strip('"\'')
    if val.startswith("postgres://"):
        val = val.replace("postgres://", "postgresql://", 1)
    return val

_database_url = _normalize_database_url(_database_url)

app.config["SQLALCHEMY_DATABASE_URI"] = _database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
    "connect_args": {"check_same_thread": False} if "sqlite" in _database_url else {}
}

# Validação bem simples antes de iniciar o app.
# Evita o crash quando DATABASE_URL vem com texto (ex: "... esta dando esse erro ...").
if not (app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite:///") or app.config["SQLALCHEMY_DATABASE_URI"].startswith("postgresql://")):
    print("[db] DATABASE_URL inválida; usando SQLite local")
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{_db_path}"


db.init_app(app)

import json as _json_module
def _from_json_filter(s):
    try:
        return _json_module.loads(s) if s else []
    except Exception:
        return []
app.jinja_env.filters['from_json'] = _from_json_filter


_migrations_done = False

def _run_migrations():
    """Roda migrações de colunas novas — compatível com SQLite e PostgreSQL."""
    global _migrations_done
    if _migrations_done:
        return
    try:
        from sqlalchemy import text, inspect
        inspector = inspect(db.engine)
        cols = [c["name"] for c in inspector.get_columns("bot_config")]
        migrations = [
            ("modo_sala_id",          "ALTER TABLE bot_config ADD COLUMN modo_sala_id VARCHAR(30)"),
            ("rate_limit_categorias", "ALTER TABLE bot_config ADD COLUMN rate_limit_categorias TEXT"),
            ("max_threads",           "ALTER TABLE bot_config ADD COLUMN max_threads INTEGER DEFAULT 3"),
            ("imagem_entrada",        "ALTER TABLE bot_config ADD COLUMN imagem_entrada TEXT"),
            ("prefixo_sala",          "ALTER TABLE bot_config ADD COLUMN prefixo_sala VARCHAR(20)"),
            ("formato_sala",          "ALTER TABLE bot_config ADD COLUMN formato_sala VARCHAR(20) DEFAULT 'junto'"),
            ("auto_sala",              "ALTER TABLE bot_config ADD COLUMN auto_sala VARCHAR(1) DEFAULT '1'"),
        ]
        with db.engine.begin() as con:
            for col, sql in migrations:
                if col not in cols:
                    try:
                        con.execute(text(sql))
                        print(f"[migration] added column {col}")
                    except Exception:
                        pass  # coluna já existe em alguns dialetos
        _migrations_done = True
    except Exception as _e:
        print(f"[migration] {_e}")

@app.before_request
def _ensure_migrations():
    try:
        _run_migrations()
    except Exception:
        pass


with app.app_context():
    db.create_all()
    _run_migrations()
    # Garante que o admin existe sempre
    from werkzeug.security import generate_password_hash
    if not User.query.filter_by(is_admin=True).first():
        u = User(username="DiasDev", password=generate_password_hash("DiasDev0"), is_admin=True)
        db.session.add(u)
        db.session.commit()
        print("[OK] Admin criado: DiasDev / DiasDev0")
    # Cria tabelas auxiliares usadas pelo bot_logic
    from sqlalchemy import text
    with db.engine.begin() as con:
        con.execute(text('''
            CREATE TABLE IF NOT EXISTS pagamentos_usados (
                hash TEXT PRIMARY KEY,
                user_id INTEGER,
                thread_id BIGINT,
                discord_user_id BIGINT,
                nome TEXT,
                valor TEXT,
                usado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )'''))
        con.execute(text('''
            CREATE TABLE IF NOT EXISTS threads_enviadas (
                user_id INTEGER,
                thread_id BIGINT,
                PRIMARY KEY(user_id, thread_id)
            )'''))
        con.execute(text('''
            CREATE TABLE IF NOT EXISTS sala_historico (
                id SERIAL PRIMARY KEY,
                user_id INTEGER,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )'''))

# Dicionário para controlar processos com limite
MAX_PROCESSOS = 50
processos: dict[int, multiprocessing.Process] = {}

def _matar_processos_anteriores():
    import psutil
    try:
        atual = psutil.Process()
        for filho in atual.children(recursive=True):
            try:
                filho.kill()
            except Exception:
                pass
    except Exception:
        pass

_matar_processos_anteriores()


def _limpar_processos_mortos():
    """Remove processos mortos do dicionário"""
    mortos = []
    for user_id, processo in processos.items():
        if not processo.is_alive():
            mortos.append(user_id)
    
    for user_id in mortos:
        processos.pop(user_id, None)
    
    return len(mortos)


def _config_dict(cfg: BotConfig) -> dict:
    """Converte configuração do banco para dicionário com validação"""
    if not cfg:
        return {}
    
    # Validação básica dos campos obrigatórios
    required_fields = ["discord_token", "server_id"]
    for field in required_fields:
        if not getattr(cfg, field, None):
            raise ValueError(f"Campo obrigatório '{field}' não configurado")
    
    def _clean_id(val):
        """Strips accidental 'key: value' prefixes, keeps only the numeric part."""
        s = str(val).strip()
        if ":" in s:
            s = s.split(":", 1)[-1].strip()
        return s

    return {
        "discord_token": str(cfg.discord_token).strip(),
        "server_id": _clean_id(cfg.server_id),
        "categoria_id": _clean_id(cfg.categoria_id) if cfg.categoria_id else "0",
        "email_user": str(cfg.email_user or "").strip(),
        "email_pass": str(cfg.email_pass or "").strip(),
        "imap_server": str(cfg.imap_server or "imap.gmail.com").strip(),
        "mensagem_entrada": str(cfg.mensagem_entrada or "Olá! Use pg Nome Sobrenome para verificar pagamento.").strip(),
        "imagem_entrada": str(cfg.imagem_entrada or "").strip(),
        "prefixo_sala": str(cfg.prefixo_sala or "").strip(),
        "rate_limit_categorias": str(cfg.rate_limit_categorias or "").strip(),
        "max_threads": int(cfg.max_threads or 3),
        "formato_sala": str(cfg.formato_sala or "junto").strip().lower(),
        "auto_sala": str(getattr(cfg, 'auto_sala', '1') or '1').strip(),
    }


def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        user = db.session.get(User, session["user_id"])
        if not user or not user.is_admin:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ── Cliente Auth ─────────────────────────────────────────────────────────────

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if "cliente_id" not in session:
            return redirect(url_for("login_cliente"))
        return f(*args, **kwargs)
    return decorated


@app.route("/cliente/login", methods=["GET", "POST"])
def login_cliente():
    erro = None
    saved_user = request.cookies.get("cliente_user", "")
    saved_key = request.cookies.get("cliente_key", "")
    if request.method == "POST":
        username = request.form["username"].strip()
        key_str = request.form["key"].strip()
        lembrar = request.form.get("lembrar")
        user = User.query.filter_by(username=username, is_admin=False).first()
        if user and user.license and user.license.key == key_str:
            if not user.license.valida():
                erro = "Licenca expirada."
            else:
                session["cliente_id"] = user.id
                resp = make_response(redirect(url_for("painel_cliente")))
                if lembrar:
                    resp.set_cookie("cliente_user", username, max_age=30*24*3600)
                    resp.set_cookie("cliente_key", key_str, max_age=30*24*3600)
                else:
                    resp.delete_cookie("cliente_user")
                    resp.delete_cookie("cliente_key")
                return resp
        else:
            erro = "Usuario ou key incorretos."
    return render_template("login_cliente.html", erro=erro, saved_user=saved_user, saved_key=saved_key)


@app.route("/cliente/logout")
def logout_cliente():
    session.pop("cliente_id", None)
    return redirect(url_for("login_cliente"))


# Default limit is 10 - used when creating new BotStatus records
_DEFAULT_LIMITE_SALAS = 10

def _get_bot_status_cliente(user_id: int):
    from models import BotStatus
    s = BotStatus.query.filter_by(user_id=user_id).first()
    if not s:
        # Use default limit of 10 instead of 0 to avoid blocking new users
        s = BotStatus(user_id=user_id, ativo=False, salas_usadas=0, limite_salas=_DEFAULT_LIMITE_SALAS)
        db.session.add(s)
        db.session.commit()
    return s


def _render_cliente(user, cfg, ativo, msg=None, msg_tipo=None):
    from datetime import datetime
    dias_restantes = None
    if user and user.license and user.license.expira_em:
        delta = user.license.expira_em - datetime.utcnow()
        dias_restantes = max(delta.days, 0)
    bot_status = _get_bot_status_cliente(user.id) if user else None
    return render_template("cliente.html", user=user, cfg=cfg, ativo=ativo,
                           dias_restantes=dias_restantes, bot_status=bot_status,
                           msg=msg, msg_tipo=msg_tipo)


@app.route("/cliente")
@login_required
def painel_cliente():
    user = db.session.get(User, session["cliente_id"])
    cfg = user.config
    ativo = user.id in processos and processos[user.id].is_alive()
    return _render_cliente(user, cfg, ativo)


@app.route("/cliente/salvar_config", methods=["POST"])
@login_required
def cliente_salvar_config():
    user = db.session.get(User, session["cliente_id"])
    cfg = user.config or BotConfig(user_id=user.id)
    cfg.discord_token = request.form["discord_token"]
    cfg.server_id = request.form["server_id"]
    cfg.categoria_id = request.form["categoria_id"]
    cfg.email_user = request.form["email_user"]
    cfg.email_pass = request.form["email_pass"]
    cfg.imap_server = request.form["imap_server"]
    cfg.mensagem_entrada = request.form["mensagem_entrada"]
    imagem = request.form.get("imagem_entrada", "").strip()
    cfg.imagem_entrada = imagem if imagem else None
    prefixo = request.form.get("prefixo_sala", "").strip()
    cfg.prefixo_sala = prefixo if request.form.get("usar_prefixo") and prefixo else None
    modo = request.form.get("modo_sala_id", "").strip()
    cfg.modo_sala_id = modo if modo else None
    formato = request.form.get("formato_sala", "junto").strip().lower()
    cfg.formato_sala = formato if formato in ("junto", "separado") else "junto"
    cfg.auto_sala = "1" if request.form.get("auto_sala") else "0"
    import json as _json
    rl_cats = [v.strip() for v in request.form.getlist("rate_limit_cat") if v.strip()]
    cfg.rate_limit_categorias = _json.dumps(rl_cats) if rl_cats else None
    try:
        cfg.max_threads = max(1, min(10, int(request.form.get("max_threads", 3))))
    except (ValueError, TypeError):
        cfg.max_threads = 3
    db.session.add(cfg)
    db.session.commit()
    
    # Se bot estiver rodando, reinicia para pegar nova config
    if user.id in processos and processos[user.id].is_alive():
        from bot_logic import parar_selfbot
        parar_selfbot(user.id)
        p = processos.pop(user.id, None)
        if p and p.is_alive():
            p.terminate()
            p.join(timeout=3)
        db.session.expire_all()
        user = db.session.get(User, session["cliente_id"])
        np = multiprocessing.Process(target=run_selfbot, args=(_config_dict(user.config), user.id), daemon=True)
        np.start()
        processos[user.id] = np
        cfg = user.config
        return _render_cliente(user, cfg, True, "Configuração salva e bot reiniciado!", "success")

    user = db.session.get(User, session["cliente_id"])
    cfg = user.config
    ativo = user.id in processos and processos[user.id].is_alive()
    return _render_cliente(user, cfg, ativo, "Configuração salva com sucesso!", "success")


@app.route("/cliente/start_bot/<int:user_id>")
@login_required
def cliente_start_bot(user_id: int):
    if session["cliente_id"] != user_id:
        return redirect(url_for("painel_cliente"))
    
    # Verifica limite de processos
    if len(processos) >= MAX_PROCESSOS:
        user = db.session.get(User, session["cliente_id"])
        cfg = user.config
        return _render_cliente(user, cfg, False, "Limite máximo de bots atingido. Tente novamente mais tarde.", "warning")
    
    if user_id in processos and processos[user_id].is_alive():
        user = db.session.get(User, session["cliente_id"])
        cfg = user.config
        ativo = True
        return _render_cliente(user, cfg, ativo, "Bot já está rodando!", "info")
    
    user = db.session.get(User, user_id)
    if not user or not user.config:
        return _render_cliente(user, None, False, "SELFBOT NÃO CONFIGURADO", "danger")
    
    try:
        # Valida configuração antes de iniciar
        config_dict = _config_dict(user.config)
        
        # Limpa processos mortos antes de criar novo
        _limpar_processos_mortos()
        
        log_path = os.path.join(os.path.dirname(__file__), "logs", f"user_{user_id}.log")
        with open(log_path, "w", encoding="utf-8"):
            pass
        
        p = multiprocessing.Process(
            target=run_selfbot, 
            args=(config_dict, user_id), 
            daemon=True,
            name=f"selfbot_user_{user_id}"
        )
        p.start()
        processos[user_id] = p
        
        cfg = user.config
        ativo = True
        return _render_cliente(user, cfg, ativo, "Bot iniciado com sucesso!", "success")
        
    except ValueError as e:
        return _render_cliente(user, user.config, False, f"Erro de configuração: {str(e)}", "danger")
    except Exception as e:
        return _render_cliente(user, user.config, False, f"Erro ao iniciar bot: {str(e)[:100]}", "danger")


@app.route("/cliente/stop_bot/<int:user_id>")
@login_required
def cliente_stop_bot(user_id: int):
    from bot_logic import parar_selfbot
    if session["cliente_id"] != user_id:
        return redirect(url_for("painel_cliente"))
    parar_selfbot(user_id)
    p = processos.pop(user_id, None)
    if p and p.is_alive():
        p.terminate()
        p.join(timeout=3)
    user = db.session.get(User, session["cliente_id"])
    cfg = user.config
    ativo = False
    return _render_cliente(user, cfg, ativo, "Bot parado com sucesso!", "success")


@app.route("/cliente/restart_bot/<int:user_id>")
@login_required
def cliente_restart_bot(user_id: int):
    from bot_logic import parar_selfbot
    if session["cliente_id"] != user_id:
        return redirect(url_for("painel_cliente"))
    parar_selfbot(user_id)
    p = processos.pop(user_id, None)
    if p and p.is_alive():
        p.terminate()
        p.join(timeout=3)
    log_path = os.path.join(os.path.dirname(__file__), "logs", f"user_{user_id}.log")
    with open(log_path, "w", encoding="utf-8"):
        pass
    # Força refresh do banco para pegar credenciais atualizadas
    db.session.expire_all()
    user = db.session.get(User, user_id)
    if not user or not user.config:
        return _render_cliente(user, None, False, "SELFBOT NÃO CONFIGURADO", "danger")
    p = multiprocessing.Process(target=run_selfbot, args=(_config_dict(user.config), user_id), daemon=True)
    p.start()
    processos[user_id] = p
    cfg = user.config
    ativo = True
    return _render_cliente(user, cfg, ativo, "Bot reiniciado com sucesso!", "success")


@app.route("/cliente/debug_imap")
@login_required
def cliente_debug_imap():
    from imap_optimizer import imap_manager
    user_id = session["cliente_id"]
    user = db.session.get(User, user_id)
    if not user or not user.config:
        return jsonify({"erro": "Usuário sem configuração"}), 400

    cfg = _config_dict(user.config)
    imap_manager.get_cache(user_id, cfg)

    conn = imap_manager.connections.get(user_id)
    if not conn:
        return jsonify({"erro": "Conexão IMAP não iniciada"}), 400

    with conn._cache_lock:
        itens = list(conn._cache.items())[-5:]

    emails = []
    for uid, entry in itens:
        if not entry:
            continue
        emails.append({
            "uid": uid,
            "banco": entry.get("banco"),
            "valor": entry.get("valor"),
            "pagador": entry.get("pagador"),
            "data": str(entry.get("data")),
        })

    return jsonify({
        "total_cache": len(conn._cache),
        "uids_usados": len(conn._uids_usados),
        "emails": emails[::-1]
    })


@app.route("/cliente/api_saldo")
@login_required
def cliente_api_saldo():
    user = db.session.get(User, session["cliente_id"])
    if not user:
        session.clear()
        return jsonify({"status": "error", "salas": "?"}), 401
    bs = _get_bot_status_cliente(session["cliente_id"])
    disponiveis = max(bs.limite_salas - bs.salas_usadas, 0)
    try:
        import requests as req
        r = req.get("https://salasff.com/modos?key=266vq0badxid7jpcf96t", timeout=5)
        total_api = r.json().get("salas", 0)
    except Exception:
        total_api = "?"
    return jsonify({"status": "ok", "user_disponiveis": disponiveis, "user_limite": bs.limite_salas, "salas": total_api})


@app.route("/cliente/api_modos")
@login_required
def cliente_api_modos():
    API_KEY = "266vq0badxid7jpcf96t"
    try:
        import requests as req
        r = req.get(f"https://salasff.com/modos?key={API_KEY}", timeout=10)
        data = r.json()
        modos = []
        if isinstance(data, list):
            modos = [{"salaid": m.get("salaid", m.get("id")), "nome": m.get("nome", m.get("name", "Gel Normal"))} for m in data]
        elif isinstance(data, dict) and "modos" in data:
            modos = [{"salaid": m.get("salaid"), "nome": m.get("nome")} for m in data["modos"]]
        return jsonify({"status": "ok", "modos": modos[:20]})
    except Exception:
        return jsonify({"status": "error", "modos": []})


@app.route("/cliente/imap_pix")
@login_required
def cliente_imap_pix():
    import re
    from collections import deque
    user_id = session["cliente_id"]
    log_path = os.path.join(os.path.dirname(__file__), "logs", f"user_{user_id}.log")
    pix = []
    # Read only the last 5000 lines to avoid scanning huge logs
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            recent_lines = deque(f, maxlen=5000)
            for linha in recent_lines:
                m = re.search(r"pagador='([^']+)'\s*\|\s*R\$([\d,\.]+)\s*\|\s*(\S+)", linha)
                if m:
                    hora_m = re.match(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", linha)
                    hora = hora_m.group(1)[11:] if hora_m else "--:--:--"
                    pix.append({
                        "hora": hora,
                        "pagador": m.group(1).title(),
                        "valor": f"R$ {m.group(2)}",
                        "banco": m.group(3),
                        "usado": "MATCH" in linha or "confirmado" in linha.lower()
                    })
    except FileNotFoundError:
        pass
    # Retorna os 50 mais recentes
    return jsonify({"pix": pix[-50:][::-1], "total": len(pix)})



@app.route("/cliente/stream_logs/<int:user_id>")
@login_required
def cliente_stream_logs(user_id: int):
    import time
    from flask import Response
    if session["cliente_id"] != user_id:
        return Response("data: Sem permissao\n\n", mimetype="text/event-stream")
    log_path = os.path.join(os.path.dirname(__file__), "logs", f"user_{user_id}.log")

    def generate():
        with open(log_path, "a", encoding="utf-8"):
            pass
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            # Mostra as ultimas 50 linhas ao abrir
            linhas = f.readlines()
            for linha in linhas[-50:]:
                if linha.strip():
                    yield f"data: {linha.rstrip()}\n\n"
            last_heartbeat = time.time()
            while True:
                linha = f.readline()
                if linha:
                    yield f"data: {linha.rstrip()}\n\n"
                    last_heartbeat = time.time()
                else:
                    time.sleep(0.3)
                    if time.time() - last_heartbeat > 20:
                        yield ": heartbeat\n\n"
                        last_heartbeat = time.time()

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET", "POST"])
def login():
    erro = None
    saved_user = request.cookies.get("admin_user", "")
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        lembrar = request.form.get("lembrar")
        user = User.query.filter_by(username=username, is_admin=True).first()
        if user and check_password_hash(user.password, password):
            session["user_id"] = user.id
            resp = make_response(redirect(url_for("admin")))
            if lembrar:
                resp.set_cookie("admin_user", username, max_age=30*24*3600, httponly=True)
            else:
                resp.delete_cookie("admin_user")
            return resp
        else:
            erro = "Usuário ou senha incorretos."
    return render_template("login_manager.html", erro=erro, saved_user=saved_user)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── Admin ─────────────────────────────────────────────────────────────────────

@app.route("/admin/limpar_sessao")
@admin_required
def limpar_sessao():
    session.clear()
    return redirect(url_for("login"))


@app.route("/admin")
@admin_required
def admin():
    from models import BotStatus
    users = User.query.filter_by(is_admin=False).all()
    keys = LicenseKey.query.order_by(LicenseKey.criado_em.desc()).all()
    status = {s.user_id: s.ativo for s in BotStatus.query.all()}
    salas_info = {s.user_id: {"usadas": s.salas_usadas, "limite": s.limite_salas} for s in BotStatus.query.all()}
    
    # Pega mensagens da sessão
    msg = session.pop("msg", None)
    msg_tipo = session.pop("msg_tipo", None)
    msg_gerar = session.pop("msg_gerar", None)
    msg_gerar_tipo = session.pop("msg_gerar_tipo", None)
    usuario_key_gerado = session.pop("usuario_key_gerado", None)
    
    seriais = session.pop("seriais", None)
    resultado_resgate = session.pop("resultado_resgate", None)
    saldo_salas = session.pop("saldo_salas", None)
    teste_api = session.pop("teste_api", None)
    keys_criadas = session.pop("keys_criadas", None)
    verificacao_key = session.pop("verificacao_key", None)
    adicao_salas = session.pop("adicao_salas", None)
    
    return render_template("admin.html", users=users, keys=keys, status=status, salas_info=salas_info, 
                         seriais=seriais, resultado_resgate=resultado_resgate, saldo_salas=saldo_salas, 
                         teste_api=teste_api, keys_criadas=keys_criadas, verificacao_key=verificacao_key, 
                         adicao_salas=adicao_salas, msg=msg, msg_tipo=msg_tipo, msg_gerar=msg_gerar, 
                         msg_gerar_tipo=msg_gerar_tipo, usuario_key_gerado=usuario_key_gerado)


@app.route("/admin/gerar_usuario_key", methods=["POST"])
@admin_required
def gerar_usuario_key():
    username = request.form["username"].strip()
    tipo = request.form.get("tipo", "mensal")
    
    if User.query.filter_by(username=username).first():
        session["msg_gerar"] = "Usuário já existe."
        session["msg_gerar_tipo"] = "danger"
        return redirect(url_for("admin") + "#gerar")
    
    # Gera a key
    lic = LicenseKey.gerar(tipo)
    lic.usado = True
    
    # Cria o usuário
    user = User(
        username=username,
        password=generate_password_hash(username),
        key_id=lic.id,
    )
    
    db.session.add(user)
    db.session.commit()
    
    # Armazena os dados para exibir na página
    session["usuario_key_gerado"] = {
        "username": username,
        "key": lic.key,
        "tipo": tipo,
        "expira_em": lic.expira_em
    }
    
    return redirect(url_for("admin") + "#gerar")


@app.route("/admin/criar_usuario", methods=["POST"])
@admin_required
def criar_usuario():
    username = request.form["username"].strip()
    tipo = request.form.get("tipo", "mensal")
    if User.query.filter_by(username=username).first():
        session["msg"] = "Usuário já existe."
        session["msg_tipo"] = "danger"
        return redirect(url_for("admin") + "#usuarios")
    lic = LicenseKey.gerar(tipo)
    lic.usado = True
    user = User(
        username=username,
        password=generate_password_hash(username),
        key_id=lic.id,
    )
    db.session.add(user)
    db.session.commit()
    session["msg"] = f"Usuário {username} criado com sucesso!"
    session["msg_tipo"] = "success"
    return redirect(url_for("admin") + "#usuarios")


@app.route("/admin/deletar_usuario/<int:user_id>")
@admin_required
def deletar_usuario(user_id: int):
    p = processos.get(user_id)
    if p and p.is_alive():
        p.terminate()
        p.join(timeout=3)
    processos.pop(user_id, None)
    user = db.session.get(User, user_id)
    if not user:
        from flask import abort
        abort(404)
    if user.config:
        db.session.delete(user.config)
    db.session.delete(user)
    db.session.commit()
    return redirect(url_for("admin"))


@app.route("/admin/revogar_key/<int:key_id>")
@admin_required
def revogar_key(key_id: int):
    k = db.session.get(LicenseKey, key_id)
    if not k:
        from flask import abort
        abort(404)
    # desvincula usuarios antes de deletar
    User.query.filter_by(key_id=k.id).update({"key_id": None})
    db.session.delete(k)
    db.session.commit()
    return redirect(url_for("admin"))


@app.route("/admin/renovar_key/<int:user_id>", methods=["POST"])
@admin_required
def renovar_key(user_id: int):
    from datetime import datetime, timedelta
    user = db.session.get(User, user_id)
    if not user:
        from flask import abort
        abort(404)
    tipo = request.form.get("tipo", "mensal")
    if not user.license:
        return redirect(url_for("admin"))
    expiracoes = {"semanal": timedelta(days=7), "mensal": timedelta(days=30)}
    base = max(user.license.expira_em, datetime.utcnow()) if user.license.expira_em else datetime.utcnow()
    user.license.tipo = tipo
    user.license.expira_em = base + expiracoes[tipo] if tipo in expiracoes else None
    db.session.commit()
    return redirect(url_for("admin"))


@app.route("/admin/criar_admin", methods=["POST"])
@admin_required
def criar_admin():
    if not User.query.filter_by(username=request.form["username"]).first():
        u = User(
            username=request.form["username"],
            password=generate_password_hash(request.form["password"]),
            is_admin=True,
        )
        db.session.add(u)
        db.session.commit()
    return redirect(url_for("admin"))


@app.route("/admin/status_json")
@admin_required
def status_json():
    from models import BotStatus
    # Verifica status real dos processos
    status_real = {}
    for user_id, processo in processos.items():
        status_real[user_id] = processo.is_alive() if processo else False
    
    # Atualiza o banco de dados com o status real
    for user_id, ativo in status_real.items():
        bot_status = BotStatus.query.filter_by(user_id=user_id).first()
        if bot_status:
            bot_status.ativo = ativo
        else:
            bot_status = BotStatus(user_id=user_id, ativo=ativo)
            db.session.add(bot_status)
    
    # Adiciona usuários que não têm processo rodando
    users = User.query.filter_by(is_admin=False).all()
    for user in users:
        if user.id not in status_real:
            status_real[user.id] = False
            bot_status = BotStatus.query.filter_by(user_id=user.id).first()
            if bot_status:
                bot_status.ativo = False
            else:
                bot_status = BotStatus(user_id=user.id, ativo=False)
                db.session.add(bot_status)
    
    db.session.commit()
    
    # Retorna status como strings para compatibilidade com JavaScript
    data = {str(user_id): status for user_id, status in status_real.items()}
    return jsonify(data)

def _get_salasff_api_key():
    """Obtém a chave da API SalasFF do .env ou usa padrão"""
    from dotenv import load_dotenv
    load_dotenv()
    return os.getenv("SALASFF_API_KEY", "266vq0badxid7jpcf96t")


def _test_salasff_api(api_key: str) -> dict:
    """Testa se a API SalasFF está funcionando"""
    import requests as req
    
    test_endpoints = [
        f"https://salasff.com/api/status?key={api_key}",
        f"https://salasff.com/status?key={api_key}",
        f"https://salasff.com/modos?key={api_key}"
    ]
    
    for url in test_endpoints:
        try:
            resp = req.get(url, timeout=5, headers={'User-Agent': 'SelfBot-Manager/1.0'})
            if resp.status_code == 200:
                return {"status": "ok", "message": "API funcionando", "endpoint": url}
        except Exception:
            continue
    
    return {"status": "error", "message": "API não responde ou chave inválida"}


@app.route("/admin/saldo_salas")
@admin_required
def saldo_salas():
    API_KEY = _get_salasff_api_key()
    import requests as req
    
    try:
        # Primeiro testa se a API está funcionando
        api_test = _test_salasff_api(API_KEY)
        if api_test["status"] == "error":
            session["saldo_salas"] = f"Erro: {api_test['message']}"
            return redirect(url_for("admin"))
        
        # Tenta diferentes endpoints da API
        endpoints = [
            f"https://salasff.com/api/saldo?key={API_KEY}",
            f"https://salasff.com/saldo?key={API_KEY}",
            f"https://salasff.com/modos?key={API_KEY}",
            f"https://salasff.com/api/balance?key={API_KEY}"
        ]
        
        saldo = None
        endpoint_usado = None
        
        for url in endpoints:
            try:
                resp = req.get(url, timeout=10, headers={'User-Agent': 'SelfBot-Manager/1.0'})
                if resp.status_code == 200:
                    content_type = resp.headers.get('content-type', '')
                    
                    if 'application/json' in content_type:
                        data = resp.json()
                        # Verifica diferentes campos possíveis
                        for field in ['saldo', 'salas', 'balance', 'credits', 'rooms']:
                            if field in data and isinstance(data[field], (int, float)):
                                saldo = data[field]
                                endpoint_usado = url
                                break
                        if saldo is not None:
                            break
                    else:
                        # Se não é JSON, verifica se é um número simples
                        texto = resp.text.strip()
                        if texto.isdigit():
                            saldo = int(texto)
                            endpoint_usado = url
                            break
            except Exception:
                continue
        
        if saldo is None:
            session["saldo_salas"] = "Erro: API não retornou dados válidos. Verifique a chave da API."
        else:
            session["saldo_salas"] = f"{saldo} salas disponíveis (via {endpoint_usado.split('/')[-1]})"
                
    except Exception as exc:
        session["saldo_salas"] = f"Erro de conexão: {str(exc)[:100]}"
    
    return redirect(url_for("admin"))


@app.route("/admin/gerar_seriais", methods=["POST"])
@admin_required
def gerar_seriais():
    import string
    quantidade = int(request.form.get("quantidade", 10))
    seriais = []
    for _ in range(quantidade):
        codigo = "SALA" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(12))
        seriais.append(codigo)
    session["seriais"] = seriais
    return redirect(url_for("admin") + "#salas")


@app.route("/admin/resgatar_seriais", methods=["POST"])
@admin_required
def resgatar_seriais():
    from dotenv import load_dotenv
    load_dotenv()
    API_KEY = os.getenv("SALASFF_KEY", "266vq0badxid7jpcf96t")
    import requests as req
    raw = request.form.get("seriais", "")
    seriais = [s.strip() for s in raw.replace(",", "\n").splitlines() if s.strip()]
    if not seriais:
        session["resultado_resgate"] = {"resgatados": [], "nao_resgatados": [], "mensagem": "Nenhum serial fornecido"}
        return redirect(url_for("admin"))
    
    seriais_str = ",".join(seriais)
    
    # Tenta diferentes endpoints para resgate
    endpoints = [
        f"https://salasff.com/resgatar?key={API_KEY}&serials={seriais_str}",
        f"https://salasff.com/api/resgatar?key={API_KEY}&serials={seriais_str}",
        f"https://salasff.com/redeem?key={API_KEY}&codes={seriais_str}"
    ]
    
    for url in endpoints:
        try:
            resp = req.get(url, timeout=15, headers={'User-Agent': 'SelfBot-Manager/1.0'})
            if resp.status_code == 200:
                content_type = resp.headers.get('content-type', '')
                if 'application/json' in content_type:
                    data = resp.json()
                    session["resultado_resgate"] = {
                        "resgatados": data.get("codigos_resgatados", data.get("success", [])),
                        "nao_resgatados": data.get("codigos_nao_resgatados", data.get("failed", [])),
                        "mensagem": data.get("mensagem", data.get("message", "Resgate processado")),
                    }
                    return redirect(url_for("admin"))
        except Exception:
            continue
    
    session["resultado_resgate"] = {
        "resgatados": [], 
        "nao_resgatados": seriais, 
        "mensagem": "Erro: API não disponível ou chave inválida"
    }
    return redirect(url_for("admin"))


@app.route("/admin/criar_key_salas", methods=["POST"])
@admin_required
def criar_key_salas():
    API_KEY = _get_salasff_api_key()
    import requests as req
    
    quantidade = int(request.form.get("quantidade", 1))
    tipo = request.form.get("tipo", "premium")  # premium, vip, etc
    duracao = int(request.form.get("duracao", 30))  # dias
    
    # Validação de entrada
    if quantidade <= 0 or quantidade > 100:
        session["keys_criadas"] = {
            "keys": [],
            "quantidade": 0,
            "tipo": tipo,
            "duracao": duracao,
            "mensagem": "Quantidade deve ser entre 1 e 100"
        }
        return redirect(url_for("admin"))
    
    if duracao <= 0 or duracao > 365:
        session["keys_criadas"] = {
            "keys": [],
            "quantidade": 0,
            "tipo": tipo,
            "duracao": duracao,
            "mensagem": "Duração deve ser entre 1 e 365 dias"
        }
        return redirect(url_for("admin"))
    
    try:
        # Primeiro testa se a API está funcionando
        api_test = _test_salasff_api(API_KEY)
        if api_test["status"] == "error":
            session["keys_criadas"] = {
                "keys": [],
                "quantidade": 0,
                "tipo": tipo,
                "duracao": duracao,
                "mensagem": f"API offline: {api_test['message']}"
            }
            return redirect(url_for("admin"))
        
        # Tenta diferentes endpoints para criar keys
        endpoints = [
            f"https://salasff.com/api/criar?key={API_KEY}&quantidade={quantidade}&tipo={tipo}&duracao={duracao}",
            f"https://salasff.com/criar_key?key={API_KEY}&qty={quantidade}&type={tipo}&days={duracao}",
            f"https://salasff.com/generate?key={API_KEY}&amount={quantidade}&plan={tipo}&duration={duracao}",
            f"https://salasff.com/api/generate_keys?key={API_KEY}&count={quantidade}&tier={tipo}&days={duracao}"
        ]
        
        for url in endpoints:
            try:
                resp = req.get(url, timeout=20, headers={'User-Agent': 'SelfBot-Manager/1.0'})
                if resp.status_code == 200:
                    content_type = resp.headers.get('content-type', '')
                    
                    if 'application/json' in content_type:
                        data = resp.json()
                        
                        # Verifica diferentes formatos de resposta
                        keys_geradas = None
                        if 'keys' in data and isinstance(data['keys'], list):
                            keys_geradas = data['keys']
                        elif 'codigos' in data and isinstance(data['codigos'], list):
                            keys_geradas = data['codigos']
                        elif 'codes' in data and isinstance(data['codes'], list):
                            keys_geradas = data['codes']
                        elif 'success' in data and isinstance(data['success'], list):
                            keys_geradas = data['success']
                        
                        if keys_geradas and len(keys_geradas) > 0:
                            session["keys_criadas"] = {
                                "keys": keys_geradas[:quantidade],  # Limita à quantidade solicitada
                                "quantidade": len(keys_geradas),
                                "tipo": tipo,
                                "duracao": duracao,
                                "mensagem": data.get('message', data.get('mensagem', f'{len(keys_geradas)} keys criadas com sucesso')),
                                "endpoint": url.split('/')[-1]
                            }
                            return redirect(url_for("admin"))
                            
            except Exception:
                continue
        
        session["keys_criadas"] = {
            "keys": [],
            "quantidade": 0,
            "tipo": tipo,
            "duracao": duracao,
            "mensagem": "Erro: Não foi possível criar as keys. API pode estar offline ou chave inválida."
        }
                
    except Exception as exc:
        session["keys_criadas"] = {
            "keys": [],
            "quantidade": 0,
            "tipo": tipo,
            "duracao": duracao,
            "mensagem": f"Erro: {str(exc)[:100]}"
        }
    
    return redirect(url_for("admin"))


@app.route("/admin/verificar_key_sala", methods=["POST"])
@admin_required
def verificar_key_sala():
    API_KEY = _get_salasff_api_key()
    import requests as req
    
    key_verificar = request.form.get("key_verificar", "").strip()
    
    if not key_verificar:
        session["verificacao_key"] = {"key": "", "status": "Erro", "info": "Key não fornecida"}
        return redirect(url_for("admin"))
    
    if len(key_verificar) < 5:
        session["verificacao_key"] = {"key": key_verificar, "status": "Erro", "info": "Key muito curta"}
        return redirect(url_for("admin"))
    
    try:
        # Primeiro testa se a API está funcionando
        api_test = _test_salasff_api(API_KEY)
        if api_test["status"] == "error":
            session["verificacao_key"] = {
                "key": key_verificar,
                "status": "Erro",
                "info": f"API offline: {api_test['message']}"
            }
            return redirect(url_for("admin"))
        
        # Tenta diferentes endpoints para verificar key
        endpoints = [
            f"https://salasff.com/api/verificar?key={API_KEY}&code={key_verificar}",
            f"https://salasff.com/verificar?key={API_KEY}&serial={key_verificar}",
            f"https://salasff.com/check?key={API_KEY}&code={key_verificar}",
            f"https://salasff.com/api/check_key?key={API_KEY}&target={key_verificar}"
        ]
        
        for url in endpoints:
            try:
                resp = req.get(url, timeout=10, headers={'User-Agent': 'SelfBot-Manager/1.0'})
                if resp.status_code == 200:
                    content_type = resp.headers.get('content-type', '')
                    
                    if 'application/json' in content_type:
                        data = resp.json()
                        session["verificacao_key"] = {
                            "key": key_verificar,
                            "status": data.get('status', data.get('state', 'Desconhecido')),
                            "info": data.get('info', data.get('message', data.get('mensagem', 'Sem informações'))),
                            "salas_restantes": data.get('salas_restantes', data.get('remaining', data.get('balance', 'N/A'))),
                            "expira_em": data.get('expira_em', data.get('expires', data.get('expiry', 'N/A'))),
                            "tipo": data.get('tipo', data.get('plan', data.get('tier', 'N/A'))),
                            "endpoint": url.split('/')[-1]
                        }
                        return redirect(url_for("admin"))
                    else:
                        # Se não é JSON, tenta interpretar resposta simples
                        texto = resp.text.strip().lower()
                        if 'valid' in texto or 'válid' in texto:
                            session["verificacao_key"] = {
                                "key": key_verificar,
                                "status": "Válida",
                                "info": resp.text.strip(),
                                "endpoint": url.split('/')[-1]
                            }
                            return redirect(url_for("admin"))
                        elif 'invalid' in texto or 'inválid' in texto:
                            session["verificacao_key"] = {
                                "key": key_verificar,
                                "status": "Inválida",
                                "info": resp.text.strip(),
                                "endpoint": url.split('/')[-1]
                            }
                            return redirect(url_for("admin"))
                            
            except Exception:
                continue
        
        session["verificacao_key"] = {
            "key": key_verificar,
            "status": "Erro",
            "info": "Não foi possível verificar a key em nenhum endpoint"
        }
                
    except Exception as exc:
        session["verificacao_key"] = {
            "key": key_verificar,
            "status": "Erro",
            "info": f"Erro de conexão: {str(exc)[:100]}"
        }
    
    return redirect(url_for("admin"))


@app.route("/admin/testar_api_salas")
@admin_required
def testar_api_salas():
    """Testa se a API SalasFF está funcionando"""
    API_KEY = _get_salasff_api_key()
    
    # Testa a API
    resultado = _test_salasff_api(API_KEY)
    
    if resultado["status"] == "ok":
        session["teste_api"] = {
            "status": "success",
            "mensagem": f"API funcionando! Endpoint: {resultado['endpoint']}",
            "chave": API_KEY[:10] + "..."
        }
    else:
        session["teste_api"] = {
            "status": "error",
            "mensagem": resultado["message"],
            "chave": API_KEY[:10] + "..."
        }
    
    return redirect(url_for("admin") + "#salas")


@app.route("/admin/debug_imap")
@admin_required
def debug_imap_admin():
    from imap_optimizer import imap_manager
    nome = request.args.get("nome", "").strip()
    if not nome:
        return jsonify({"error": "Parâmetro 'nome' obrigatório"}), 400

    try:
        user = User.query.filter_by(is_admin=False).first()
        if not user or not user.config:
            return jsonify({"error": "Nenhum usuário com config"}), 404

        cfg = _config_dict(user.config)
        imap_manager.get_cache(user.id, cfg)
        conn = imap_manager.connections.get(user.id)
        if not conn:
            return jsonify({"error": "Conexão IMAP não iniciada"}), 400

        resultado = conn.buscar(nome)

        debug = {
            "nome_pesquisado": nome,
            "resultado": resultado,
            "user_demo": user.username,
            "total_cache": len(conn._cache),
            "uids_usados": len(conn._uids_usados)
        }

        return jsonify(debug)
    except Exception as e:
        return jsonify({"error": str(e)}), 500



@app.route("/admin/adicionar_salas", methods=["POST"])
@admin_required
def adicionar_salas():
    from dotenv import load_dotenv
    load_dotenv()
    API_KEY = os.getenv("SALASFF_KEY", "266vq0badxid7jpcf96t")
    import requests as req
    
    quantidade_adicionar = int(request.form.get("quantidade_adicionar", 0))
    
    try:
        # Tenta diferentes endpoints para adicionar salas
        endpoints = [
            f"https://salasff.com/api/adicionar?key={API_KEY}&quantidade={quantidade_adicionar}",
            f"https://salasff.com/adicionar?key={API_KEY}&qty={quantidade_adicionar}",
            f"https://salasff.com/add?key={API_KEY}&amount={quantidade_adicionar}"
        ]
        
        for url in endpoints:
            try:
                resp = req.get(url, timeout=15, headers={'User-Agent': 'SelfBot-Manager/1.0'})
                if resp.status_code == 200:
                    content_type = resp.headers.get('content-type', '')
                    if 'application/json' in content_type:
                        data = resp.json()
                        session["adicao_salas"] = {
                            "quantidade": quantidade_adicionar,
                            "status": data.get('status', 'success'),
                            "mensagem": data.get('message', data.get('mensagem', f'{quantidade_adicionar} salas adicionadas')),
                            "saldo_atual": data.get('saldo_atual', data.get('balance', 'N/A'))
                        }
                        return redirect(url_for("admin"))
            except Exception:
                continue
        
        session["adicao_salas"] = {
            "quantidade": quantidade_adicionar,
            "status": "error",
            "mensagem": "Erro: Não foi possível adicionar salas",
            "saldo_atual": "N/A"
        }
                
    except Exception as exc:
        session["adicao_salas"] = {
            "quantidade": quantidade_adicionar,
            "status": "error",
            "mensagem": f"Erro: {exc}",
            "saldo_atual": "N/A"
        }
    
    return redirect(url_for("admin"))


@app.route("/admin/api_saldo_salas")
@admin_required
def admin_api_saldo_salas():
    API_KEY = "266vq0badxid7jpcf96t"
    try:
        import requests as req
        r = req.get(f"https://salasff.com/modos?key={API_KEY}", timeout=5)
        data = r.json()
        salas = data.get("salas", "?")
    except Exception:
        salas = "?"
    return jsonify({"salas": salas})


@admin_required
def imap_stats():
    from imap_optimizer import imap_manager
    stats = imap_manager.get_global_stats()
    return jsonify(stats)


@app.route("/admin/imap_stats_user/<int:user_id>")
@admin_required
def imap_stats_user(user_id: int):
    from imap_optimizer import imap_manager
    conn = imap_manager.connections.get(user_id)
    if conn:
        with conn._cache_lock:
            total_cache = len(conn._cache)
        return jsonify({
            "user_id": user_id,
            "connected_monitor": conn._monitor_connected,
            "connected_search": conn._search_connected,
            "total_cache": total_cache,
            "uids_usados": len(conn._uids_usados),
        })
    return jsonify({"error": "Cache não encontrado para este usuário"})


@app.route("/admin/limite_salas/<int:user_id>", methods=["POST"])
@admin_required
def limite_salas(user_id: int):
    from models import BotStatus
    # Default to 10 if not provided (instead of 0 which would block all rooms)
    limite = int(request.form.get("limite", _DEFAULT_LIMITE_SALAS))
    s = BotStatus.query.filter_by(user_id=user_id).first() or BotStatus(user_id=user_id)
    s.limite_salas = limite
    db.session.add(s)
    db.session.commit()
    user = db.session.get(User, user_id)
    username = user.username if user else f"ID {user_id}"
    session["msg"] = f"Limite de {username} atualizado para {limite} salas!"
    session["msg_tipo"] = "success"
    return redirect(url_for("admin") + "#salas")


@app.route("/admin/resetar_salas/<int:user_id>")
@admin_required
def resetar_salas(user_id: int):
    from models import BotStatus
    s = BotStatus.query.filter_by(user_id=user_id).first()
    if s:
        s.salas_usadas = 0
        db.session.commit()
        user = db.session.get(User, user_id)
        username = user.username if user else f"ID {user_id}"
        session["msg"] = f"Salas de {username} resetadas com sucesso!"
        session["msg_tipo"] = "success"
    return redirect(url_for("admin") + "#salas")


@app.route("/admin/deletar_key", methods=["POST"])
@admin_required
def deletar_key():
    # Pode vir como string vazia/não-numérica dependendo do form/JS.
    key_id_raw = request.form.get("key_id", "").strip()
    if not key_id_raw:
        session["msg"] = "Nenhuma key informada."
        session["msg_tipo"] = "danger"
        return redirect(url_for("admin") + "#keys")

    try:
        key_id = int(key_id_raw)
    except (TypeError, ValueError):
        session["msg"] = "key_id inválido."
        session["msg_tipo"] = "danger"
        return redirect(url_for("admin") + "#keys")

    key = db.session.get(LicenseKey, key_id)
    if not key:
        session["msg"] = "Key não encontrada."
        session["msg_tipo"] = "warning"
        return redirect(url_for("admin") + "#keys")

    try:
        # Desvincula usuários antes de deletar
        User.query.filter_by(key_id=key.id).update({"key_id": None})
        db.session.delete(key)
        db.session.commit()
        session["msg"] = "Key deletada com sucesso!"
        session["msg_tipo"] = "success"
    except Exception:
        db.session.rollback()
        session["msg"] = "Erro ao deletar key (verifique dependências no banco)."
        session["msg_tipo"] = "danger"

    return redirect(url_for("admin") + "#keys")



@app.route("/admin/_migrate_db")
@admin_required
def migrate_db():
    _run_migrations()
    return jsonify({"status": "ok", "msg": "migrations applied"})


@app.route("/admin/limpar_seriais")
@admin_required
def limpar_seriais():
    session.pop("seriais", None)
    return redirect(url_for("admin") + "#salas")


@app.route("/admin/gerar_key", methods=["POST"])
@admin_required
def gerar_key():
    tipo = request.form.get("tipo", "mensal")
    key = LicenseKey.gerar(tipo)
    db.session.add(key)
    db.session.commit()
    session["msg"] = f"Key {key.key} gerada com sucesso!"
    session["msg_tipo"] = "success"
    return redirect(url_for("admin") + "#keys")


@app.route("/admin/deletar_usuario", methods=["POST"])
@admin_required
def deletar_usuario_post():
    user_id = request.form.get("user_id")
    if user_id:
        user_id = int(user_id)
        p = processos.get(user_id)
        if p and p.is_alive():
            p.terminate()
            p.join(timeout=3)
        processos.pop(user_id, None)
        user = db.session.get(User, user_id)
        if user:
            username = user.username
            if user.config:
                db.session.delete(user.config)
            db.session.delete(user)
            db.session.commit()
            session["msg"] = f"Usuário {username} deletado com sucesso!"
            session["msg_tipo"] = "success"
    return redirect(url_for("admin") + "#usuarios")


@app.route("/admin/stop_bot/<int:user_id>")
@admin_required
def stop_bot(user_id: int):
    p = processos.get(user_id)
    if p and p.is_alive():
        p.terminate()
        p.join(timeout=3)
    processos.pop(user_id, None)
    return redirect(url_for("admin"))


@app.route("/admin/start_bot/<int:user_id>")
@admin_required
def start_bot(user_id: int):
    if user_id in processos and processos[user_id].is_alive():
        return redirect(url_for("admin"))
    user = db.session.get(User, user_id)
    if not user or not user.config:
        return render_template("admin.html",
            users=User.query.filter_by(is_admin=False).all(),
            keys=LicenseKey.query.order_by(LicenseKey.criado_em.desc()).all(),
            status={uid: p.is_alive() for uid, p in processos.items()},
            msg="SELFBOT NÃO CONFIGURADO", msg_tipo="danger")
    log_path = os.path.join(os.path.dirname(__file__), "logs", f"user_{user_id}.log")
    with open(log_path, "w", encoding="utf-8"):
        pass
    p = multiprocessing.Process(target=run_selfbot, args=(_config_dict(user.config), user_id), daemon=True)
    p.start()
    processos[user_id] = p
    return redirect(url_for("admin"))


@app.route("/admin/testar_imap/<int:user_id>")
@admin_required
def testar_imap(user_id: int):
    user = db.session.get(User, user_id)
    if not user or not user.config:
        return jsonify({"erro": "Usuário sem configuração"})
    cfg = user.config
    try:
        from imap_tools import MailBox
        mb = MailBox(cfg.imap_server.strip(), timeout=15)
        mb.login(cfg.email_user.strip(), cfg.email_pass.strip(), initial_folder="INBOX")
        mb.logout()
        return jsonify({"status": "ok", "email": cfg.email_user, "servidor": cfg.imap_server})
    except Exception as e:
        return jsonify({"status": "erro", "email": cfg.email_user, "servidor": cfg.imap_server, "erro": str(e)})


@app.route("/admin/limpar_cache_imap/<int:user_id>")
@admin_required
def limpar_cache_imap(user_id: int):
    import glob
    cache_dir = os.path.join(os.path.dirname(__file__), "imap_cache")
    for f in glob.glob(os.path.join(cache_dir, f"user_{user_id}.json")):
        os.remove(f)
    from imap_optimizer import imap_manager
    imap_manager.stop_cache(user_id)
    session["msg"] = f"Cache IMAP do user {user_id} limpo!"
    session["msg_tipo"] = "success"
    return redirect(url_for("admin"))


@app.route("/admin/restart_bot/<int:user_id>")
@admin_required
def restart_bot(user_id: int):
    p = processos.get(user_id)
    if p and p.is_alive():
        p.terminate()
        p.join(timeout=3)
    processos.pop(user_id, None)
    log_path = os.path.join(os.path.dirname(__file__), "logs", f"user_{user_id}.log")
    with open(log_path, "w", encoding="utf-8"):
        pass
    user = db.session.get(User, user_id)
    if not user or not user.config:
        return render_template("admin.html",
            users=User.query.filter_by(is_admin=False).all(),
            keys=LicenseKey.query.order_by(LicenseKey.criado_em.desc()).all(),
            status={uid: p.is_alive() for uid, p in processos.items()},
            msg="SELFBOT NÃO CONFIGURADO", msg_tipo="danger")
    np = multiprocessing.Process(target=run_selfbot, args=(_config_dict(user.config), user_id), daemon=True)
    np.start()
    processos[user_id] = np
    return redirect(url_for("admin"))


if __name__ == "__main__":
    with app.app_context():
        if not User.query.filter_by(is_admin=True).first():
            u = User(
                username="DiasDev",
                password=generate_password_hash("DiasDev0"),
                is_admin=True,
            )
            db.session.add(u)
            db.session.commit()
            print("Admin criado: DiasDev / DiasDev0")

    # SquareCloud pode injetar PORT/HOST; use-os quando existirem.
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "80"))
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True, processes=1)

