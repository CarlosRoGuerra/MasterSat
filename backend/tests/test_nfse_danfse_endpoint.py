"""
Rota /nfse/{id}/danfse: oficial-primeiro com fallback automático.

Decisão da revisão de 07/08/2026: o DANFSE oficial (design idêntico ao do
governo) é tentado primeiro; só cai na reprodução local quando o ADN está
instável (502/503/504 ou sem resposta). Erros que a contingência não resolve —
certificado (401/403) e nota inexistente no ADN (404) — continuam estourando
para o operador, porque um PDF local não conserta credencial nem emissão
incompleta.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.billing import Billing
from app.models.enums import BillingStatus
from app.models.nfse_nota import NfseNota
from app.services import nfse_danfse, nfse_nacional
from app.services.nfse_nacional import NfseApiError, NfseError

OFICIAL = b'%PDF-OFICIAL-do-governo'
LOCAL = b'%PDF-LOCAL-reproducao'


def _nota(db, cliente, *, xml: str | None = '<NFSe/>') -> NfseNota:
    b = Billing(
        client_id=cliente.id, amount=Decimal('651.61'),
        due_date=date.today() + timedelta(days=10), status=BillingStatus.PENDING,
        billing_type='recorrente', title='Mensalidade',
    )
    db.add(b)
    db.commit()
    nota = NfseNota(
        billing_id=b.id, status='emitida', numero_nfse='34',
        chave_acesso='42091022214228344000167000000000003426089785058736',
        xml_retorno=xml,
    )
    db.add(nota)
    db.commit()
    return nota


@pytest.fixture()
def nota_emitida(db, cliente) -> NfseNota:
    return _nota(db, cliente)


@pytest.fixture(autouse=True)
def _reproducao_mockada(monkeypatch):
    """A reprodução local é testada em test_nfse_danfse.py; aqui só interessa
    SE e QUANDO ela é chamada, então devolve um marcador reconhecível."""
    monkeypatch.setattr(nfse_danfse, 'gerar_danfse_pdf', lambda *a, **k: LOCAL)


def _oficial(monkeypatch, resultado):
    """Faz baixar_danfse devolver bytes ou levantar a exceção informada."""
    def _fn(_chave):
        if isinstance(resultado, Exception):
            raise resultado
        return resultado
    monkeypatch.setattr(nfse_nacional, 'baixar_danfse', _fn)


# ── Governo no ar: sai o oficial, não a reprodução ──────────────────────────

def test_governo_no_ar_entrega_o_oficial(http, nota_emitida, monkeypatch):
    _oficial(monkeypatch, OFICIAL)
    r = http.get(f'/api/v1/nfse/{nota_emitida.billing_id}/danfse')
    assert r.status_code == 200
    assert r.content == OFICIAL


# ── Instabilidade do ADN: cai na reprodução ─────────────────────────────────

@pytest.mark.parametrize('status', [502, 503, 504])
def test_instabilidade_cai_na_reproducao(http, nota_emitida, monkeypatch, status):
    _oficial(monkeypatch, NfseApiError('ADN fora do ar', status_code=status))
    r = http.get(f'/api/v1/nfse/{nota_emitida.billing_id}/danfse')
    assert r.status_code == 200
    assert r.content == LOCAL


def test_falha_de_transporte_tambem_cai(http, nota_emitida, monkeypatch):
    """status_code None = não alcançou o ADN — mesma classe de instabilidade."""
    _oficial(monkeypatch, NfseApiError('Falha ao baixar o DANFSE: timeout'))
    r = http.get(f'/api/v1/nfse/{nota_emitida.billing_id}/danfse')
    assert r.status_code == 200
    assert r.content == LOCAL


# ── Erros que NÃO são contingência: propagam ────────────────────────────────

@pytest.mark.parametrize('status', [401, 403, 404])
def test_erro_do_operador_nao_cai_na_reproducao(http, nota_emitida, monkeypatch, status):
    _oficial(monkeypatch, NfseApiError('recusado pelo ADN', status_code=status))
    r = http.get(f'/api/v1/nfse/{nota_emitida.billing_id}/danfse')
    assert r.status_code == 502          # NfseApiError → 502 (não vira PDF local)
    assert r.content != LOCAL


def test_erro_de_config_nao_cai_na_reproducao(http, nota_emitida, monkeypatch):
    """Ambiente/certificado ausente é problema nosso — tem de aparecer, não ser
    mascarado por um PDF de contingência."""
    _oficial(monkeypatch, NfseError('NFSE_NAC_AMBIENTE inválido'))
    r = http.get(f'/api/v1/nfse/{nota_emitida.billing_id}/danfse')
    assert r.status_code == 400          # NfseError → 400
    assert r.content != LOCAL


def test_instabilidade_sem_xml_nao_inventa_pdf(http, db, cliente, monkeypatch):
    """Sem XML não há o que montar; o erro do governo prevalece sobre o fallback."""
    nota = _nota(db, cliente, xml=None)
    _oficial(monkeypatch, NfseApiError('ADN fora do ar', status_code=503))
    r = http.get(f'/api/v1/nfse/{nota.billing_id}/danfse')
    assert r.status_code == 502
    assert r.content != LOCAL


# ── A rota /danfse-local não mudou: sempre a reprodução ─────────────────────

def test_danfse_local_ignora_o_governo(http, nota_emitida, monkeypatch):
    # mesmo com o oficial disponível, a rota -local usa a reprodução
    _oficial(monkeypatch, OFICIAL)
    r = http.get(f'/api/v1/nfse/{nota_emitida.billing_id}/danfse-local')
    assert r.status_code == 200
    assert r.content == LOCAL
