"""Reserva transacional e fencing das chamadas fiscais, compartilhados pelos providers.

Uma lease vencida permite consultar o documento, nunca reenviá-lo. Nenhum erro
de transporte prova rejeição e erros legados não são evidência de envio seguro.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import logging
import os
import socket
import threading
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.nfse_nota import NfseNota

logger = logging.getLogger(__name__)
EMISSOR = f'{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:12]}'
ERROS_REPROCESSAVEIS = ('local', 'rejeicao')
HEARTBEAT_INTERVAL_SECONDS = 20
IDENTIDADE = ('provedor', 'ambiente', 'numero_rps', 'serie_rps', 'prestador_cnpj',
              'prestador_im', 'codigo_municipio', 'dps_id')


def agora(db: Session) -> datetime:
    # PostgreSQL clock_timestamp não fica congelado no início da transação.
    clock = func.clock_timestamp() if db.get_bind().dialect.name == 'postgresql' else func.current_timestamp()
    value = db.scalar(select(clock))
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def _duracao() -> timedelta:
    return timedelta(seconds=max(120, settings.nfse_timeout_seconds * 3))


def lease_ativa(nota: NfseNota, instante: datetime) -> bool:
    return bool(nota.lease_expires_at and _utc(nota.lease_expires_at) > instante)


def _nota(db, billing_id):
    return db.scalar(select(NfseNota).where(NfseNota.billing_id == billing_id)
                     .with_for_update().execution_options(populate_existing=True))


def pode_emitir(nota: NfseNota) -> bool:
    return (nota.status == 'pending' and (not nota.tentativa_numero or
            nota.erro_tipo in ERROS_REPROCESSAVEIS and nota.tentativa_id is None
            and nota.envio_iniciado_em is None and (nota.emissor_id or '').startswith('lote:'))
            or nota.status == 'erro' and nota.erro_tipo in ERROS_REPROCESSAVEIS)


def arquivar_tentativa(nota: NfseNota) -> list:
    historico = list(nota.tentativas_anteriores or [])
    if nota.tentativa_id and nota.tentativa_numero:
        snapshot = {key: getattr(nota, key) for key in (
            *IDENTIDADE, 'tentativa_id', 'tentativa_numero', 'emissor_id', 'lote_id',
            'numero_lote', 'protocolo', 'xml_envio', 'xml_retorno',
            'erro_tipo', 'erro_codigo', 'erro_mensagem', 'discriminacao', 'codigo_servico')}
        for key in ('competencia', 'envio_iniciado_em', 'heartbeat_at'):
            value = getattr(nota, key)
            snapshot[key] = value.isoformat() if value else None
        historico.append(snapshot)
    return historico


def reservar(db: Session, billing_id: int, *, lote_id=None, **campos):
    """Retorna (nota, token exclusivo ou None). Commit acontece antes do POST."""
    nota = _nota(db, billing_id)
    criada = False
    if nota is None:
        try:
            with db.begin_nested():
                nota = NfseNota(billing_id=billing_id, status='pending', tentativa_numero=0)
                db.add(nota)
                db.flush()
                criada = True
        except IntegrityError:
            # O UNIQUE só resolve a criação: releia e revalide o vencedor.
            nota = _nota(db, billing_id)
    instante = agora(db)
    erro_revisavel = nota.status == 'erro' and nota.erro_tipo in ERROS_REPROCESSAVEIS
    if nota.status == 'emitida' or (nota.lote_id is not None and nota.lote_id != lote_id and not erro_revisavel):
        db.commit()
        return nota, None
    expirada = nota.lease_expires_at and not lease_ativa(nota, instante)
    legado = not criada and nota.status == 'pending' and not nota.emissor_id
    if expirada or legado:
        nota.status = 'desconhecido'
        nota.erro_tipo = 'desconhecido'
        nota.tentativa_id = str(uuid.uuid4())
        nota.lease_expires_at = None
        db.commit()
        return nota, None
    if not pode_emitir(nota):
        if nota.status == 'erro' or (nota.lease_expires_at and not lease_ativa(nota, instante)):
            nota.status = 'desconhecido'
            nota.erro_tipo = 'desconhecido'
            nota.tentativa_id = str(uuid.uuid4())  # invalida respostas do emissor anterior
            nota.lease_expires_at = None
        db.commit()
        return nota, None
    nota.tentativas_anteriores = arquivar_tentativa(nota)
    for key, value in campos.items():
        # Trocar série/ambiente não pode contornar uma rejeição. O provider
        # valida esta identidade contra a configuração antes de montar XML.
        if nota.tentativa_numero and key in IDENTIDADE and getattr(nota, key) is not None:
            continue
        # O payload aprovado no lote prevalece até a primeira tentativa.
        if key in ('competencia', 'discriminacao', 'codigo_servico') and nota.lote_id and getattr(nota, key) is not None:
            continue
        setattr(nota, key, value)
    nota.tentativa_numero += 1
    nota.tentativa_id = str(uuid.uuid4())
    nota.emissor_id = EMISSOR
    nota.status = 'processing'
    nota.heartbeat_at = instante
    nota.lease_expires_at = instante + _duracao()
    nota.envio_iniciado_em = None
    nota.erro_tipo = nota.erro_codigo = nota.erro_mensagem = None
    token = nota.tentativa_id
    db.commit()
    return nota, token


def validar_identidade(nota, **esperada):
    if any(getattr(nota, key) != value for key, value in esperada.items()):
        raise ValueError('Configuração fiscal difere da tentativa original; restaure provedor, ambiente e série antes de revisar a emissão.')


def atualizar(db: Session, nota_id: int, token: str, **campos) -> NfseNota:
    """CAS: resposta atrasada jamais altera outra tentativa ou nota autorizada."""
    if campos.get('status') in ('emitida', 'erro', 'desconhecido'):
        campos['lease_expires_at'] = None
    db.execute(update(NfseNota).where(
        NfseNota.id == nota_id, NfseNota.tentativa_id == token,
        NfseNota.status != 'emitida',
    ).values(**campos).execution_options(synchronize_session=False))
    db.commit()
    nota = db.get(NfseNota, nota_id)
    db.refresh(nota)
    return nota


def marcar_envio(db, nota_id, token, xml, **campos) -> bool:
    instante = agora(db)
    result = db.execute(update(NfseNota).where(
        NfseNota.id == nota_id, NfseNota.tentativa_id == token,
        NfseNota.status == 'processing', NfseNota.envio_iniciado_em.is_(None),
        NfseNota.lease_expires_at > instante,
    ).values(xml_envio=xml, envio_iniciado_em=instante, heartbeat_at=instante,
             lease_expires_at=instante + _duracao(), **campos)
      .execution_options(synchronize_session=False))
    db.commit()
    return result.rowcount == 1


def recuperar_expiradas(db: Session) -> list[int]:
    """Somente leases vencidas; notas novas/ativas de outro worker ficam intactas."""
    instante = agora(db)
    ids = list(db.scalars(select(NfseNota.id).where(
        NfseNota.status.in_(('pending', 'processing', 'desconhecido')),
        NfseNota.lease_expires_at <= instante)))
    recuperadas = []
    for nota_id in ids:
        result = db.execute(update(NfseNota).where(
            NfseNota.id == nota_id, NfseNota.status != 'emitida',
            NfseNota.lease_expires_at <= instante,
        ).values(status='desconhecido', erro_tipo='desconhecido',
                 erro_mensagem='Lease expirada; consulte DPS/RPS antes de qualquer decisão de reenvio.',
                 tentativa_id=str(uuid.uuid4()), lease_expires_at=None)
          .execution_options(synchronize_session=False))
        if result.rowcount:
            recuperadas.append(nota_id)
    db.commit()
    db.expire_all()
    return recuperadas


def reservar_consulta(db: Session, nota: NfseNota) -> str | None:
    nota = _nota(db, nota.billing_id)
    instante = agora(db)
    if nota.status == 'emitida' or lease_ativa(nota, instante):
        db.commit()
        return None
    nota.tentativa_id = str(uuid.uuid4())
    nota.emissor_id = EMISSOR
    nota.heartbeat_at = instante
    nota.lease_expires_at = instante + _duracao()
    token = nota.tentativa_id
    db.commit()
    return token


@contextmanager
def heartbeat(db: Session, nota_id: int, token: str):
    """Renova a lease em sessão independente enquanto a chamada de rede bloqueia."""
    stopped = threading.Event()
    bind = db.get_bind()

    def renew():
        while not stopped.wait(HEARTBEAT_INTERVAL_SECONDS):
            try:
                with Session(bind=bind) as session:
                    instante = agora(session)
                    changed = session.execute(update(NfseNota).where(
                        NfseNota.id == nota_id, NfseNota.tentativa_id == token,
                        NfseNota.status != 'emitida', NfseNota.lease_expires_at > instante,
                    ).values(heartbeat_at=instante, lease_expires_at=instante + _duracao()))
                    session.commit()
                    if changed.rowcount != 1:
                        return
            except Exception:
                logger.exception('Falha ao renovar lease fiscal da nota %s', nota_id)
                return

    worker = threading.Thread(target=renew, name=f'nfse-lease-{nota_id}', daemon=True)
    worker.start()
    try:
        yield
    finally:
        stopped.set()
        worker.join(timeout=2)
