"""
Ponto único de escolha do provedor de NFS-e, por ``NFSE_PROVEDOR``.

  - 'nacional'  → Emissor Nacional (Sefin Nacional). Padrão desde 20/07/2026.
  - 'joinville' → webservice municipal legado (só consulta de notas antigas).

Endpoints e o serviço de lote importam daqui em vez de decidir cada um por si.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.config import settings
from app.services import nfse_joinville, nfse_nacional

# Erros dos dois provedores, para os handlers tratarem de forma uniforme.
ErrosConfig = (nfse_joinville.NfseError, nfse_nacional.NfseError)
ErrosApi = (nfse_joinville.NfseApiError, nfse_nacional.NfseApiError)


def modulo():
    """Retorna o módulo do provedor ativo."""
    return nfse_joinville if settings.nfse_provedor == 'joinville' else nfse_nacional


def descricao_fechamento(db, billing) -> str | None:
    """Discrimina os itens do título único no campo de serviço da NFS-e."""
    if not (billing.title or '').startswith('Fechamento '):
        return None
    from app.api.v1.endpoints.boletos import _itens_detalhados, _periodo_servico_fechamento

    items = _itens_detalhados(billing, db)
    total = sum((Decimal(str(value)) for _, value in items), Decimal('0.00'))
    if total != Decimal(str(billing.amount)):
        raise nfse_nacional.NfseError(
            'Itens do fechamento divergem do valor do título. Corrija antes de emitir a NFS-e.'
        )
    period = _periodo_servico_fechamento(billing) or billing.period_label or ''
    description = f'Serviços de rastreamento - competência {period}. ' + ' | '.join(
        f'{label}: R$ {format(Decimal(str(value)), ".2f").replace(".", ",")}'
        for label, value in items
    )
    if len(description) > 2000:
        raise nfse_nacional.NfseError(
            'A discriminação do fechamento excede 2000 caracteres. '
            'Revise os itens antes de emitir a NFS-e.'
        )
    return description


def emitir_nfse(db, billing, client, cod_trib_nacional=None, *, competencia=None,
                discriminacao=None, lote_id=None):
    if discriminacao is None:
        discriminacao = descricao_fechamento(db, billing)
    return modulo().emitir_nfse(db, billing, client, cod_trib_nacional=cod_trib_nacional,
                               competencia=competencia, discriminacao=discriminacao, lote_id=lote_id)


def consultar_nfse(db, nota):
    """A identidade persistida determina a consulta, nunca NFSE_PROVEDOR atual."""
    if nota.status == 'emitida':
        return nota
    if nota.provedor == 'nacional':
        return nfse_nacional.consultar(db, nota)
    if nota.provedor == 'joinville':
        return nfse_joinville.consultar(db, nota)
    raise nfse_nacional.NfseError('Provedor legado não identificado; reconciliação manual necessária.')
