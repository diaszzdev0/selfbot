import os
import multiprocessing
import secrets
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, make_response
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, LicenseKey, BotConfig
from bot_logic import run_selfbot

app = Flask(__name__)

# Configuração de segurança melhorada
secret_key = os.getenv("FLASK_SECRET_KEY")
if not secret_key:
    secret_key = secrets.token_hex(32)
    print("AVISO: Usando chave secreta temporária. Configure FLASK_SECRET_KEY no .env")

app.secret_key = secret_key
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.config["SESSION_COOKIE_SECURE"] = False
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

_db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selfbot.db")
_database_url = os.getenv("DATABASE_URL", f"sqlite:///{_db_path}")

def _normalize_database_url(val: str) -> str:
    if not val:
        return f"sqlite:///{_db_path}"
    val = str(val).strip()
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
    global _migrations_done
    if _migrations_done:
        return
    try:
        from sqlalchemy import text
        with db.engine.connect() as con:
            cols_result = con.execute(text("PRAGMA table_info(bot_config)"))
            cols = [row[1] for row in cols_result.fetchall()]
            migrations = [
                ("modo_sala_id",          "ALTER TABLE bot_config ADD COLUMN modo_sala_id VARCHAR(30)"),
                ("rate_limit_categorias", "ALTER TABLE bot_config ADD COLUMN rate_limit_categorias TEXT"),
                ("max_threads",           "ALTER TABLE bot_config ADD COLUMN max_threads INTEGER DEFAULT 3"),
                ("imagem_entrada",        "ALTER TABLE bot_config ADD COLUMN imagem_entrada TEXT"),
                ("prefixo_sala",          "ALTER TABLE bot_config ADD COLUMN prefixo_sala VARCHAR(20)"),
            ]
            for col, sql in migrations:
                if col not in cols:
                    con.execute(text(sql))
                    print(f"[migration] added column {col}")
            con.commit()
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
    try:
        _run_migrations()
    except Exception:
        pass

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
    mortos = []
    for user_id, processo in processos.items():
        if not processo.is_alive():
            mortos.append(user_id)
    for user_id in mortos:
        processos.pop(user_id, None)
    return len(mortos)

def _config_dict(cfg: BotConfig) -> dict:
    if not cfg:
        return {}
    required_fields = ["discord_token", "server_id", "categoria_id"]
    for field in required_fields:
        if not getattr(cfg, field, None):
            raise ValueError(f"Campo obrigatório '{field}' não configurado")
    
    def _clean_id(val):
        s = str(val).strip()
        if ":" in s:
            s = s.split(":", 1)[-1].strip()
        return s

    return {
        "discord_token": str(cfg.discord_token).strip(),
        "server_id": _clean_id(cfg.server_id),
        "categoria_id": _clean_id(cfg.categoria_id),
        "email_user": str(cfg.email_user or "").strip(),
        "email_pass": str(cfg.email_pass or "").strip(),
        "imap_server": str(cfg.imap_server or "imap.gmail.com").strip(),
        "mensagem_entrada": str(cfg.mensagem_entrada or "Olá! Use pg Nome Sobrenome para verificar pagamento.").strip(),
        "imagem_entrada": str(cfg.imagem_entrada or "").strip(),
        "prefixo_sala": str(cfg.prefixo_sala or "").strip(),
        "rate_limit_categorias": str(cfg.rate_limit_categorias or "").strip(),
        "max_threads": int(cfg.max_threads or 3),
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

def _get_bot_status_cliente(user_id: int):
    from models import BotStatus
    s = BotStatus.query.filter_by(user_id=user_id).first()
    if not s:
        s = BotStatus(user_id=user_id, ativo=False, salas_usadas=0, limite_salas=0)
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

@app.route("/")
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

@app.route("/admin")
@admin_required
def admin():
    from models import BotStatus
    users = User.query.filter_by(is_admin=False).all()
    keys = LicenseKey.query.order_by(LicenseKey.criado_em.desc()).all()
    status = {s.user_id: s.ativo for s in BotStatus.query.all()}
    salas_info = {s.user_id: {"usadas": s.salas_usadas, "limite": s.limite_salas} for s in BotStatus.query.all()}
    
    msg = session.pop("msg", None)
    msg_tipo = session.pop("msg_tipo", None)
    
    return render_template("admin.html", users=users, keys=keys, status=status, salas_info=salas_info, 
                         msg=msg, msg_tipo=msg_tipo)

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

@app.route("/admin/deletar_key", methods=["POST"])
@admin_required
def deletar_key():
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

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "80"))
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True, processes=1)
