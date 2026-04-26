import os
import multiprocessing
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, make_response
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, LicenseKey, BotConfig
from bot_logic import run_selfbot

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "manager_secret_key")
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
_db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selfbot.db")
_database_url = os.getenv("DATABASE_URL", f"sqlite:///{_db_path}")
# Heroku/Railway/SquareCloud retornam postgres://, SQLAlchemy exige postgresql://
if _database_url.startswith("postgres://"):
    _database_url = _database_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = _database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db.init_app(app)

processos: dict[int, multiprocessing.Process] = {}


def _config_dict(cfg: BotConfig) -> dict:
    return {
        "discord_token": cfg.discord_token,
        "server_id": cfg.server_id,
        "categoria_id": cfg.categoria_id,
        "email_user": cfg.email_user,
        "email_pass": cfg.email_pass,
        "imap_server": cfg.imap_server,
        "mensagem_entrada": cfg.mensagem_entrada,
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


@app.route("/cliente/salvar_config", methods=["POST"])
@login_required
def cliente_salvar_config():
    from models import BotConfig
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
    db.session.add(cfg)
    db.session.commit()
    return redirect(url_for("painel_cliente"))


@app.route("/cliente/start_bot/<int:user_id>")
@login_required
def cliente_start_bot(user_id: int):
    if session["cliente_id"] != user_id:
        return jsonify({"erro": "Sem permissao"}), 403
    if user_id in processos and processos[user_id].is_alive():
        return redirect(url_for("painel_cliente"))
    user = db.session.get(User, user_id)
    if not user or not user.config:
        return _render_cliente(user, None, False, "SELFBOT NAO CONFIGURADO", "danger")
    p = multiprocessing.Process(target=run_selfbot, args=(_config_dict(user.config), user_id), daemon=True)
    p.start()
    processos[user_id] = p
    return redirect(url_for("painel_cliente"))


@app.route("/cliente/stop_bot/<int:user_id>")
@login_required
def cliente_stop_bot(user_id: int):
    from bot_logic import parar_selfbot
    if session["cliente_id"] != user_id:
        return jsonify({"erro": "Sem permissao"}), 403
    parar_selfbot(user_id)
    p = processos.pop(user_id, None)
    if p and p.is_alive():
        p.terminate()
        p.join(timeout=3)
    return redirect(url_for("painel_cliente"))


@app.route("/cliente/restart_bot/<int:user_id>")
@login_required
def cliente_restart_bot(user_id: int):
    from bot_logic import parar_selfbot
    if session["cliente_id"] != user_id:
        return jsonify({"erro": "Sem permissao"}), 403
    parar_selfbot(user_id)
    p = processos.pop(user_id, None)
    if p and p.is_alive():
        p.terminate()
        p.join(timeout=3)
    log_path = os.path.join(os.path.dirname(__file__), "logs", f"user_{user_id}.log")
    open(log_path, "w").close()
    user = db.session.get(User, user_id)
    if not user or not user.config:
        return _render_cliente(user, None, False, "SELFBOT NAO CONFIGURADO", "danger")
    p = multiprocessing.Process(target=run_selfbot, args=(_config_dict(user.config), user_id), daemon=True)
    p.start()
    processos[user_id] = p
    return redirect(url_for("painel_cliente"))


@app.route("/cliente/api_saldo")
@login_required
def cliente_api_saldo():
    bs = _get_bot_status_cliente(session["cliente_id"])
    disponiveis = max(bs.limite_salas - bs.salas_usadas, 0)
    try:
        import requests as req
        r = req.get("https://salasff.com/modos?key=266vq0badxid7jpcf96t", timeout=5)
        total_api = r.json().get("salas", 0)
    except Exception:
        total_api = "?"
    return jsonify({"status": "ok", "user_disponiveis": disponiveis, "user_limite": bs.limite_salas, "salas": total_api})


@app.route("/cliente/stream_logs/<int:user_id>")
@login_required
def cliente_stream_logs(user_id: int):
    import time
    from flask import Response
    if session["cliente_id"] != user_id:
        return jsonify({"erro": "Sem permissao"}), 403
    log_path = os.path.join(os.path.dirname(__file__), "logs", f"user_{user_id}.log")

    def generate():
        with open(log_path, "a", encoding="utf-8"):
            pass
        with open(log_path, "r", encoding="utf-8") as f:
            for linha in f.read().splitlines():
                if linha.strip():
                    yield f"data: {linha}\n\n"
            while True:
                linha = f.readline()
                if linha:
                    yield f"data: {linha.rstrip()}\n\n"
                else:
                    time.sleep(0.3)

    return Response(generate(), mimetype="text/event-stream")


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET", "POST"])
def login():
    erro = None
    saved_user = request.cookies.get("admin_user", "")
    saved_pass = ""
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
    return render_template("login_manager.html", erro=erro, saved_user=saved_user, saved_pass=saved_pass)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── Admin ─────────────────────────────────────────────────────────────────────

@app.route("/admin")
@admin_required
def admin():
    from models import BotStatus
    users = User.query.filter_by(is_admin=False).all()
    keys = LicenseKey.query.order_by(LicenseKey.criado_em.desc()).all()
    status = {s.user_id: s.ativo for s in BotStatus.query.all()}
    salas_info = {s.user_id: {"usadas": s.salas_usadas, "limite": s.limite_salas} for s in BotStatus.query.all()}
    seriais = session.pop("seriais", None)
    resultado_resgate = session.pop("resultado_resgate", None)
    saldo_salas = session.pop("saldo_salas", None)
    teste_api = session.pop("teste_api", None)
    keys_criadas = session.pop("keys_criadas", None)
    verificacao_key = session.pop("verificacao_key", None)
    adicao_salas = session.pop("adicao_salas", None)
    return render_template("admin.html", users=users, keys=keys, status=status, salas_info=salas_info, 
                         seriais=seriais, resultado_resgate=resultado_resgate, saldo_salas=saldo_salas, 
                         teste_api=teste_api, keys_criadas=keys_criadas, verificacao_key=verificacao_key, adicao_salas=adicao_salas)


@app.route("/admin/criar_usuario", methods=["POST"])
@admin_required
def criar_usuario():
    username = request.form["username"].strip()
    tipo = request.form.get("tipo", "mensal")
    if User.query.filter_by(username=username).first():
        return render_template("admin.html",
            users=User.query.filter_by(is_admin=False).all(),
            keys=LicenseKey.query.order_by(LicenseKey.criado_em.desc()).all(),
            status={uid: p.is_alive() for uid, p in processos.items()},
            msg="Usuário já existe.", msg_tipo="danger")
    lic = LicenseKey.gerar(tipo)
    lic.usado = True
    user = User(
        username=username,
        password=generate_password_hash(username),
        key_id=lic.id,
    )
    db.session.add(user)
    db.session.commit()
    return redirect(url_for("admin"))


@app.route("/admin/deletar_usuario/<int:user_id>")
@admin_required
def deletar_usuario(user_id: int):
    p = processos.get(user_id)
    if p and p.is_alive():
        p.terminate()
        p.join(timeout=3)
    processos.pop(user_id, None)
    user = User.query.get_or_404(user_id)
    if user.config:
        db.session.delete(user.config)
    db.session.delete(user)
    db.session.commit()
    return redirect(url_for("admin"))


@app.route("/admin/revogar_key/<int:key_id>")
@admin_required
def revogar_key(key_id: int):
    k = LicenseKey.query.get_or_404(key_id)
    # desvincula usuarios antes de deletar
    User.query.filter_by(key_id=k.id).update({"key_id": None})
    db.session.delete(k)
    db.session.commit()
    return redirect(url_for("admin"))


@app.route("/admin/renovar_key/<int:user_id>", methods=["POST"])
@admin_required
def renovar_key(user_id: int):
    from datetime import datetime, timedelta
    user = User.query.get_or_404(user_id)
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

@app.route("/admin/api_saldo_total")
@admin_required
def admin_api_saldo_total():
    from dotenv import load_dotenv
    load_dotenv()
    API_KEY = os.getenv("SALASFF_KEY", "266vq0badxid7jpcf96t")
    import requests as req
    try:
        # Tenta diferentes endpoints
        endpoints = [
            f"https://salasff.com/modos?key={API_KEY}",
            f"https://salasff.com/api/saldo?key={API_KEY}",
            f"https://salasff.com/saldo?key={API_KEY}"
        ]
        
        for url in endpoints:
            try:
                resp = req.get(url, timeout=10, headers={'User-Agent': 'SelfBot-Manager/1.0'})
                if resp.status_code == 200:
                    content_type = resp.headers.get('content-type', '')
                    if 'application/json' in content_type:
                        data = resp.json()
                        if 'salas' in data:
                            return jsonify({"status": "ok", "salas": data['salas']})
                        elif 'saldo' in data:
                            return jsonify({"status": "ok", "salas": data['saldo']})
            except Exception:
                continue
                
        return jsonify({"status": "error", "message": "API não retornou dados válidos"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@app.route("/admin/saldo_salas")
@admin_required
def saldo_salas():
    from dotenv import load_dotenv
    load_dotenv()
    API_KEY = os.getenv("SALASFF_KEY", "266vq0badxid7jpcf96t")
    import requests as req
    try:
        # Tenta diferentes endpoints da API
        endpoints = [
            f"https://salasff.com/api/saldo?key={API_KEY}",
            f"https://salasff.com/saldo?key={API_KEY}",
            f"https://salasff.com/modos?key={API_KEY}"
        ]
        
        saldo = None
        for url in endpoints:
            try:
                resp = req.get(url, timeout=10, headers={'User-Agent': 'SelfBot-Manager/1.0'})
                if resp.status_code == 200:
                    content_type = resp.headers.get('content-type', '')
                    if 'application/json' in content_type:
                        data = resp.json()
                        if 'saldo' in data:
                            saldo = data['saldo']
                            break
                        elif 'salas' in data:
                            saldo = data['salas']
                            break
                    else:
                        # Se não é JSON, verifica se é um número simples
                        texto = resp.text.strip()
                        if texto.isdigit():
                            saldo = int(texto)
                            break
            except Exception:
                continue
        
        if saldo is None:
            saldo = "Erro: API não retornou dados válidos. Verifique a chave da API."
                
    except Exception as e:
        saldo = f"Erro de conexão: {e}"
    
    session["saldo_salas"] = saldo
    return redirect(url_for("admin"))


@app.route("/admin/gerar_seriais", methods=["POST"])
@admin_required
def gerar_seriais():
    import secrets, string
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
    from dotenv import load_dotenv
    load_dotenv()
    API_KEY = os.getenv("SALASFF_KEY", "266vq0badxid7jpcf96t")
    import requests as req
    
    quantidade = int(request.form.get("quantidade", 1))
    tipo = request.form.get("tipo", "premium")  # premium, vip, etc
    duracao = int(request.form.get("duracao", 30))  # dias
    
    try:
        # Tenta diferentes endpoints para criar keys
        endpoints = [
            f"https://salasff.com/api/criar_key?key={API_KEY}&quantidade={quantidade}&tipo={tipo}&duracao={duracao}",
            f"https://salasff.com/criar_key?key={API_KEY}&qty={quantidade}&type={tipo}&days={duracao}",
            f"https://salasff.com/generate?key={API_KEY}&amount={quantidade}&plan={tipo}&duration={duracao}"
        ]
        
        for url in endpoints:
            try:
                resp = req.get(url, timeout=15, headers={'User-Agent': 'SelfBot-Manager/1.0'})
                if resp.status_code == 200:
                    content_type = resp.headers.get('content-type', '')
                    if 'application/json' in content_type:
                        data = resp.json()
                        if 'keys' in data or 'codigos' in data or 'success' in data:
                            session["keys_criadas"] = {
                                "keys": data.get('keys', data.get('codigos', [])),
                                "quantidade": quantidade,
                                "tipo": tipo,
                                "duracao": duracao,
                                "mensagem": data.get('message', data.get('mensagem', 'Keys criadas com sucesso'))
                            }
                            return redirect(url_for("admin"))
            except Exception:
                continue
        
        session["keys_criadas"] = {
            "keys": [],
            "quantidade": 0,
            "tipo": tipo,
            "duracao": duracao,
            "mensagem": "Erro: Não foi possível criar as keys. Verifique a API."
        }
                
    except Exception as e:
        session["keys_criadas"] = {
            "keys": [],
            "quantidade": 0,
            "tipo": tipo,
            "duracao": duracao,
            "mensagem": f"Erro: {e}"
        }
    
    return redirect(url_for("admin"))


@app.route("/admin/verificar_key_sala", methods=["POST"])
@admin_required
def verificar_key_sala():
    from dotenv import load_dotenv
    load_dotenv()
    API_KEY = os.getenv("SALASFF_KEY", "266vq0badxid7jpcf96t")
    import requests as req
    
    key_verificar = request.form.get("key_verificar", "").strip()
    
    if not key_verificar:
        session["verificacao_key"] = {"key": "", "status": "Erro", "info": "Key não fornecida"}
        return redirect(url_for("admin"))
    
    try:
        # Tenta diferentes endpoints para verificar key
        endpoints = [
            f"https://salasff.com/api/verificar?key={API_KEY}&code={key_verificar}",
            f"https://salasff.com/verificar?key={API_KEY}&serial={key_verificar}",
            f"https://salasff.com/check?key={API_KEY}&code={key_verificar}"
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
                            "status": data.get('status', 'Desconhecido'),
                            "info": data.get('info', data.get('message', data.get('mensagem', 'Sem informações'))),
                            "salas_restantes": data.get('salas_restantes', data.get('remaining', 'N/A')),
                            "expira_em": data.get('expira_em', data.get('expires', 'N/A')),
                            "tipo": data.get('tipo', data.get('plan', 'N/A'))
                        }
                        return redirect(url_for("admin"))
            except Exception:
                continue
        
        session["verificacao_key"] = {
            "key": key_verificar,
            "status": "Erro",
            "info": "Não foi possível verificar a key"
        }
                
    except Exception as e:
        session["verificacao_key"] = {
            "key": key_verificar,
            "status": "Erro",
            "info": str(e)
        }
    
    return redirect(url_for("admin"))


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
                
    except Exception as e:
        session["adicao_salas"] = {
            "quantidade": quantidade_adicionar,
            "status": "error",
            "mensagem": f"Erro: {e}",
            "saldo_atual": "N/A"
        }
    
    return redirect(url_for("admin"))


@app.route("/admin/limite_salas/<int:user_id>", methods=["POST"])
@admin_required
def limite_salas(user_id: int):
    from models import BotStatus
    limite = int(request.form.get("limite", 10))
    s = BotStatus.query.filter_by(user_id=user_id).first() or BotStatus(user_id=user_id)
    s.limite_salas = limite
    db.session.add(s)
    db.session.commit()
    return redirect(url_for("admin"))


@app.route("/admin/resetar_salas/<int:user_id>")
@admin_required
def resetar_salas(user_id: int):
    from models import BotStatus
    s = BotStatus.query.filter_by(user_id=user_id).first()
    if s:
        s.salas_usadas = 0
        db.session.commit()
    return redirect(url_for("admin"))


@app.route("/admin/gerar_key", methods=["POST"])
@admin_required
def gerar_key():
    tipo = request.form.get("tipo", "mensal")
    key = LicenseKey.gerar(tipo)
    db.session.add(key)
    db.session.commit()
    return redirect(url_for("admin"))


@app.route("/admin/deletar_key", methods=["POST"])
@admin_required
def deletar_key():
    key_id = request.form.get("key_id")
    if key_id:
        key = LicenseKey.query.get(key_id)
        if key:
            # Desvincula usuários antes de deletar
            User.query.filter_by(key_id=key.id).update({"key_id": None})
            db.session.delete(key)
            db.session.commit()
    return redirect(url_for("admin"))


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
        user = User.query.get(user_id)
        if user:
            if user.config:
                db.session.delete(user.config)
            db.session.delete(user)
            db.session.commit()
    return redirect(url_for("admin"))


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
    p = multiprocessing.Process(target=run_selfbot, args=(_config_dict(user.config), user_id), daemon=True)
    p.start()
    processos[user_id] = p
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
    open(log_path, "w").close()
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
        db.create_all()
        if not User.query.filter_by(is_admin=True).first():
            u = User(
                username="DiasDev",
                password=generate_password_hash("DiasDev0"),
                is_admin=True,
            )
            db.session.add(u)
            db.session.commit()
            print("Admin criado: DiasDev / DiasDev0")
    app.run(host="0.0.0.0", port=80, debug=False, use_reloader=False)
