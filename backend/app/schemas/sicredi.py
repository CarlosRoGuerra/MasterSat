from datetime import date, datetime

from pydantic import BaseModel, Field


class SicrediStatusOut(BaseModel):
    habilitado: bool
    ambiente: str
    producao_confirmada: bool
    cooperativa: str | None = None
    posto: str | None = None
    codigo_beneficiario: str | None = None
    hibrido: bool
    erro_configuracao: str | None = None


class SicrediConexaoOut(BaseModel):
    ok: bool
    ambiente: str
    cooperativa: str
    posto: str
    codigo_beneficiario: str


class SicrediEmitirIn(BaseModel):
    billing_id: int
    # Retomada de um registro com desfecho desconhecido: consulta antes de reenviar.
    nova_tentativa: bool = False


class SicrediBaixaIn(BaseModel):
    motivo: str = Field(min_length=3, max_length=500)


class SicrediBoletoOut(BaseModel):
    billing_id: int
    estado: str
    status: str
    ambiente: str
    situacao: str | None = None
    nosso_numero: str
    seu_numero: str | None = None
    tipo_cobranca: str
    linha_digitavel: str | None = None
    codigo_barras: str | None = None
    txid: str | None = None
    qr_code: str | None = None
    valor_nominal: float | None = None
    data_vencimento: date | None = None
    erro: str | None = None
    baixa_status: str | None = None
    baixa_enviada_em: datetime | None = None
    baixa_confirmada_em: datetime | None = None
    pendencia: str | None = None
    ultima_consulta_em: datetime | None = None
    data_liquidacao: date | None = None
    valor_liquidado: float | None = None


class SicrediConsultaOut(BaseModel):
    billing_id: int
    consultado: bool
    situacao: str | None = None
    pago: bool
    data_pagamento: date | None = None
    valor_pago: float | None = None
    mensagem: str
    pendencia: str | None = None
    quitada: bool = False
    desfecho: str | None = None


class SicrediBaixaOut(BaseModel):
    billing_id: int
    baixa_status: str
    transaction_id: str | None = None
    mensagem: str


class SicrediConciliacaoOut(BaseModel):
    desfechos_resolvidos: int
    liquidados_no_banco: int
    consultados: int
    quitados: int
    pendencias: int
    falhas: int
    erro_liquidados: str | None = None
