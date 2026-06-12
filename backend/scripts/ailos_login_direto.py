"""
Autorização headless do cooperado na Ailos (sem navegador), usando o CÓDIGO
numérico da cooperativa.

Útil quando a tela de login (dropdown) não identifica a cooperativa pelo nome
do cooperado, mas você tem o código numérico fornecido pela Ailos
(Login.CodigoCooperativa).

Faz o fluxo completo: obter/id -> POST /login/index (multipart) -> a Ailos
chama o callback que persiste o JWT. Depois confirma o status.

Uso (dentro do container backend):
  python scripts/ailos_login_direto.py --cooperativa <COD> --conta <CONTA> [--senha aaaaa11111@]

Exemplo:
  python scripts/ailos_login_direto.py --cooperativa 1 --conta 99545233
"""
from __future__ import annotations

import argparse
import os
import sys
import time

# Garante que 'app' é encontrado quando executado de dentro de backend/
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

os.environ.setdefault('DATABASE_URL', 'postgresql+psycopg://postgres:postgres@localhost:5432/rastreamento')

from app.db.session import SessionLocal  # noqa: E402
from app.models.ailos_integration import AilosIntegration  # noqa: E402
from app.services import ailos_client  # noqa: E402
from app.services.ailos_client import AilosApiError, AilosError  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description='Autoriza o cooperado Ailos via API (headless, sem navegador)')
    parser.add_argument('--cooperativa', required=True, help='Código numérico da cooperativa (Login.CodigoCooperativa)')
    parser.add_argument('--conta', required=True, help='Número da conta (Login.CodigoConta) — ex.: 99545233')
    parser.add_argument('--senha', default='aaaaa11111@', help='Senha de homologação (default: aaaaa11111@)')
    args = parser.parse_args()

    db = SessionLocal()
    try:
        print(f'Autorizando cooperado — cooperativa={args.cooperativa} conta={args.conta} ...\n')
        try:
            resultado = ailos_client.autorizar_cooperado_directo(db, args.cooperativa, args.conta, args.senha)
        except (AilosError, AilosApiError) as exc:
            print(f'[ERRO] {exc}')
            sys.exit(1)

        print(f'Resposta do login/index: HTTP {resultado["status_code"]}')
        corpo = resultado['json'] if resultado['json'] is not None else resultado['text']
        print(f'  corpo: {corpo}\n')

        print('Aguardando o callback da Ailos persistir o JWT...')
        for i in range(10):
            time.sleep(2)
            db.expire_all()
            integ = db.query(AilosIntegration).order_by(AilosIntegration.id.asc()).first()
            status = integ.status if integ else 'pending'
            print(f'  [{(i + 1) * 2}s] cooperado_status = {status}')
            if status == 'authorized':
                print('\n✅ Cooperado autorizado! Agora rode:')
                print('   python scripts/ailos_homologacao.py')
                return

        print('\n⚠️ Ainda não autorizado após 20s. Analise a resposta do login/index acima.')
        print('   Causas comuns: código da cooperativa ou conta incorretos (erro LG006),')
        print('   ou o callback não foi recebido. Confira AILOS_CALLBACK_URL e o log do backend.')
    finally:
        db.close()


if __name__ == '__main__':
    main()
