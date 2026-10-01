"""
Recuperação controlada de acesso administrativo (SEC-03).

Uso (dentro de backend/, no container do backend):
    python scripts/reset_admin_senha.py --email admin@empresa.com.br
        → pede a nova senha duas vezes (não aparece na tela nem no histórico)
    python scripts/reset_admin_senha.py --email admin@empresa.com.br --gerar
        → gera uma senha que atende à política e a mostra UMA vez no terminal
    python scripts/reset_admin_senha.py --email admin@empresa.com.br --reativar
        → necessário se a conta estiver excluída ou inativa (decisão explícita)
    python scripts/reset_admin_senha.py --criar --email admin@empresa.com.br
        → instalação nova (banco sem nenhum usuário): cria o admin inicial

Regras:
- só atua em usuário ADMIN; a senha passa pela mesma política da aplicação;
- encerra todas as sessões do usuário (access e refresh) e grava a ação em
  audit_logs, sem a senha;
- nunca escreve a senha em log. Passá-la como argumento posicional ainda
  funciona (compatibilidade), mas ela fica no histórico do shell — evite.

Aponta para o banco do DATABASE_URL do ambiente atual (local OU produção —
cuidado).
"""
import argparse
import getpass
import os
import secrets
import string
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.password_policy import password_problems  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services.admin_bootstrap import RecuperacaoRecusada, recuperar_admin  # noqa: E402


def gerar_senha() -> str:
    alfabeto = string.ascii_letters + string.digits + '@#%&*-_+='
    while True:
        senha = ''.join(secrets.choice(alfabeto) for _ in range(20))
        if not password_problems(senha):
            return senha


def ler_senha() -> str:
    primeira = getpass.getpass('Nova senha: ')
    if getpass.getpass('Repita a senha: ') != primeira:
        print('[ERRO] As senhas não conferem.')
        sys.exit(2)
    return primeira


def main() -> None:
    parser = argparse.ArgumentParser(description='Recuperação controlada de admin.')
    parser.add_argument('senha', nargs='?', default=None,
                        help='(desaconselhado: fica no histórico do shell) nova senha')
    parser.add_argument('--email', default='admin@rastreamento.local', help='E-mail do admin')
    parser.add_argument('--gerar', action='store_true', help='Gera a senha e mostra uma vez')
    parser.add_argument('--reativar', action='store_true', help='Permite reativar conta excluída/inativa')
    parser.add_argument('--criar', action='store_true', help='Cria o admin inicial (só com banco sem usuários)')
    parser.add_argument('--operador', default=os.environ.get('USER') or os.environ.get('USERNAME') or 'cli',
                        help='Quem está executando (vai para a trilha de auditoria)')
    args = parser.parse_args()

    if args.senha:
        print('[AVISO] Senha passada na linha de comando fica no histórico do shell.', file=sys.stderr)
        senha = args.senha
    elif args.gerar:
        senha = gerar_senha()
    else:
        senha = ler_senha()

    db = SessionLocal()
    try:
        resultado = recuperar_admin(
            db, args.email, senha,
            reativar=args.reativar, criar=args.criar, operador=args.operador,
        )
    except RecuperacaoRecusada as exc:
        print(f'[RECUSADO] {exc}')
        sys.exit(1)
    finally:
        db.close()

    acao = 'criado' if resultado.criado else ('reativado e senha redefinida' if resultado.reativado else 'senha redefinida')
    print(f'[OK] {resultado.email}: {acao}. Sessões encerradas: {resultado.sessoes_revogadas}.')
    if args.gerar:
        # stdout do terminal do operador — não é log da aplicação.
        print(f'  Senha gerada (mostrada só agora): {senha}')


if __name__ == '__main__':
    main()
