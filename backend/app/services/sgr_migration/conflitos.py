"""
Fila de decisões da migração SGR (``sgr_conflitos``).

Conflito é o que a importação NÃO decide sozinha: veículo que no MasterSat é
de outro cliente, cobrança mexida aqui e na origem, transição que a política
não automatiza (pago→aberto, cancelado→pago), valor alterado na origem depois
de importado. Cada decisão fica registrada com responsável e justificativa;
nada é apagado.

Decisões:
  * ``manter_local`` — o MasterSat prevalece. A próxima rodada não reabre o
    conflito enquanto a origem continuar no mesmo estado.
  * ``aplicar_origem`` — leva a cobrança ao estado da origem, pela política
    bancária única. Só para as transições que a própria sincronização faria
    (aberto→pago, aberto→cancelado, vencimento); estorno ou reabertura passam
    pelo fluxo financeiro normal (recebimento/estorno), não por aqui.
  * ``aprovar_transferencia`` — o veículo passa ao cliente da origem. Recusada
    enquanto houver contrato ativo do dono anterior: encerrar o contrato é
    decisão comercial, tomada na tela de contratos.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.billing import Billing
from app.models.contract import Contract
from app.models.sgr_migracao import SgrConflito, SgrDocumentoLinha, SgrVinculo
from app.models.tracker import Tracker
from app.models.vehicle import Vehicle
from app.services import titulo_bancario

DECISOES = ('manter_local', 'aplicar_origem', 'aprovar_transferencia')
_APLICAVEIS = ('edicao_local', 'transicao_nao_automatica', 'divergencia_legado', 'titulo_bancario_local')


class ResolucaoRecusada(ValueError):
    pass


def listar(db: Session, status: str = 'aberto') -> list[dict]:
    """Conflitos para a tela/CLI — só ids, códigos, tipos e o detalhe
    (que já nasce sem dado pessoal)."""
    return [
        {
            'id': c.id, 'tipo': c.tipo, 'entidade': c.entidade, 'chave_origem': c.chave_origem,
            'tabela_local': c.tabela_local, 'local_id': c.local_id, 'detalhe': c.detalhe,
            'execucao_id': c.execucao_id, 'status': c.status, 'resolucao': c.resolucao,
        }
        for c in db.scalars(select(SgrConflito).where(SgrConflito.status == status).order_by(SgrConflito.id)).all()
    ]


def _linha_da_cobranca(db: Session, billing_id: int | None) -> SgrDocumentoLinha | None:
    if billing_id is None:
        return None
    return db.scalar(select(SgrDocumentoLinha).where(SgrDocumentoLinha.billing_id == billing_id))


def resolver(db: Session, conflito_id: int, decisao: str, *, operador: str, justificativa: str) -> SgrConflito:
    """Aplica a decisão e registra quem decidiu. Não faz commit."""
    from app.services.sgr_migration.importer import _aplicar_alvo, impressao_local

    if decisao not in DECISOES:
        raise ResolucaoRecusada(f'decisão desconhecida: {decisao} (use {", ".join(DECISOES)})')
    operador = (operador or '').strip()
    justificativa = (justificativa or '').strip()
    if not operador or not justificativa:
        raise ResolucaoRecusada('operador e justificativa são obrigatórios')
    conflito = db.get(SgrConflito, conflito_id)
    if conflito is None:
        raise ResolucaoRecusada(f'conflito #{conflito_id} não existe')
    if conflito.status != 'aberto':
        raise ResolucaoRecusada(f'conflito #{conflito_id} já foi resolvido ({conflito.resolucao})')

    if decisao == 'manter_local':
        linha = _linha_da_cobranca(db, conflito.local_id) if conflito.tabela_local == 'billings' else None
        if linha is not None:
            billing = db.get(Billing, linha.billing_id)
            if billing is not None:
                # O estado atual (decidido por gente) vira a referência: as
                # próximas mudanças da origem partem dele.
                linha.hash_local = impressao_local(billing)

    elif decisao == 'aplicar_origem':
        if conflito.tipo not in _APLICAVEIS or conflito.tabela_local != 'billings':
            raise ResolucaoRecusada(
                f'"aplicar_origem" não se aplica a {conflito.tipo}: resolva pelo fluxo normal do '
                f'MasterSat e registre "manter_local"'
            )
        alvo = (conflito.detalhe or {}).get('alvo')
        billing = db.get(Billing, conflito.local_id)
        if not alvo or billing is None:
            raise ResolucaoRecusada('conflito sem estado de origem registrado ou cobrança inexistente')
        try:
            _aplicar_alvo(db, billing, alvo, f'Migração SGR — conflito #{conflito.id} ({operador}): {justificativa}')
        except titulo_bancario.PoliticaBancariaError as exc:
            raise ResolucaoRecusada(f'política bancária recusou: {exc.message}') from exc
        except ValueError as exc:
            raise ResolucaoRecusada(
                f'{exc} — estorno/reabertura é operação financeira: use o recebimento/estorno do '
                f'MasterSat e depois registre "manter_local"'
            ) from exc
        linha = _linha_da_cobranca(db, billing.id)
        if linha is not None:
            linha.hash_local = impressao_local(db.get(Billing, billing.id))

    else:  # aprovar_transferencia
        if conflito.tipo != 'transferencia_veiculo':
            raise ResolucaoRecusada('"aprovar_transferencia" só vale para transferencia_veiculo')
        detalhe = conflito.detalhe or {}
        vehicle = db.get(Vehicle, conflito.local_id)
        origem, destino = detalhe.get('dono_local_client_id'), detalhe.get('dono_origem_client_id')
        if vehicle is None or vehicle.is_deleted:
            raise ResolucaoRecusada('veículo não existe mais no MasterSat')
        if vehicle.client_id != origem:
            raise ResolucaoRecusada('o dono do veículo mudou desde o conflito — rode a importação de novo')
        ativo = db.scalar(select(Contract.id).where(
            Contract.vehicle_id == vehicle.id, Contract.client_id == origem,
            Contract.is_deleted.is_(False), Contract.status == 'ativo',
        ).limit(1))
        if ativo is not None:
            raise ResolucaoRecusada(
                f'o contrato #{ativo} do dono atual está ativo: encerre-o antes (o histórico e as '
                f'cobranças dele continuam com o dono anterior)'
            )
        vehicle.client_id = destino
        # Se a origem registrou o veículo com outro código para o novo dono, a
        # identidade passa a ser esse código (o antigo fica no audit log).
        codigo_novo = detalhe.get('cod_veiculo_origem')
        anterior = None
        vinculo = db.scalar(select(SgrVinculo).where(
            SgrVinculo.entidade == 'veiculo', SgrVinculo.local_id == vehicle.id,
        ))
        if codigo_novo and vinculo is not None and vinculo.chave_origem != str(codigo_novo):
            ocupado = db.scalar(select(SgrVinculo.id).where(
                SgrVinculo.entidade == 'veiculo', SgrVinculo.chave_origem == str(codigo_novo),
            ))
            if ocupado is not None:
                raise ResolucaoRecusada(f'o código de origem {codigo_novo} já identifica outro veículo')
            anterior = vinculo.chave_origem
            vinculo.chave_origem = str(codigo_novo)
        for tracker in db.scalars(select(Tracker).where(
            Tracker.vehicle_id == vehicle.id, Tracker.client_id == origem, Tracker.is_deleted.is_(False),
        )).all():
            tracker.client_id = destino
        db.add(AuditLog(
            user_name=operador[:120], method='CLI', path='scripts/sgr_import.py --resolver',
            entity_type='vehicle', entity_id=vehicle.id,
            description=(f'Migração SGR: transferência do veículo #{vehicle.id} do cliente #{origem} para '
                         f'#{destino} (conflito #{conflito.id})'
                         + (f'; código de origem {anterior} → {codigo_novo}' if anterior else '')
                         + f'. {justificativa}')[:2000],
        ))

    conflito.status = 'resolvido'
    conflito.resolucao = decisao
    conflito.resolvido_por = operador[:120]
    conflito.justificativa = justificativa
    conflito.resolvido_em = datetime.now(timezone.utc)
    db.flush()
    return conflito
