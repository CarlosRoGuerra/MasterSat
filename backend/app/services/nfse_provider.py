"""
Ponto único de escolha do provedor de NFS-e, por ``NFSE_PROVEDOR``.

  - 'nacional'  → Emissor Nacional (Sefin Nacional). Padrão desde 20/07/2026.
  - 'joinville' → webservice municipal legado (só consulta de notas antigas).

Endpoints e o serviço de lote importam daqui em vez de decidir cada um por si.
"""
from __future__ import annotations

from app.core.config import settings
from app.services import nfse_joinville, nfse_nacional

# Erros dos dois provedores, para os handlers tratarem de forma uniforme.
ErrosConfig = (nfse_joinville.NfseError, nfse_nacional.NfseError)
ErrosApi = (nfse_joinville.NfseApiError, nfse_nacional.NfseApiError)


def modulo():
    """Retorna o módulo do provedor ativo."""
    return nfse_joinville if settings.nfse_provedor == 'joinville' else nfse_nacional


def emitir_nfse(db, billing, client, cod_trib_nacional=None):
    return modulo().emitir_nfse(db, billing, client, cod_trib_nacional=cod_trib_nacional)
