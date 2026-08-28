"""
Indicador "contrato armazenado" no cliente.

A empresa colhe a assinatura no papel e anexa o scan (categoria 'contrato').
A lista e o detalhe do cliente mostram quem já tem o contrato guardado e quem
ainda falta.
"""
from __future__ import annotations

from app.models.client import Client
from app.models.document import Document
from app.models.enums import ClientStatus


def _cliente(db, nome='ACME LTDA') -> Client:
    c = Client(name=nome, cpf_cnpj=f'{abs(hash(nome)) % 10**11:011d}', type='pj',
               status=ClientStatus.ACTIVE)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _doc(db, client_id, *, category='contrato', active=True):
    d = Document(
        file_name='contrato.pdf', object_key=f'clients/{client_id}/{category}/{active}-x',
        content_type='application/pdf', size_bytes=10,
        reference_type='client', reference_id=client_id, category=category, active=active,
    )
    db.add(d)
    db.commit()
    return d


def test_sem_documento_o_indicador_e_falso(http, db):
    c = _cliente(db)
    assert http.get(f'/api/v1/clients/{c.id}').json()['contrato_armazenado'] is False


def test_com_contrato_anexado_o_indicador_e_verdadeiro(http, db):
    c = _cliente(db)
    _doc(db, c.id, category='contrato')
    assert http.get(f'/api/v1/clients/{c.id}').json()['contrato_armazenado'] is True


def test_outro_tipo_de_documento_nao_conta(http, db):
    """CNH ou comprovante de endereço não são o contrato."""
    c = _cliente(db)
    _doc(db, c.id, category='cnh')
    assert http.get(f'/api/v1/clients/{c.id}').json()['contrato_armazenado'] is False


def test_contrato_inativo_nao_conta(http, db):
    """Documento removido (soft delete) não conta como armazenado."""
    c = _cliente(db)
    _doc(db, c.id, category='contrato', active=False)
    assert http.get(f'/api/v1/clients/{c.id}').json()['contrato_armazenado'] is False


def test_contrato_de_outro_cliente_nao_vaza(http, db):
    a, b = _cliente(db, 'A LTDA'), _cliente(db, 'B LTDA')
    _doc(db, a.id, category='contrato')
    assert http.get(f'/api/v1/clients/{a.id}').json()['contrato_armazenado'] is True
    assert http.get(f'/api/v1/clients/{b.id}').json()['contrato_armazenado'] is False


def test_a_lista_traz_o_indicador_de_cada_um(http, db):
    a, b = _cliente(db, 'COM CONTRATO'), _cliente(db, 'SEM CONTRATO')
    _doc(db, a.id, category='contrato')
    por_id = {c['id']: c['contrato_armazenado'] for c in http.get('/api/v1/clients/').json()['items']}
    assert por_id[a.id] is True
    assert por_id[b.id] is False
