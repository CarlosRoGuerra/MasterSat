"""Painel do Financeiro para o Pix Automático (somente leitura).

A situação exibida é DERIVADA dos registros existentes (autorização, agendamento,
recebimento e cobrança); nada aqui altera estados financeiros ou bancários.
Regra central: atraso de consulta não é falha de débito. Só é "débito não
realizado" o que o próprio Sicredi informou (REJEITADA/EXPIRADA após o envio).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.timezone import BRASILIA_TZ, hoje
from app.models.billing import Billing
from app.models.client import Client
from app.models.enums import BillingStatus, ClientStatus
from app.models.sicredi_pix import SicrediPixCobranca, SicrediPixRecorrencia

# código: (rótulo, próxima ação, rótulo da ação, gravidade)
SITUACOES: dict[str, tuple[str, str, str, str]] = {
    'autorizacao_nao_gerada': ('Autorização não gerada', 'gerar_autorizacao', 'Gerar autorização', 'media'),
    'aguardando_geracao': ('Aguardando geração da autorização', 'aguardar', 'Aguardar consulta automática', 'info'),
    'autorizacao_pendente': ('Autorização pendente do cliente', 'contatar_cliente', 'Contatar cliente', 'media'),
    'autorizacao_recusada': ('Autorização recusada', 'contatar_cliente', 'Contatar cliente', 'alta'),
    'autorizacao_expirada': ('Autorização expirada', 'contatar_cliente', 'Contatar cliente e gerar nova autorização', 'alta'),
    'autorizacao_cancelada': ('Autorização cancelada', 'contatar_cliente', 'Contatar cliente e definir a forma de cobrança', 'alta'),
    'falha_comunicacao': ('Falha na comunicação com o Sicredi', 'verificar_integracao', 'Verificar integração e gerar novamente', 'alta'),
    'resultado_desconhecido': ('Resultado bancário desconhecido', 'consultar_banco', 'Consultar banco', 'alta'),
    'fora_da_autorizacao': ('Vencimento anterior ao início da autorização', 'cobrar_outro_meio', 'Cobrar por outro meio', 'alta'),
    'aguardando_associacao': ('Aguardando associação automática à autorização', 'aguardar', 'Nenhuma (automático)', 'info'),
    'aguardando_janela': ('Agendamento ainda não enviado (fora da janela)', 'nenhuma', 'Nenhuma', 'info'),
    'aguardando_envio': ('Agendamento ainda não enviado', 'aguardar', 'Aguardar envio automático', 'info'),
    'envio_atrasado': ('Agendamento não enviado e prazo terminando', 'verificar_integracao', 'Verificar envio automático', 'alta'),
    'agendamento_recusado': ('Agendamento recusado pelo banco', 'cobrar_outro_meio', 'Revisar e cobrar por outro meio', 'alta'),
    'agendamento_cancelado': ('Agendamento cancelado', 'cobrar_outro_meio', 'Cobrar por outro meio', 'alta'),
    'agendado': ('Agendado no banco', 'nenhuma', 'Nenhuma', 'info'),
    'aguardando_confirmacao': ('Aguardando confirmação do banco', 'aguardar', 'Aguardar consulta automática', 'info'),
    'pagamento_nao_confirmado': ('Pagamento não confirmado pelo banco', 'consultar_banco', 'Consultar banco e contatar cliente', 'alta'),
    'debito_nao_realizado': ('Débito não realizado', 'contatar_cliente', 'Contatar cliente', 'alta'),
    'revisao_necessaria': ('Revisão necessária', 'revisar', 'Revisar agendamento', 'alta'),
    'divergencia': ('Divergência de valor ou identificação', 'revisar', 'Conferir no Financeiro', 'alta'),
    'devolucao': ('Devolução ou estorno', 'revisar', 'Conferir no Financeiro', 'alta'),
    'falha_integracao': ('Falha na integração', 'verificar_integracao', 'Verificar integração', 'alta'),
    'pago': ('Pago', 'nenhuma', 'Nenhuma', 'info'),
}
AUTORIZACAO_PENDENTE = {'autorizacao_nao_gerada', 'aguardando_geracao', 'autorizacao_pendente', 'resultado_desconhecido'}
AGUARDANDO_CONFIRMACAO = {'aguardando_confirmacao', 'pagamento_nao_confirmado'}
INTEGRACAO = {'falha_comunicacao', 'resultado_desconhecido', 'envio_atrasado', 'agendamento_recusado', 'falha_integracao'}
SEM_ACAO = {'nenhuma', 'aguardar'}
TRANQUILAS = {'pago', 'agendado', 'aguardando_janela'}  # ocultas por padrão: nada pendente


def _ambiente() -> str:
    value = (settings.sicredi_pix_ambiente or '').strip().lower()
    return value if value in ('homologacao', 'producao') else 'homologacao'


def situacao_autorizacao(rec: SicrediPixRecorrencia | None) -> str:
    if rec is None:
        return 'autorizacao_nao_gerada'
    if rec.status == 'APROVADA' and rec.ativa:
        return 'aguardando_associacao'
    return {
        'PREPARANDO': 'aguardando_geracao',
        'CRIADA': 'autorizacao_pendente',
        'DESFECHO_DESCONHECIDO': 'resultado_desconhecido',
        'REJEITADA': 'autorizacao_recusada',
        'EXPIRADA': 'autorizacao_expirada',
        'CANCELADA': 'autorizacao_cancelada',
        'ERRO': 'falha_comunicacao',
    }.get(rec.status, 'resultado_desconhecido')


def _data_brt(value) -> date | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BRASILIA_TZ).date()


def situacao_cobranca(charge: SicrediPixCobranca, hoje_: date) -> str:
    pend = (charge.pendencia or '').lower()
    if charge.status == 'PENDENCIA':
        return 'revisao_necessaria'
    if charge.pendencia and charge.status == 'CONCLUIDA':
        return 'devolucao' if 'devolu' in pend or 'estorno' in pend else 'divergencia'
    if charge.status in ('ENVIANDO', 'DESFECHO_DESCONHECIDO'):
        return 'resultado_desconhecido'
    if charge.status in ('NA_FILA', 'AGUARDANDO_AUTORIZACAO'):
        if charge.erro and charge.status == 'NA_FILA':
            return 'falha_integracao'
        dias = (charge.vencimento - hoje_).days
        if dias > 10:
            return 'aguardando_janela'
        if dias <= 3:
            return 'envio_atrasado'
        return 'aguardando_envio'
    if charge.status in ('CRIADA', 'ATIVA'):
        if charge.pendencia:
            return 'devolucao' if 'devolu' in pend else 'divergencia'
        if hoje_ < charge.vencimento:
            return 'agendado'
        consultada = _data_brt(charge.consultada_em)
        # Só após uma consulta feita DEPOIS do dia do vencimento a falta de
        # liquidação é tratada como "não confirmado"; antes disso é atraso.
        return 'pagamento_nao_confirmado' if consultada and consultada > charge.vencimento else 'aguardando_confirmacao'
    if charge.status == 'REJEITADA':
        if charge.consultada_em is None:  # PUT recusado: o banco não aceitou o agendamento
            return 'agendamento_recusado'
        return 'debito_nao_realizado' if hoje_ >= charge.vencimento else 'agendamento_recusado'
    if charge.status == 'EXPIRADA':
        return 'debito_nao_realizado'
    if charge.status == 'CANCELADA':
        return 'agendamento_cancelado'
    if charge.status == 'CONCLUIDA':
        return 'pago'
    return 'revisao_necessaria'


def _linha(client: Client, situacao: str, *, billing: Billing | None = None, charge=None, rec=None,
           detalhe: str | None = None) -> dict:
    rotulo, acao, rotulo_acao, gravidade = SITUACOES[situacao]
    return {
        'client_id': client.id, 'cliente': client.name,
        'billing_id': billing.id if billing else None,
        'cobranca_pix_id': charge.id if charge else None,
        'vencimento': billing.due_date if billing else None,
        'valor': Decimal(str(billing.amount)) if billing else None,
        'situacao': situacao, 'situacao_label': rotulo,
        'acao': acao, 'acao_label': rotulo_acao, 'gravidade': gravidade,
        'detalhe': detalhe,
        'convite_status': rec.convite_status if rec is not None else None,
    }


def _detalhe_autorizacao(rec: SicrediPixRecorrencia | None) -> str | None:
    if rec is None:
        return 'Cliente com Pix Automático sem autorização solicitada ao banco.'
    partes = []
    if rec.erro:
        partes.append(rec.erro)
    if rec.status == 'CRIADA':
        partes.append({
            'enviado': 'Convite enviado por e-mail.',
            'erro': 'Convite por e-mail não enviado: ' + (rec.convite_erro or 'erro.'),
            'desconhecido': 'Envio do convite sem confirmação; confira antes de reenviar.',
            'pendente': 'Convite por e-mail aguardando envio.',
        }.get(rec.convite_status or '', 'Convite ainda não enviado ao cliente.'))
    return ' '.join(partes) or None


def montar_painel(db: Session, *, incluir_resolvidos: bool = False) -> dict:
    hoje_ = hoje()
    env = _ambiente()
    payers = {c.id: c for c in db.scalars(select(Client).where(
        Client.is_deleted.is_(False), Client.forma_cobranca == 'pix_automatico',
        Client.status != ClientStatus.INACTIVE)).all()}
    abertas = db.execute(select(Billing, func.coalesce(Billing.payer_client_id, Billing.client_id)).where(
        Billing.payment_method == 'pix_automatico', Billing.is_deleted.is_(False),
        Billing.status.in_((BillingStatus.PENDING, BillingStatus.OVERDUE)),
    ).order_by(Billing.due_date, Billing.id)).all()
    pagas = db.execute(select(Billing, func.coalesce(Billing.payer_client_id, Billing.client_id)).where(
        Billing.payment_method == 'pix_automatico', Billing.is_deleted.is_(False),
        Billing.status == BillingStatus.PAID, Billing.due_date >= hoje_ - timedelta(days=45),
    ).order_by(Billing.due_date, Billing.id)).all() if incluir_resolvidos else []
    payer_ids = set(payers) | {pid for _, pid in abertas} | {pid for _, pid in pagas}
    for client in db.scalars(select(Client).where(Client.id.in_(payer_ids - set(payers)))).all() if payer_ids else []:
        payers[client.id] = client
    recs: dict[int, SicrediPixRecorrencia] = {}
    if payer_ids:
        for rec in db.scalars(select(SicrediPixRecorrencia).where(
            SicrediPixRecorrencia.client_id.in_(payer_ids), SicrediPixRecorrencia.ambiente == env,
        ).order_by(SicrediPixRecorrencia.ativa.asc(), SicrediPixRecorrencia.id.asc())).all():
            recs[rec.client_id] = rec  # ativa e mais recente prevalecem (mesma ordem de recorrencia_cliente)
    billing_ids = [b.id for b, _ in (*abertas, *pagas)]
    charges = {c.billing_id: c for c in db.scalars(select(SicrediPixCobranca).where(
        SicrediPixCobranca.billing_id.in_(billing_ids))).all()} if billing_ids else {}

    linhas: list[dict] = []
    com_cobranca: set[int] = set()
    for billing, payer_id in (*abertas, *pagas):
        client = payers.get(payer_id)
        if client is None:
            continue
        com_cobranca.add(payer_id)
        rec = recs.get(payer_id)
        charge = charges.get(billing.id)
        if charge is not None:
            situacao = situacao_cobranca(charge, hoje_)
            if charge.status == 'AGUARDANDO_AUTORIZACAO' and rec is not None and rec.status != 'APROVADA':
                situacao = situacao_autorizacao(rec)
            detalhe = charge.pendencia or charge.erro
            if situacao in ('aguardando_janela', 'aguardando_envio', 'envio_atrasado'):
                detalhe = (f'Janela de envio: {(charge.vencimento - timedelta(days=10)):%d/%m} a '
                           f'{(charge.vencimento - timedelta(days=2)):%d/%m}.' + (f' {detalhe}' if detalhe else ''))
        elif rec is not None and rec.ativa and rec.status == 'APROVADA':
            situacao = 'fora_da_autorizacao' if billing.due_date < rec.data_inicial else 'aguardando_associacao'
            detalhe = (f'Autorização válida a partir de {rec.data_inicial:%d/%m/%Y}.'
                       if situacao == 'fora_da_autorizacao' else None)
        else:
            situacao = situacao_autorizacao(rec)
            detalhe = _detalhe_autorizacao(rec)
        if billing.status == BillingStatus.PAID and situacao != 'pago' and not (charge and charge.pendencia):
            situacao, detalhe = 'pago', None
        linhas.append(_linha(client, situacao, billing=billing, charge=charge, rec=rec, detalhe=detalhe))

    for payer_id, client in payers.items():
        if payer_id in com_cobranca or client.forma_cobranca != 'pix_automatico':
            continue
        rec = recs.get(payer_id)
        if rec is not None and rec.ativa and rec.status == 'APROVADA':
            continue
        linhas.append(_linha(client, situacao_autorizacao(rec), rec=rec, detalhe=_detalhe_autorizacao(rec)))

    if not incluir_resolvidos:
        linhas = [l for l in linhas if l['situacao'] not in TRANQUILAS]
    ordem = {'alta': 0, 'media': 1, 'info': 2}
    linhas.sort(key=lambda l: (ordem[l['gravidade']], l['vencimento'] or date.max, l['cliente'] or ''))

    # Por pagador, a partir da própria autorização (não das linhas de cobrança).
    pendentes = {pid for pid, client in payers.items()
                 if (client.forma_cobranca == 'pix_automatico' or pid in com_cobranca)
                 and situacao_autorizacao(recs.get(pid)) in AUTORIZACAO_PENDENTE}
    contatar = {l['client_id'] for l in linhas if l['acao'] == 'contatar_cliente'}
    total_conf = sum((l['valor'] or Decimal('0')) for l in linhas
                     if l['situacao'] in AGUARDANDO_CONFIRMACAO
                     or (l['situacao'] == 'resultado_desconhecido' and l['cobranca_pix_id']))
    resumo = {
        'autorizacoes_pendentes': len(pendentes),
        'debitos_nao_realizados': sum(1 for l in linhas if l['situacao'] == 'debito_nao_realizado'),
        'total_aguardando_confirmacao': Decimal(total_conf).quantize(Decimal('0.01')),
        'clientes_a_contatar': len(contatar),
        'falhas_integracao': sum(1 for l in linhas if l['situacao'] in INTEGRACAO),
        'acoes_necessarias': sum(1 for l in linhas if l['acao'] not in SEM_ACAO),
    }
    agrupado = defaultdict(int)
    for l in linhas:
        agrupado[l['situacao']] += 1
    return {'ambiente': env, 'hoje': hoje_, 'resumo': resumo, 'por_situacao': dict(agrupado), 'linhas': linhas}
