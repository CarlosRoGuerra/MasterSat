"""Pix Automático: autorização por pagador, fila por ciclo e baixa conciliada."""
from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re
from uuid import uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.timezone import BRASILIA_TZ, hoje
from app.models.billing import Billing
from app.models.client import Client
from app.models.enums import BillingStatus, ClientStatus
from app.models.sicredi_pix import SicrediPixCobranca, SicrediPixRecebimento, SicrediPixRecorrencia
from app.services.financial import lock_billings_for_update, marcar_billing_pago
from app.services.sicredi_pix_client import PixClient, PixError, PixResultadoIncerto, ambiente, erro_seguro, exigir_escrita
from app.services.titulo_bancario import PoliticaBancariaError

REC_TERMINAIS = {'CANCELADA', 'REJEITADA', 'EXPIRADA'}
COB_TERMINAIS = {'CANCELADA', 'REJEITADA', 'EXPIRADA', 'CONCLUIDA'}
ID_REC = re.compile(r'[a-zA-Z0-9]{29}')
TXID = re.compile(r'[a-zA-Z0-9]{26,35}')


def agora() -> datetime:
    return datetime.now(timezone.utc)


def recorrencia_cliente(db: Session, client_id: int) -> SicrediPixRecorrencia | None:
    return db.scalar(select(SicrediPixRecorrencia).where(
        SicrediPixRecorrencia.client_id == client_id,
        SicrediPixRecorrencia.ambiente == ambiente(),
    ).order_by(SicrediPixRecorrencia.ativa.desc(), SicrediPixRecorrencia.id.desc()))


def validar_alteracao_cliente(db: Session, client: Client, data: dict) -> None:
    rec = db.scalar(select(SicrediPixRecorrencia).where(
        SicrediPixRecorrencia.client_id == client.id, SicrediPixRecorrencia.ativa.is_(True)))
    sent = db.scalar(select(SicrediPixCobranca.id).join(SicrediPixRecorrencia).where(
        SicrediPixRecorrencia.client_id == client.id,
        SicrediPixCobranca.enviada_em.is_not(None), SicrediPixCobranca.status.not_in(COB_TERMINAIS)))
    if rec or sent:
        changes_identity = 'cpf_cnpj' in data and data['cpf_cnpj'] != client.cpf_cnpj
        changes_cycle = 'billing_day' in data and data['billing_day'] != client.billing_day
        leaving = 'forma_cobranca' in data and data['forma_cobranca'] != 'pix_automatico'
        deactivating = data.get('is_deleted') or ('status' in data and data['status'] != ClientStatus.ACTIVE)
        if changes_identity or changes_cycle or leaving or deactivating:
            raise PixError('Cancele e confirme a autorização Pix Automático antes de alterar o pagador, vencimento ou forma de cobrança.')
    if data.get('forma_cobranca', client.forma_cobranca) == 'pix_automatico':
        data['boleto_format'] = 'unico'
        data['boleto_fee'] = 'nao'


def _aplicar_recorrencia(db: Session, rec: SicrediPixRecorrencia, body: dict) -> None:
    rec = db.scalar(select(SicrediPixRecorrencia).where(SicrediPixRecorrencia.id == rec.id)
                    .with_for_update().execution_options(populate_existing=True))
    id_rec = body.get('idRec', '')
    if not ID_REC.fullmatch(id_rec) or (rec.id_rec and rec.id_rec != id_rec):
        raise PixError('Consulta devolveu outra autorização Pix ou um identificador inválido.')
    vinculo = body.get('vinculo') or {}
    if vinculo.get('contrato') and vinculo['contrato'] != rec.referencia:
        raise PixError('O objeto da autorização Pix não corresponde ao cadastro.')
    status = body.get('status')
    if status not in {'CRIADA', 'APROVADA', *REC_TERMINAIS}:
        raise PixError('Situação de autorização Pix não reconhecida; nenhuma cobrança será agendada.')
    if rec.status in REC_TERMINAIS and status not in REC_TERMINAIS:
        raise PixError('Autorização encerrada não pode ser reativada por uma resposta anterior.')
    rec.id_rec = id_rec
    rec.status = status
    rec.ativa = status not in REC_TERMINAIS
    rec.consultada_em = agora()
    rec.processar_evento = False
    rec.erro = None
    qr = (body.get('dadosQR') or {}).get('pixCopiaECola')
    if qr:
        rec.pix_copia_cola = str(qr)[:512]
    if not rec.ativa:
        rec.pix_copia_cola = None
        for charge in db.scalars(select(SicrediPixCobranca).where(
            SicrediPixCobranca.recorrencia_id == rec.id,
            SicrediPixCobranca.enviada_em.is_(None),
            SicrediPixCobranca.status.not_in(COB_TERMINAIS),
        )).all():
            charge.status = 'CANCELADA'
            charge.erro = 'Autorização Pix encerrada antes do envio.'


def criar_recorrencia(db: Session, client_id: int, *, data_inicial: date,
                     valor_minimo: Decimal, objeto: str) -> SicrediPixRecorrencia:
    exigir_escrita()
    client = db.scalar(select(Client).where(Client.id == client_id).with_for_update())
    if not client or client.is_deleted or client.status != ClientStatus.ACTIVE:
        raise PixError('Cliente não está ativo.')
    if client.forma_cobranca != 'pix_automatico':
        raise PixError('Selecione Pix Automático na forma de cobrança e salve o cliente primeiro.')
    if data_inicial < hoje() + timedelta(days=2):
        raise PixError('O primeiro vencimento deve permitir ao menos dois dias para o agendamento.')
    if valor_minimo < Decimal('0.01') or len(objeto.strip()) not in range(1, 36):
        raise PixError('Informe objeto de até 35 caracteres e valor mínimo positivo.')
    existing = recorrencia_cliente(db, client_id)
    if existing and existing.ativa:
        return existing
    rec = SicrediPixRecorrencia(client_id=client_id, ambiente=ambiente(), referencia='MS' + uuid4().hex,
        data_inicial=data_inicial, valor_minimo=valor_minimo, objeto=objeto.strip(),
        status='PREPARANDO', ativa=True, politica_retentativa='NAO_PERMITE')
    db.add(rec)
    db.commit()  # Reserva durável antes de qualquer chamada de criação.
    rec_id = rec.id
    try:
        with PixClient(escrita=True) as api:
            location = api.request('POST', '/api/v1/locrec')
            if not str(location.get('id', '')).isdigit():
                raise PixResultadoIncerto('Location não retornada pelo Sicredi; consulte antes de criar outra autorização.')
            rec.location_id = str(location['id'])
            db.commit()
            digits = re.sub(r'\D', '', client.cpf_cnpj)
            body = {
                'vinculo': {'contrato': rec.referencia, 'devedor': {
                    'cpf' if len(digits) == 11 else 'cnpj': digits, 'nome': client.name[:140]}, 'objeto': rec.objeto},
                'calendario': {'dataInicial': data_inicial.isoformat(), 'periodicidade': 'MENSAL'},
                'valor': {'valorMinimoRecebedor': f'{valor_minimo:.2f}'},
                'politicaRetentativa': rec.politica_retentativa,
                'loc': int(rec.location_id),
            }
            rec.status = 'DESFECHO_DESCONHECIDO'
            db.commit()
            response = api.request('POST', '/api/v1/rec', json=body)
            # Guardar o ID antes de buscar o QR: falha nessa consulta não autoriza outro POST.
            if not ID_REC.fullmatch(str(response.get('idRec', ''))):
                raise PixResultadoIncerto('Sicredi não retornou o identificador da autorização; consulte o desfecho.')
            rec.id_rec = response['idRec']
            rec.status = 'CRIADA'
            db.commit()
            _aplicar_recorrencia(db, rec, api.request('GET', '/api/v1/rec/' + rec.id_rec))
            db.commit()
    except PixError as exc:
        db.rollback()
        rec = db.get(SicrediPixRecorrencia, rec_id)
        rec.erro = erro_seguro(str(exc))
        if not rec.id_rec:
            rec.status = 'DESFECHO_DESCONHECIDO' if isinstance(exc, PixResultadoIncerto) else 'ERRO'
            rec.ativa = isinstance(exc, PixResultadoIncerto)
        db.commit()
        raise
    return rec


def consultar_recorrencia(db: Session, rec: SicrediPixRecorrencia) -> SicrediPixRecorrencia:
    if rec.ambiente != ambiente():
        raise PixError('Esta autorização pertence a outro ambiente.')
    with PixClient() as api:
        if rec.id_rec:
            body = api.request('GET', '/api/v1/rec/' + rec.id_rec)
        else:
            # POST sem resposta: recuperar pelo vínculo, nunca repetir criação às cegas.
            client = db.get(Client, rec.client_id)
            created = rec.created_at.replace(tzinfo=timezone.utc) if rec.created_at.tzinfo is None else rec.created_at
            query = {'inicio': (created - timedelta(minutes=5)).isoformat(), 'fim': agora().isoformat(),
                     'cpf' if len(client.cpf_cnpj) == 11 else 'cnpj': client.cpf_cnpj,
                     'paginacao.itensPorPagina': 100}
            found = []
            for page in range(10):
                query['paginacao.paginaAtual'] = page
                result = api.request('GET', '/api/v1/rec', params=query)
                rows = result.get('recs', [])
                found.extend(x for x in rows if (x.get('vinculo') or {}).get('contrato') == rec.referencia)
                if len(rows) < 100:
                    break
            if len(found) != 1:
                raise PixError('Não foi possível identificar um desfecho único. Nenhuma autorização foi reenviada.')
            id_rec = found[0].get('idRec', '')
            if not ID_REC.fullmatch(id_rec):
                raise PixError('Resposta de recuperação da autorização inválida.')
            body = api.request('GET', '/api/v1/rec/' + id_rec)
        _aplicar_recorrencia(db, rec, body)
        db.commit()
    return rec


def cancelar_recorrencia(db: Session, rec: SicrediPixRecorrencia) -> SicrediPixRecorrencia:
    exigir_escrita()
    if rec.ambiente != ambiente() or not rec.id_rec:
        raise PixError('Consulte o desfecho desta autorização antes de solicitar o cancelamento.')
    if rec.status in REC_TERMINAIS:
        return rec
    with PixClient(escrita=True) as api:
        api.request('PATCH', '/api/v1/rec/' + rec.id_rec, json={'status': 'CANCELADA'})
        _aplicar_recorrencia(db, rec, api.request('GET', '/api/v1/rec/' + rec.id_rec))
        db.commit()
    return rec


def ciclo_mensal(inicio: date, vencimento: date) -> date:
    if vencimento < inicio:
        raise PixError('Vencimento anterior ao início da autorização Pix.')
    months = (vencimento.year - inicio.year) * 12 + vencimento.month - inicio.month
    def anchor(offset):
        total = inicio.year * 12 + inicio.month - 1 + offset
        year, month = divmod(total, 12)
        month += 1
        return date(year, month, min(inicio.day, monthrange(year, month)[1]))
    current = anchor(months)
    return anchor(months - 1) if vencimento < current else current


def enfileirar_fechamento(db: Session, billing_ids: list[int]) -> list[int]:
    """Somente grava a fila local, dentro da transação do fechamento; não acessa rede."""
    queued = []
    for bid in sorted(set(billing_ids)):
        billing = db.get(Billing, bid)
        if not billing or billing.is_deleted or billing.status not in (BillingStatus.PENDING, BillingStatus.OVERDUE):
            continue
        payer = db.get(Client, billing.payer_client_id or billing.client_id)
        if not payer or payer.forma_cobranca != 'pix_automatico':
            continue
        billing.payment_method = 'pix_automatico'
        existing = db.scalar(select(SicrediPixCobranca).where(SicrediPixCobranca.billing_id == bid))
        if existing:
            continue
        rec = recorrencia_cliente(db, payer.id)
        if not rec or not rec.ativa:
            continue  # Mostrada como pendente de adesão no cadastro, sem converter títulos antigos.
        try:
            ciclo = ciclo_mensal(rec.data_inicial, billing.due_date)
        except PixError:
            continue
        conflict = db.scalar(select(SicrediPixCobranca).where(
            SicrediPixCobranca.recorrencia_id == rec.id, SicrediPixCobranca.ciclo == ciclo))
        if conflict:
            conflict.pendencia = f'Existe outra cobrança (#{bid}) no mesmo ciclo. Revise antes de agendar.'
            if conflict.enviada_em is None and conflict.status not in COB_TERMINAIS:
                conflict.status = 'PENDENCIA'
            continue
        charge = SicrediPixCobranca(billing_id=bid, recorrencia_id=rec.id, txid=uuid4().hex,
            ciclo=ciclo, vencimento=billing.due_date, valor=billing.amount,
            status='NA_FILA' if rec.status == 'APROVADA' else 'AGUARDANDO_AUTORIZACAO')
        db.add(charge)
        db.flush()
        queued.append(charge.id)
    return queued


def _aplicar_recebimento(db: Session, charge: SicrediPixCobranca, pix: dict) -> bool:
    """Somente dados devolvidos pela conexão bancária autenticada entram aqui."""
    if pix.get('txid') != charge.txid or not re.fullmatch(r'E[A-Za-z0-9]{31}', str(pix.get('endToEndId', ''))):
        charge.pendencia = 'Identificação do recebimento Pix divergente ou incompleta.'
        db.commit()
        return False
    try:
        amount = Decimal(str(pix['valor']))
        if not amount.is_finite() or amount <= 0 or amount > Decimal('99999999.99') or amount != amount.quantize(Decimal('0.01')):
            raise ValueError('valor inválido')
        moment = datetime.fromisoformat(pix['horario'].replace('Z', '+00:00'))
        if moment.tzinfo is None:
            raise ValueError('horário sem fuso')
    except (KeyError, ValueError, InvalidOperation, TypeError):
        charge.pendencia = 'Valor ou horário do recebimento Pix inválido.'
        db.commit()
        return False
    billing = lock_billings_for_update(db, [charge.billing_id])[0]
    receipt = db.scalar(select(SicrediPixRecebimento).where(
        SicrediPixRecebimento.end_to_end_id == pix['endToEndId']))
    if receipt and receipt.cobranca_id != charge.id:
        charge.pendencia = 'Recebimento já associado a outra cobrança.'
        db.commit()
        return False
    if receipt and receipt.aplicado:
        if pix.get('devolucoes'):
            receipt.pendencia = charge.pendencia = 'Recebimento possui devolução; conferir no financeiro.'
            db.commit()
        elif billing.status != BillingStatus.PAID or billing.is_deleted:
            receipt.pendencia = charge.pendencia = 'Recebimento já conciliado com situação local alterada; conferir estorno.'
            db.commit()
        return False
    if receipt is None:
        receipt = SicrediPixRecebimento(cobranca_id=charge.id, end_to_end_id=pix['endToEndId'],
            valor=amount, horario=moment, aplicado=False)
        db.add(receipt)
    reason = None
    if pix.get('devolucoes'):
        reason = 'Recebimento possui devolução; exige conferência do financeiro.'
    elif amount != Decimal(str(charge.valor)) or amount != Decimal(str(billing.amount)):
        reason = 'Pagamento Pix com valor diferente da cobrança; confira antes de dar baixa.'
    elif billing.is_deleted or billing.status == BillingStatus.CANCELED:
        reason = 'Pagamento Pix de cobrança cancelada ou removida.'
    elif billing.status == BillingStatus.PAID:
        reason = 'Possível pagamento duplicado; cobrança já recebida.'
    if reason:
        receipt.pendencia = charge.pendencia = reason
        db.commit()
        return False
    receipt.aplicado = True
    charge.pendencia = None
    charge.status = 'CONCLUIDA'
    marcar_billing_pago(db, billing, payment_date=moment.astimezone(BRASILIA_TZ).date(),
        paid_amount=amount, payment_method='pix_automatico', notes='Baixa conciliada pela API Pix Sicredi.', lock=False)
    return True


def consultar_cobranca(db: Session, charge: SicrediPixCobranca, *, api: PixClient | None = None) -> None:
    rec = db.get(SicrediPixRecorrencia, charge.recorrencia_id)
    if rec.ambiente != ambiente():
        raise PixError('Esta cobrança Pix pertence a outro ambiente.')
    if api is None:
        with PixClient() as own_api:
            return consultar_cobranca(db, charge, api=own_api)
    body = api.request('GET', '/api/v1/cobr/' + charge.txid)
    lock_billings_for_update(db, [charge.billing_id])
    charge = db.scalar(select(SicrediPixCobranca).where(SicrediPixCobranca.id == charge.id)
                       .with_for_update().execution_options(populate_existing=True))
    if body.get('txid') != charge.txid or body.get('idRec') != rec.id_rec:
        raise PixError('Consulta bancária não corresponde à cobrança Pix solicitada.')
    bank_status = body.get('status')
    if bank_status not in {'CRIADA', 'ATIVA', *COB_TERMINAIS}:
        raise PixError('Situação bancária Pix desconhecida; consulta preservada para conferência.')
    if charge.status in COB_TERMINAIS and bank_status not in COB_TERMINAIS:
        raise PixError('Consulta anterior não pode reativar agendamento Pix encerrado.')
    charge.status = bank_status
    charge.enviada_em = charge.enviada_em or agora()
    charge.consultada_em = agora()
    charge.proxima_tentativa = agora() + timedelta(seconds=max(30, settings.sicredi_pix_worker_interval_seconds))
    charge.processar_evento = False
    charge.erro = None
    payments = body.get('pix') or []
    if len(payments) == 1:
        _aplicar_recebimento(db, charge, payments[0])
    elif len(payments) > 1:
        charge.pendencia = 'Mais de um recebimento Pix nesta cobrança; conferir duplicidade.'
    elif bank_status == 'CONCLUIDA':
        charge.pendencia = 'Sicredi informa conclusão sem os dados da liquidação; baixa aguardando confirmação.'
    db.commit()


def cancelar_cobranca(db: Session, charge: SicrediPixCobranca) -> None:
    lock_billings_for_update(db, [charge.billing_id])
    charge = db.scalar(select(SicrediPixCobranca).where(SicrediPixCobranca.id == charge.id)
                       .with_for_update().execution_options(populate_existing=True))
    if charge.status in COB_TERMINAIS:
        return
    rec = db.get(SicrediPixRecorrencia, charge.recorrencia_id)
    if rec.ambiente != ambiente():
        raise PixError('Esta cobrança pertence a outro ambiente.')
    if charge.enviada_em is None:
        charge.status = 'CANCELADA'
        charge.erro = 'Agendamento cancelado antes do envio ao Sicredi.'
        db.commit()
        return
    exigir_escrita()
    deadline = datetime.combine(charge.vencimento - timedelta(days=1), time(22), tzinfo=BRASILIA_TZ)
    if agora() >= deadline:
        raise PixError('Prazo de cancelamento encerrado: até 22h da véspera do vencimento.')
    with PixClient(escrita=True) as api:
        api.request('PATCH', '/api/v1/cobr/' + charge.txid, json={'status': 'CANCELADA'})
        consultar_cobranca(db, charge, api=api)


def processar_cobranca(db: Session, charge: SicrediPixCobranca) -> None:
    if charge.enviada_em or charge.status in {'ENVIANDO', 'DESFECHO_DESCONHECIDO'}:
        consultar_cobranca(db, charge)
        return
    if charge.status in COB_TERMINAIS or charge.status == 'PENDENCIA':
        if charge.processar_evento and charge.enviada_em:
            consultar_cobranca(db, charge)
        return
    rec = db.get(SicrediPixRecorrencia, charge.recorrencia_id)
    payer = db.get(Client, rec.client_id)
    days = (charge.vencimento - hoje()).days
    if not payer or payer.is_deleted or payer.status != ClientStatus.ACTIVE or payer.forma_cobranca != 'pix_automatico':
        charge.status = 'PENDENCIA'
        charge.pendencia = 'Pagador indisponível ou forma de cobrança alterada; envio interrompido.'
        db.commit()
        return
    if days < 2:
        charge.status = 'PENDENCIA'
        charge.pendencia = 'Janela de envio encerrada: agendamento exige antecedência de 2 a 10 dias.'
        db.commit()
        return
    if days > 10:
        charge.proxima_tentativa = datetime.combine(charge.vencimento - timedelta(days=10), time.min, tzinfo=BRASILIA_TZ)
        db.commit()
        return
    consultar_recorrencia(db, rec)  # Confirmar autorização atual antes de cada envio.
    if rec.status != 'APROVADA':
        charge.status = 'AGUARDANDO_AUTORIZACAO' if rec.ativa else 'CANCELADA'
        charge.proxima_tentativa = agora() + timedelta(minutes=5)
        db.commit()
        return
    exigir_escrita()
    from app.services import titulo_bancario
    billing = lock_billings_for_update(db, [charge.billing_id])[0]
    charge = db.scalar(select(SicrediPixCobranca).where(SicrediPixCobranca.id == charge.id)
                       .execution_options(populate_existing=True).with_for_update())
    if charge.enviada_em or charge.status not in ('NA_FILA', 'AGUARDANDO_AUTORIZACAO'):
        db.rollback()
        return
    if (billing.is_deleted or billing.status not in (BillingStatus.PENDING, BillingStatus.OVERDUE)
        or (billing.payer_client_id or billing.client_id) != rec.client_id
        or billing.due_date != charge.vencimento or Decimal(str(billing.amount)) != Decimal(str(charge.valor))):
        charge.status = 'PENDENCIA'
        charge.pendencia = 'Cobrança local mudou; revise o agendamento antes de enviar.'
        db.commit()
        return
    titulo_bancario.exigir(db, titulo_bancario.EMITIR_PIX, [billing.id], pix_cobranca_id=charge.id)
    if not settings.sicredi_pix_agencia or not settings.sicredi_pix_conta:
        raise PixError('Conta recebedora da API Pix não configurada.')
    if Decimal(str(charge.valor)) < Decimal(str(rec.valor_minimo)):
        charge.status = 'PENDENCIA'
        charge.pendencia = 'Valor da cobrança inferior ao mínimo da autorização.'
        db.commit()
        return
    charge.status = 'ENVIANDO'
    charge.enviada_em = agora()  # Mesmo timeout posterior não autoriza outro PUT.
    charge.tentativas += 1
    db.commit()
    put_accepted = False
    try:
        with PixClient(escrita=True) as api:
            api.request('PUT', '/api/v1/cobr/' + charge.txid, json={
                'idRec': rec.id_rec,
                'infoAdicional': (billing.title or 'Mensalidades e serviços MasterSat')[:140],
                'calendario': {'dataDeVencimento': charge.vencimento.isoformat()},
                'valor': {'original': f'{charge.valor:.2f}'},
                # Pix pode liquidar na data acordada, inclusive em fins de semana.
                # Não deslocar a liquidação sem recalcular a janela de envio.
                'ajusteDiaUtil': False,
                'recebedor': {'agencia': settings.sicredi_pix_agencia,
                              'conta': settings.sicredi_pix_conta, 'tipoConta': 'CORRENTE'},
            })
            put_accepted = True
            consultar_cobranca(db, charge, api=api)
    except PixError as exc:
        db.rollback()
        charge = db.get(SicrediPixCobranca, charge.id)
        if not put_accepted and not isinstance(exc, PixResultadoIncerto) and exc.status_code in (400, 401, 403, 422):
            charge.status = 'REJEITADA'
        else:
            charge.status = 'DESFECHO_DESCONHECIDO'  # Nunca reenviar após consulta inconclusiva.
        charge.erro = erro_seguro(str(exc))
        db.commit()
        raise


def processar_fila(db: Session, *, limite: int = 100) -> dict:
    if not settings.sicredi_pix_enabled:
        return {'consultadas': 0, 'processadas': 0, 'falhas': 0}
    result = {'consultadas': 0, 'processadas': 0, 'falhas': 0}
    recs = db.scalars(select(SicrediPixRecorrencia).where(
        SicrediPixRecorrencia.ambiente == ambiente(),
        or_(SicrediPixRecorrencia.ativa.is_(True), SicrediPixRecorrencia.processar_evento.is_(True)),
    ).order_by(SicrediPixRecorrencia.consultada_em.asc().nulls_first(), SicrediPixRecorrencia.id).limit(limite)).all()
    for rec in recs:
        if not rec.id_rec and rec.status != 'DESFECHO_DESCONHECIDO':
            continue
        try:
            consultar_recorrencia(db, rec)
            result['consultadas'] += 1
        except PixError as exc:
            db.rollback()
            rec.erro = erro_seguro(str(exc))
            rec.consultada_em = agora()
            db.commit()
            result['falhas'] += 1
    charges = db.scalars(select(SicrediPixCobranca).join(SicrediPixRecorrencia).where(
        SicrediPixRecorrencia.ambiente == ambiente(),
        or_(SicrediPixCobranca.status.not_in(COB_TERMINAIS | {'PENDENCIA'}), SicrediPixCobranca.processar_evento.is_(True),
            and_(SicrediPixCobranca.status == 'CONCLUIDA', SicrediPixCobranca.pendencia.is_not(None))),
        or_(SicrediPixCobranca.enviada_em.is_not(None), settings.sicredi_pix_escrita_habilitada),
        or_(SicrediPixCobranca.proxima_tentativa.is_(None), SicrediPixCobranca.proxima_tentativa <= agora(),
            SicrediPixCobranca.processar_evento.is_(True)),
    ).order_by(SicrediPixCobranca.proxima_tentativa.asc().nulls_first(), SicrediPixCobranca.id).limit(limite)).all()
    for charge in charges:
        try:
            # Em leitura, só acompanhar o que já foi enviado. Nunca liberar fila implicitamente.
            if charge.enviada_em or settings.sicredi_pix_escrita_habilitada:
                processar_cobranca(db, charge)
                result['processadas'] += 1
        except (PixError, PoliticaBancariaError) as exc:
            db.rollback()
            charge.erro = erro_seguro(str(exc))
            charge.proxima_tentativa = agora() + timedelta(minutes=5)
            db.commit()
            result['falhas'] += 1
    return result


def receber_aviso(db: Session, payload: dict) -> int:
    """Webhook é um aviso durável: situação/valor recebidos NUNCA dão baixa diretamente."""
    count = 0
    for key in ('recs', 'cobsr', 'cobrs', 'pix'):
        entries = payload.get(key) or []
        if not isinstance(entries, list) or len(entries) > 100:
            raise PixError('Quantidade ou formato de avisos Pix inválido.')
        for item in entries:
            if not isinstance(item, dict):
                continue
            if key == 'recs' and ID_REC.fullmatch(str(item.get('idRec', ''))):
                rec = db.scalar(select(SicrediPixRecorrencia).where(
                    SicrediPixRecorrencia.id_rec == item['idRec'], SicrediPixRecorrencia.ambiente == ambiente()))
                if rec:
                    rec.processar_evento = True
                    count += 1
            elif TXID.fullmatch(str(item.get('txid', ''))):
                charge = db.scalar(select(SicrediPixCobranca).join(SicrediPixRecorrencia).where(
                    SicrediPixCobranca.txid == item['txid'], SicrediPixRecorrencia.ambiente == ambiente()))
                if charge:
                    charge.processar_evento = True
                    count += 1
    db.commit()
    return count
