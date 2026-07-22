"""
Lista os clientes cujo cadastro impede a emissão de NFS-e pelo Emissor Nacional.

Roda sem certificado digital e sem acessar a Sefin: usa a mesma validação que a
emissão usa, então o que passar aqui passa lá (no que diz respeito ao cadastro).

Uso, dentro do container:
    docker compose exec backend python scripts/nfse_checar_cadastros.py
    docker compose exec backend python scripts/nfse_checar_cadastros.py --csv > pendencias.csv
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal          # noqa: E402
from app.models.client import Client             # noqa: E402
from app.services.nfse_nacional import (         # noqa: E402
    NfseError,
    validar_cadastro_tomador,
)

CSV = '--csv' in sys.argv


def main() -> int:
    db = SessionLocal()
    try:
        clientes = (
            db.query(Client)
            .filter(Client.is_deleted.is_(False))
            .order_by(Client.name)
            .all()
        )
    finally:
        db.close()

    pendentes: list[tuple[Client, str]] = []
    ignorados = 0
    for cliente in clientes:
        if (getattr(cliente, 'issue_invoice', None) or 'sim') == 'nao':
            ignorados += 1
            continue
        try:
            validar_cadastro_tomador(cliente)
        except NfseError as exc:
            motivo = str(exc).split('Preencha: ', 1)[-1].split('.')[0]
            pendentes.append((cliente, motivo))

    if CSV:
        print('id,nome,cpf_cnpj,faltando')
        for cliente, motivo in pendentes:
            nome = (cliente.name or '').replace(',', ' ')
            print(f'{cliente.id},{nome},{cliente.cpf_cnpj or ""},"{motivo}"')
        return 0

    print(f'Clientes ativos analisados : {len(clientes) - ignorados}')
    print(f'Marcados p/ não emitir NF  : {ignorados}')
    print(f'Com cadastro incompleto    : {len(pendentes)}')
    if not pendentes:
        print('\nTudo certo — nenhum cadastro trava a emissão.')
        return 0

    print('\n{:<6} {:<40} {}'.format('ID', 'CLIENTE', 'FALTANDO'))
    print('-' * 100)
    for cliente, motivo in pendentes:
        print('{:<6} {:<40} {}'.format(cliente.id, (cliente.name or '')[:39], motivo))
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
