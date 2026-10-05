"""Dia de vencimento a herdar ao criar um contrato.

No SGR o dia de vencimento mora no vínculo (contrato), não no cadastro do
cliente — por isso a migração deixou ``clients.billing_day`` vazio para quase
todos os clientes, e o vínculo de rastreador caía no dia do início do contrato
(limitado a 28), com vencimento diferente do que o cliente sempre teve.

Ordem de herança:
1. o dia do cadastro do cliente;
2. o dos contratos ATIVOS do cliente — o do próprio veículo, se houver; senão
   o mais frequente (``alternativas`` lista os outros dias, para conferir);
3. o do contrato mais recente (qualquer situação) do veículo e, depois, do
   cliente — é o caso do contrato SGR encerrado que está sendo refeito.
"""
from __future__ import annotations

from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.client import Client
from app.models.contract import Contract


def sugerir_dia_vencimento(db: Session, client_id: int, vehicle_id: int | None = None) -> dict:
    client = db.get(Client, client_id)
    if client and client.billing_day:
        return _sugestao(client.billing_day, 'cliente')

    contratos = db.scalars(
        select(Contract)
        .where(
            Contract.client_id == client_id,
            Contract.is_deleted.is_(False),
            Contract.billing_day.is_not(None),
        )
        .order_by(Contract.id.desc())
    ).all()

    ativos = [c for c in contratos if c.status == 'ativo']
    if ativos:
        dias = sorted({c.billing_day for c in ativos})
        do_veiculo = next((c for c in ativos if vehicle_id and c.vehicle_id == vehicle_id), None)
        if do_veiculo:
            escolhido = do_veiculo
        else:
            # Mais frequente; empate fica com o contrato mais recente.
            contagem = Counter(c.billing_day for c in ativos)
            mais_comum = max(contagem.values())
            escolhido = next(c for c in ativos if contagem[c.billing_day] == mais_comum)
        alternativas = [d for d in dias if d != escolhido.billing_day]
        return _sugestao(escolhido.billing_day, 'contrato_ativo', escolhido.id, alternativas)

    anterior = next((c for c in contratos if vehicle_id and c.vehicle_id == vehicle_id), None)
    anterior = anterior or (contratos[0] if contratos else None)
    if anterior:
        return _sugestao(anterior.billing_day, 'contrato_anterior', anterior.id)
    return _sugestao(None, None)


def _sugestao(dia, origem, contrato_id=None, alternativas=None) -> dict:
    return {
        'dia': dia,
        'origem': origem,
        'contrato_id': contrato_id,
        'alternativas': alternativas or [],
    }
