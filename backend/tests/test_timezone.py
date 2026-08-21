"""Data/hora de negócio no fuso de Brasília (vencimento/inadimplência)."""
from __future__ import annotations

from datetime import date, timedelta

from app.core.timezone import agora, hoje
from app.services.financial import valor_com_juros


def test_agora_e_aware_em_utc_menos_3():
    a = agora()
    assert a.tzinfo is not None
    assert a.utcoffset() == timedelta(hours=-3)


def test_hoje_retorna_data():
    assert isinstance(hoje(), date)
    assert hoje() == agora().date()


def test_valor_com_juros_usa_referencia_posicional():
    # Regressão da renomeação do parâmetro (antes 'hoje', colidia com o helper).
    venc = date(2025, 1, 10)
    ref = date(2025, 2, 10)  # 31 dias de atraso → 2 meses (ceil)
    assert valor_com_juros(100.0, venc, ref) == round(100 * 1.02 + 100 * 0.01 * 2, 2)


def test_valor_com_juros_none_quando_nao_vencido():
    futuro = hoje() + timedelta(days=30)
    assert valor_com_juros(100.0, futuro) is None
