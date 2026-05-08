import os
import time
import threading
from flask import Flask, render_template, request, redirect, url_for, session, Response, jsonify, make_response
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf.csrf import CSRFProtect
from models import db, User, LicenseKey, BotConfig, BotStatus
from bot_logic import run_selfbot, parar_selfbot

app = Flask(__name__)
csrf = CSRFProtect(app)
app.secret_key = os.getenv("SECRET_KEY", os.urandom(24).hex())
_db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selfbot.db")
_database_url = os.getenv("DATABASE_URL", f"sqlite:///{_db_path}")
if _database_url.startswith("postgres://"):
    _database_url = _database_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = _database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db.init_app(app)

processos: dict[int, threading.Thread] = {}
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")


def _config_dict(cfg: BotConfig) -> dict:
    return {
        "discord_token": cfg.discord_token,
        "server_id": cfg.server_id,
        "categoria_id": cfg.categoria_id,
        "email_user": cfg.email_user,
        "email_pass": cfg.email_pass,
        "imap_server": cfg.imap_server,
        "mensagem_entrada": cfg.mensagem_entrada,
        "prefixo_sala": cfg.prefixo_sala or "",
        "imagem_entrada": cfg.imagem_entrada or "",
    }


def _set_status(user_id: int, ativo: bool):
    s = BotStatus.query.filter_by(user_id=user_id).first() or BotStatus(user_id=user_id)
    s.ativo = ativo
    db.session.add(s)
    db.session.commit()


def _iniciar_processo(user_id: int, cfg):
    # Validar user_id para prevenir path traversal
    if not isinstance(user_id, int) or user_id <= 0:
        raise ValueError("Invalid user_id")
    log_path = os.path.join(LOG_DIR, f"user_{user_id}.log")
    with open(log_path, "w", encoding="utf-8"):
        pass
    t = threading.Thread(target=run_selfbot, args=(_config_dict(cfg), user_id), daemon=True)
    t.start()
    processos[user_id] = t


def _get_bot_status(user_id: int) -> BotStatus:
    s = BotStatus.query.filter_by(user_id=user_id).first()
    if not s:
        s = BotStatus(user_id=user_id, ativo=False, salas_usadas=0, limite_salas=0)
        db.session.add(s)
        db.session.commit()
    return s


def _render_cliente(user, cfg, ativo, msg=None, msg_tipo=None):
    from datetime import datetime, timezone
    dias_restantes = None
    if user and user.license and user.license.expira_em:
        delta = user.license.expira_em - datetime.now(timezone.utc)
        dias_restantes = max(delta.days, 0)
    bot_status = _get_bot_status(user.id) if user else None
    return render_template("cliente.html", user=user, cfg=cfg, ativo=ativo,
                           dias_restantes=dias_restantes, bot_status=bot_status,
                           msg=msg, msg_tipo=msg_tipo)


def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/", methods=["GET", "POST"])
def login():
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
                session["user_id"] = user.id
                resp = make_response(redirect(url_for("cliente")))
                if lembrar:
                    resp.set_cookie("cliente_user", username, max_age=30*24*3600, secure=True, httponly=True)
                    resp.set_cookie("cliente_key", key_str, max_age=30*24*3600, secure=True, httponly=True)
                else:
                    resp.delete_cookie("cliente_user")
                    resp.delete_cookie("cliente_key")
                return resp
        else:
            erro = "Usuario ou key incorretos."
    return render_template("login_cliente.html", erro=erro, saved_user=saved_user, saved_key=saved_key)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/cliente")
@login_required
def cliente():
    user = db.session.get(User, session["user_id"])
    cfg = user.config
    ativo = user.id in processos and processos[user.id].is_alive()
    return _render_cliente(user, cfg, ativo)


@app.route("/salvar_config", methods=["POST"])
@login_required
def salvar_config():
    user = db.session.get(User, session["user_id"])
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
    db.session.add(cfg)
    db.session.commit()
    return redirect(url_for("cliente"))


@app.route("/start_bot/<int:user_id>")
@login_required
def start_bot(user_id: int):
    if session["user_id"] != user_id:
        return jsonify({"erro": "Sem permissao"}), 403
    if user_id in processos and processos[user_id].is_alive():
        return redirect(url_for("cliente"))
    user = db.session.get(User, user_id)
    if not user or not user.config:
        return _render_cliente(user, None, False, "SELFBOT NAO CONFIGURADO", "danger")
    _iniciar_processo(user_id, user.config)
    _set_status(user_id, True)
    return redirect(url_for("cliente"))


@app.route("/stop_bot/<int:user_id>")
@login_required
def stop_bot(user_id: int):
    if session["user_id"] != user_id:
        return jsonify({"erro": "Sem permissao"}), 403
    parar_selfbot(user_id)
    processos.pop(user_id, None)
    _set_status(user_id, False)
    from datetime import datetime, timezone
    log_path = os.path.join(LOG_DIR, f"user_{user_id}.log")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] Selfbot desligado pelo painel.\n")
        f.flush()
    return redirect(url_for("cliente"))


@app.route("/restart_bot/<int:user_id>")
@login_required
def restart_bot(user_id: int):
    if session["user_id"] != user_id:
        return jsonify({"erro": "Sem permissao"}), 403
    parar_selfbot(user_id)
    processos.pop(user_id, None)
    user = db.session.get(User, user_id)
    if not user or not user.config:
        return _render_cliente(user, None, False, "SELFBOT NAO CONFIGURADO", "danger")
    _iniciar_processo(user_id, user.config)
    _set_status(user_id, True)
    return redirect(url_for("cliente"))


@app.route("/api_saldo")
@login_required
def api_saldo():
    bs = _get_bot_status(session["user_id"])
    disponiveis = max(bs.limite_salas - bs.salas_usadas, 0)
    try:
        import requests as req
        r = req.get("https://salasff.com/modos?key=266vq0badxid7jpcf96t", timeout=5)
        total_api = r.json().get("salas", 0)
    except Exception:
        total_api = "?"
    return jsonify({"status": "ok", "user_disponiveis": disponiveis, "user_limite": bs.limite_salas, "salas": total_api})


@app.route("/api_modos")
@login_required
def api_modos():
    API_KEY = "266vq0badxid7jpcf96t"
    try:
        import requests as req
        r = req.get(f"https://salasff.com/modos?key={API_KEY}", timeout=10)
        data = r.json()
        modos = []
        if isinstance(data, list):
            modos = [{"salaid": m.get("salaid", m.get("id")), "nome": m.get("nome", m.get("name", "Modo Padrão"))} for m in data]
        elif isinstance(data, dict) and "modos" in data:
            modos = [{"salaid": m.get("salaid"), "nome": m.get("nome")} for m in data["modos"]]
        return jsonify({"status": "ok", "modos": modos[:20]})  # Limite 20 modos
    except Exception:
        return jsonify({"status": "error", "modos": []})


@app.route("/stream_logs/<int:user_id>")
@login_required
def stream_logs(user_id: int):
    if session["user_id"] != user_id:
        return jsonify({"erro": "Sem permissao"}), 403

    # Validar user_id para prevenir path traversal
    if not isinstance(user_id, int) or user_id <= 0:
        raise ValueError("Invalid user_id")
    log_path = os.path.join(LOG_DIR, f"user_{user_id}.log")

    def generate():
        with open(log_path, "a", encoding="utf-8"):
            pass
        with open(log_path, "r", encoding="utf-8") as f:
            conteudo = f.read()
            for linha in conteudo.splitlines():
                if linha.strip():
                    yield f"data: {linha}\n\n"
            while True:
                linha = f.readline()
                if linha:
                    yield f"data: {linha.rstrip()}\n\n"
                else:
                    time.sleep(0.3)

    return Response(generate(), mimetype="text/event-stream")


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        import sqlite3 as _sqlite3
        with _sqlite3.connect(_db_path) as con:
            cols = [r[1] for r in con.execute("PRAGMA table_info(bot_status)").fetchall()]
            if "salas_usadas" not in cols:
                con.execute("ALTER TABLE bot_status ADD COLUMN salas_usadas INTEGER DEFAULT 0")
            if "limite_salas" not in cols:
                con.execute("ALTER TABLE bot_status ADD COLUMN limite_salas INTEGER DEFAULT 10")
            cols_cfg = [r[1] for r in con.execute("PRAGMA table_info(bot_config)").fetchall()]
            if "prefixo_sala" not in cols_cfg:
                con.execute("ALTER TABLE bot_config ADD COLUMN prefixo_sala VARCHAR(20)")
            if "imagem_entrada" not in cols_cfg:
                con.execute("ALTER TABLE bot_config ADD COLUMN imagem_entrada VARCHAR(500)")
            if "modo_sala_id" not in cols_cfg:
                con.execute("ALTER TABLE bot_config ADD COLUMN modo_sala_id VARCHAR(30) DEFAULT '826526295161871655'")
            con.commit()
        for status in BotStatus.query.filter_by(ativo=True).all():
            user = db.session.get(User, status.user_id)
            if user and user.config and user.license and user.license.valida():
                _iniciar_processo(user.id, user.config)
    debug_mode = os.getenv("DEBUG", "False").lower() == "true"
    host = os.getenv("HOST", "127.0.0.1")
    app.run(host=host, port=5001, debug=debug_mode, use_reloader=False)
