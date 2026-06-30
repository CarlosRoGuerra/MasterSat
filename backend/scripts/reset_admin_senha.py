"""
Redefine a senha de um usuário admin diretamente no banco.

Uso (dentro de backend/):
    python scripts/reset_admin_senha.py "NovaSenha@123"
    python scripts/reset_admin_senha.py            # gera uma senha aleatória
    python scripts/reset_admin_senha.py --email outro@admin.com "Senha@123"

Aponta para o banco do DATABASE_URL do .env atual (local OU produção — cuidado).
"""
import argparse
import os
import secrets
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.security import get_password_hash
from app.db.session import SessionLocal
from app.models.user import User


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('senha', nargs='?', default=None, help='Nova senha (se omitida, gera aleatória)')
    parser.add_argument('--email', default='admin@rastreamento.local', help='E-mail do usuário')
    args = parser.parse_args()

    senha = args.senha or secrets.token_urlsafe(12)

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == args.email).first()
        if user is None:
            print(f'[ERRO] Usuario {args.email} nao encontrado neste banco.')
            sys.exit(1)
        user.password_hash = get_password_hash(senha)
        user.active = True
        db.commit()
        print(f'[OK] Senha redefinida.\n  E-mail: {args.email}\n  Senha:  {senha}')
    finally:
        db.close()


if __name__ == '__main__':
    main()
