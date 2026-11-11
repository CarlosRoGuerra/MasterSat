"""Configuração do roteiro existente: faixa do e-mail e isolamento de produção."""
from datetime import date
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys

import pytest

from app.core.config import settings
from app.models.billing import Billing


@pytest.fixture()
def roteiro():
    caminho=Path(__file__).resolve().parents[1]/'scripts/ailos_homologacao.py'
    spec=importlib.util.spec_from_file_location('ailos_homologacao_teste',caminho)
    modulo=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_numero_e_valor_configurados_sem_duplicar_registro(roteiro,db,cliente):
    a=roteiro._upsert_billing(db,cliente,date(2099,1,15),numero=380,valor=Decimal('10.00'))
    b=roteiro._upsert_billing(db,cliente,date(2099,1,15),numero=380,valor=Decimal('10.00'))
    assert a.id==b.id==380 and b.amount==Decimal('10.00')
    assert db.query(Billing).count()==1
    with pytest.raises(ValueError,match='outra numeração'):
        roteiro._upsert_billing(db,cliente,date(2099,1,15),numero=381)


def test_pdf_nao_e_gerado_sem_confirmacao_da_api(roteiro,db,cliente):
    b=roteiro._upsert_billing(db,cliente,date(2099,1,15),numero=380)
    with pytest.raises(ValueError,match='Registro ainda não confirmado'):
        roteiro._gerar_pdf(db,b,cliente)


@pytest.mark.parametrize('argumentos,ambiente,url',[
    ([], 'production', 'https://apiendpoint.ailos.coop.br'),
    ([], 'sandbox', 'https://apiendpoint.ailos.coop.br'),
    (['--primeiro-numero','389'], 'sandbox', 'https://apiendpointhml.ailos.coop.br'),
    (['--valor','0'], 'sandbox', 'https://apiendpointhml.ailos.coop.br'),
])
def test_recusa_producao_numeracao_fora_da_faixa_ou_valor_invalido(roteiro,monkeypatch,argumentos,ambiente,url):
    monkeypatch.setattr(settings,'ailos_env',ambiente)
    monkeypatch.setattr(settings,'ailos_apim_base_url',url)
    monkeypatch.setattr(settings,'ailos_gateway_base_url',url+'/ailos/cobranca/api')
    monkeypatch.setattr(sys,'argv',['ailos_homologacao.py',*argumentos])
    monkeypatch.setattr(roteiro,'SessionLocal',lambda:pytest.fail('Não deve acessar cobranças nesta configuração.'))
    with pytest.raises(SystemExit) as erro:
        roteiro.main()
    assert erro.value.code==2
