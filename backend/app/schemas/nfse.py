from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class NfseOut(BaseModel):
    """Status/dados da NFS-e de uma cobrança."""

    model_config = ConfigDict(from_attributes=True)

    billing_id: int
    status: str
    numero_rps: str | None = None
    serie_rps: str | None = None
    numero_lote: str | None = None
    protocolo: str | None = None
    situacao: str | None = None
    numero_nfse: str | None = None
    serie_nfse: str | None = None
    codigo_verificacao: str | None = None
    chave_acesso: str | None = None
    link_visualizacao: str | None = None
    data_emissao: datetime | None = None
    erro_codigo: str | None = None
    erro_mensagem: str | None = None


class NfseClientItem(NfseOut):
    """Item da listagem de notas por cliente (inclui contexto da cobrança)."""

    valor: float | None = None
    titulo: str | None = None
