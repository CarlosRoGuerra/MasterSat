from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class PixAutorizacaoIn(BaseModel):
    data_inicial: date
    valor_minimo: Decimal = Field(default=Decimal('0.01'), ge=Decimal('0.01'), max_digits=10, decimal_places=2)
    objeto: str = Field(default='Mensalidades e serviços MasterSat', min_length=1, max_length=35)


class PixRecorrenciaOut(BaseModel):
    id: int
    id_rec: str | None
    status: str
    ativa: bool
    ambiente: str
    data_inicial: date
    objeto: str
    valor_minimo: Decimal
    pix_copia_cola: str | None
    qr_svg: str | None = None
    erro: str | None
    consultada_em: datetime | None
    model_config = {'from_attributes': True}


class PixCobrancaOut(BaseModel):
    id: int
    billing_id: int
    txid: str
    status: str
    vencimento: date
    valor: Decimal
    pendencia: str | None
    erro: str | None
    enviada_em: datetime | None
    model_config = {'from_attributes': True}


class PixStatusOut(BaseModel):
    habilitado: bool
    configurado: bool
    ambiente: str
    escrita_habilitada: bool
    erro_configuracao: str | None = None


class PixClienteOut(BaseModel):
    client_id: int
    forma_cobranca: str | None
    conexao: PixStatusOut
    recorrencia: PixRecorrenciaOut | None
    cobrancas: list[PixCobrancaOut]
    pendentes_adesao: int
