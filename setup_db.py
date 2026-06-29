"""
Roda uma vez para criar todas as tabelas no banco externo.
Uso: python setup_db.py
"""
import os
from dotenv import load_dotenv
load_dotenv()

from app import app, db
from models import User
from werkzeug.security import generate_password_hash

with app.app_context():
    db.create_all()
    print(f"[OK] Tabelas criadas em: {app.config['SQLALCHEMY_DATABASE_URI'][:40]}...")

    if not User.query.filter_by(is_admin=True).first():
        u = User(username="DiasDev", password=generate_password_hash("DiasDev0"), is_admin=True)
        db.session.add(u)
        db.session.commit()
        print("[OK] Admin criado: DiasDev / DiasDev0")
    else:
        print("[OK] Admin já existe.")
