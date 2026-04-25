from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
import secrets

db = SQLAlchemy()


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    key_id = db.Column(db.Integer, db.ForeignKey("license_key.id"), nullable=True)
    config = db.relationship("BotConfig", backref="user", uselist=False)
    bot_status = db.relationship("BotStatus", backref="user", uselist=False)


class LicenseKey(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(8), unique=True, default=lambda: ''.join(secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(8)))
    tipo = db.Column(db.String(20), nullable=False)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    expira_em = db.Column(db.DateTime, nullable=True)
    usado = db.Column(db.Boolean, default=False)
    users = db.relationship("User", backref="license", foreign_keys=[User.key_id])

    @staticmethod
    def gerar(tipo: str):
        expiracoes = {"semanal": timedelta(days=7), "mensal": timedelta(days=30)}
        expira = datetime.utcnow() + expiracoes[tipo] if tipo in expiracoes else None
        k = LicenseKey(tipo=tipo, expira_em=expira)
        db.session.add(k)
        db.session.commit()
        return k

    def valida(self):
        if self.expira_em is None:
            return True
        return datetime.utcnow() < self.expira_em


class BotConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True)
    discord_token = db.Column(db.String(200), nullable=False)
    server_id = db.Column(db.String(30), nullable=False)
    categoria_id = db.Column(db.String(30), nullable=False)
    email_user = db.Column(db.String(120), nullable=False)
    email_pass = db.Column(db.String(200), nullable=False)
    imap_server = db.Column(db.String(120), nullable=False)
    mensagem_entrada = db.Column(db.Text, default="👋 Olá! Use `pg Nome Sobrenome` para verificar seu pagamento.")
    imagem_entrada = db.Column(db.String(500), nullable=True)
    prefixo_sala = db.Column(db.String(20), nullable=True)  # ex: !sala — se None envia só id+senha
    modo_sala_id = db.Column(db.String(30), nullable=True)  # salaid do modo escolhido


class BotStatus(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True)
    ativo = db.Column(db.Boolean, default=False)
    salas_usadas = db.Column(db.Integer, default=0)
    limite_salas = db.Column(db.Integer, default=10)
