"""
Solicitação/listagem/download de arquivos de retorno (ZIP) via API de
Cobrança Ailos (v1/boletos/.../arquivo/retorno).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.ailos_retorno_arquivo import AilosRetornoArquivo
from app.services import ailos_client
from app.services.ailos_client import AilosError
from app.services.storage import upload_bytes

_PATH_SOLICITAR = '/v1/boletos/solicitar/arquivo/retorno/convenios/{convenio}/{data}'
_PATH_LISTAR = '/v1/boletos/listar/arquivo/retorno/convenios/{convenio}/{inicio}/{fim}'
_PATH_BAIXAR = '/v1/boletos/baixar/arquivo/retorno/convenios/{convenio}/{ticket}'

# A Ailos limita o intervalo da listagem a 30 dias (Cartilha jan/26, p.12).
_MAX_INTERVALO_LISTAGEM = timedelta(days=30)


def solicitar_arquivo_retorno(db: Session, data_movimento: date) -> AilosRetornoArquivo:
    """Solicita à Ailos a geração do arquivo de retorno de uma data de movimento."""
    resp = ailos_client.request(
        db, 'POST',
        _PATH_SOLICITAR.format(convenio=settings.ailos_numero_convenio, data=data_movimento.isoformat()),
    )

    body = resp.json if isinstance(resp.json, dict) else {}
    ticket = body.get('ticket') or body.get('ticketLote')

    row = AilosRetornoArquivo(
        numero_convenio=settings.ailos_numero_convenio,
        data_movimento=data_movimento,
        ticket=ticket,
        status='requested',
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def listar_arquivos_retorno(db: Session, data_inicial: date, data_final: date) -> dict | list | None:
    """Lista os arquivos de retorno disponíveis na Ailos num intervalo de datas."""
    if data_final < data_inicial:
        raise AilosError('Data final anterior à data inicial')
    if data_final - data_inicial > _MAX_INTERVALO_LISTAGEM:
        raise AilosError('Intervalo de listagem excede o máximo de 30 dias permitido pela Ailos')

    resp = ailos_client.request(
        db, 'GET',
        _PATH_LISTAR.format(
            convenio=settings.ailos_numero_convenio,
            inicio=data_inicial.isoformat(),
            fim=data_final.isoformat(),
        ),
    )
    return resp.json


def baixar_arquivo_retorno(db: Session, row: AilosRetornoArquivo) -> AilosRetornoArquivo:
    """
    Baixa o arquivo de retorno (ZIP) de um ``ticket`` já solicitado e o
    armazena no MinIO. Se a Ailos ainda estiver processando, mantém
    ``status='processing'`` sem subir nada ao storage.
    """
    if not row.ticket:
        raise AilosError('Arquivo de retorno sem ticket — solicite novamente via /retorno/solicitar')

    resp = ailos_client.request(
        db, 'GET',
        _PATH_BAIXAR.format(convenio=settings.ailos_numero_convenio, ticket=row.ticket),
        accept_zip=True,
    )

    if resp.processing or 'zip' not in (resp.headers.get('Content-Type') or '').lower():
        row.status = 'processing'
    else:
        object_name = f'ailos/retorno/{row.ticket}.zip'
        upload_bytes(object_name, resp.content, 'application/zip')
        row.storage_object_key = object_name
        row.status = 'downloaded'
        row.downloaded_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(row)
    return row
