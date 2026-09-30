"""
Verifica um banco RESTAURADO: segredos Fernet, certificado A1, documentos e
saldo financeiro. Só leitura — mesmo assim, aponte para o banco de ensaio
criado por `backup/restore.sh banco`, nunca para produção.

Uso (a partir de backend/, com a chave Fernet do cofre no ambiente):
  AILOS_TOKEN_ENCRYPTION_KEY=... python scripts/verificar_restauracao.py \\
      --database-url postgresql+psycopg://postgres:SENHA@db:5432/rastreamento_restore_X \\
      --objetos-dir /restauracao/objetos        # ou --minio (usa MINIO_* do ambiente)
      [--json resultado.json]

Saída 0 = aprovado; 1 = reprovado (lista de erros no final).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, _BACKEND_DIR)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, OSError):
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--database-url', required=True, help='URL SQLAlchemy do banco restaurado')
    origem = parser.add_mutually_exclusive_group()
    origem.add_argument('--objetos-dir', help='diretório com a cópia restaurada dos objetos')
    origem.add_argument('--minio', action='store_true', help='consultar o MinIO configurado em MINIO_*')
    parser.add_argument('--json', help='grava o resultado completo neste arquivo')
    args = parser.parse_args(argv)

    from sqlalchemy import create_engine

    from app.services.verificacao_restauracao import tamanho_em_diretorio, tamanho_no_minio, verificar

    tamanho = None
    if args.objetos_dir:
        tamanho = tamanho_em_diretorio(args.objetos_dir)
    elif args.minio:
        tamanho = tamanho_no_minio()

    engine = create_engine(args.database_url)
    try:
        res = verificar(engine, tamanho)
    finally:
        engine.dispose()

    dados = res.as_dict()
    if args.json:
        with open(args.json, 'w', encoding='utf-8') as fh:
            json.dump(dados, fh, ensure_ascii=False, indent=2)

    print(f"Alembic: {dados['alembic_revision']}")
    print(f"Chave Fernet usada: {dados['fernet_key_fingerprint'] or 'nenhuma'} (compare com fernet_key_fingerprint do manifest)")
    for rotulo, cont in res.segredos.items():
        print(f'Segredos {rotulo}: {cont["decifrados"]}/{cont["total"]} decifrados')
    for cert in res.certificados:
        print(f'Certificado NFS-e #{cert["id"]}: abre={cert.get("abre")} válido até {cert.get("valido_ate")}')
    if res.objetos:
        print(f'Objetos: {res.objetos}')
    for chave, valores in res.financeiro.items():
        print(f'Financeiro {chave}: {valores}')
    for aviso in res.avisos:
        print(f'AVISO: {aviso}')
    for erro in res.erros:
        print(f'ERRO: {erro}')
    print('APROVADO' if res.aprovado else 'REPROVADO')
    return 0 if res.aprovado else 1


if __name__ == '__main__':
    raise SystemExit(main())
