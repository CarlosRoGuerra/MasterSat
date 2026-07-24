"""
Emite uma NFS-e de TESTE pelo Emissor Nacional (produção restrita, tpAmb=2,
SEM valor fiscal). Serve para validar a integração de ponta a ponta a partir do
próprio ambiente (container/local), usando o certificado configurado no .env.

Uso (dentro do container):
    docker compose exec backend python scripts/nfse_emitir_teste.py
    docker compose exec backend python scripts/nfse_emitir_teste.py <billing_id>

Sem billing_id, usa um tomador de teste embutido. Com billing_id, emite a NFS-e
de uma cobrança real do banco (continua em produção restrita — não fiscal).

⚠ Trava de segurança: recusa rodar se NFSE_NAC_AMBIENTE=producao (nota real),
a menos que se passe --producao explicitamente.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings                      # noqa: E402
from app.services import nfse_nacional as nf              # noqa: E402


def _tomador_de_teste():
    return SimpleNamespace(
        name='CLIENTE TESTE PRODUCAO RESTRITA', cpf_cnpj='00000000000191',
        type='pj', email='teste@mastersat.com.br', phone='4730000000',
        zip_code='89201100', address_line='RUA DO PRINCIPE', address_number='100',
        address_complement='', neighborhood='CENTRO', city='JOINVILLE', state='SC',
        city_ibge_code='4209102', optante_simples=None, iss_retido=None,
        issue_invoice=None,
    )


def main() -> int:
    args = [a for a in sys.argv[1:] if a != '--producao']
    permitir_producao = '--producao' in sys.argv

    if settings.nfse_nac_ambiente == 'producao' and not permitir_producao:
        print('ABORTADO: NFSE_NAC_AMBIENTE=producao emite nota REAL. '
              'Use producao_restrita, ou passe --producao se for intencional.')
        return 2

    print('Ambiente :', settings.nfse_nac_ambiente, '->', nf._ambiente()[1])
    print('cTribNac :', settings.nfse_nac_cod_trib_nacional, '| série', settings.nfse_nac_serie)
    print('Cert     :', settings.nfse_cert_path or '(NENHUM — vai falhar)')

    if args:
        # Emite uma cobrança real do banco pelo fluxo completo (idempotente).
        from app.db.session import SessionLocal
        from app.models.billing import Billing
        from app.models.client import Client
        db = SessionLocal()
        try:
            billing = db.get(Billing, int(args[0]))
            if billing is None:
                print(f'Cobrança #{args[0]} não encontrada.')
                return 1
            client = db.get(Client, billing.client_id)
            print(f'Emitindo NFS-e da cobrança #{billing.id} ({client.name})...')
            nota = nf.emitir_nfse(db, billing, client)
            print('status:', nota.status, '| nº NFS-e:', nota.numero_nfse,
                  '| chave:', nota.chave_acesso)
            if nota.erro_mensagem:
                print('erro:', nota.erro_mensagem)
            return 0 if nota.status == 'emitida' else 1
        finally:
            db.close()

    # Sem billing_id: monta/assina/envia um tomador de teste (sem tocar no banco).
    import base64
    import gzip

    from lxml import etree

    billing = SimpleNamespace(id=1, title='MENSALIDADE MONITORAMENTO', amount=Decimal('10.00'))
    dps = nf.montar_dps(billing, _tomador_de_teste(), '1')
    nf.validar_dps(dps)
    xml_bytes = nf._serializar_dps(nf.assinar_dps(dps))
    resp = nf._post('/nfse', {'dpsXmlGZipB64': nf._compactar(xml_bytes)})

    print('\nHTTP', resp.status_code)
    corpo = resp.json()
    if resp.status_code == 201:
        chave = corpo.get('chaveAcesso')
        print('>>> NFS-e EMITIDA <<<')
        print('chave de acesso:', chave)
        print('consulta pública:',
              f'https://www.producaorestrita.nfse.gov.br/consultapublica?chave={chave}')
        b64 = corpo.get('nfseXmlGZipB64')
        if b64:
            xml_nfse = gzip.decompress(base64.b64decode(b64)).decode('utf-8')
            raiz = etree.fromstring(xml_nfse.encode())
            num = raiz.find('.//{http://www.sped.fazenda.gov.br/nfse}nNFSe')
            print('número NFS-e   :', num.text if num is not None else '?')
        return 0

    print('Rejeição:', corpo.get('erros') or corpo)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
