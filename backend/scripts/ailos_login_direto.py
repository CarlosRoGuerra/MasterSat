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
import re
import sys
import time

# Garante que 'app' é encontrado quando executado de dentro de backend/
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

os.environ.setdefault('DATABASE_URL', 'postgresql+psycopg://postgres:postgres@localhost:5432/rastreamento')

from app.db.session import SessionLocal  # noqa: E402
from app.models.ailos_integration import AilosIntegration  # noqa: E402
from app.services import ailos_client  # noqa: E402
from app.services.ailos_client import AilosApiError, AilosError  # noqa: E402


def _diagnosticar_html(html: str) -> None:
    """Extrai a mensagem de erro de login (LG003/LG006/BLOQUEADA) do HTML da Ailos."""
    # Salva o corpo completo para inspeção manual, se necessário.
    caminho = '/tmp/ailos_login_resposta.html'
    try:
        with open(caminho, 'w', encoding='utf-8') as fh:
            fh.write(html)
        print(f'  (HTML completo salvo em {caminho})')
    except OSError:
        pass

    # Procura mensagens de erro conhecidas (validation summary do ASP.NET).
    achados: list[str] = []
    for padrao in (
        r'LG0\d{2}[^<\n]*',                 # LG003, LG006, ...
        r'[^<>\n]*BLOQUEAD[AO][^<\n]*',     # conta bloqueada
        r'[^<>\n]*[Cc]ooperado n[ãa]o possui[^<\n]*',
        r'[^<>\n]*[Cc]redencial[^<\n]*inv[áa]lid[^<\n]*',
        r'[^<>\n]*[Dd]esenvolvedor[^<\n]*UUID[^<\n]*',
        r'[^<>\n]*callback[^<\n]*inv[áa]lid[^<\n]*',
    ):
        for m in re.findall(padrao, html):
            texto = m.strip()
            if texto and texto not in achados:
                achados.append(texto)

    if achados:
        print('\n  🔎 ERRO retornado pela Ailos:')
        for a in achados:
            print(f'     → {a}')
    else:
        print('\n  🔎 Nenhuma mensagem de erro LG00x encontrada no HTML.')
        print('     (pode ser que o POST não foi processado como login — veja o arquivo salvo)')


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
        if resultado['json'] is not None:
            print(f'  corpo (JSON): {resultado["json"]}\n')
        else:
            html = resultado['text'] or ''
            if '<html' in html.lower():
                print('  corpo: página HTML de login (login NÃO concluído)')
                _diagnosticar_html(html)
            else:
                print(f'  corpo: {html}')
        print()

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
