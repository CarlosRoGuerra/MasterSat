from datetime import date, datetime

from pydantic import BaseModel, Field


# ── Autenticação / status ──────────────────────────────────────────────────

class AilosStatusOut(BaseModel):
    client_token_configured: bool
    cooperado_status: str
    numero_convenio: str | None = None
    authorized_at: datetime | None = None
    last_refresh_at: datetime | None = None
    last_error: str | None = None


class AilosConnectOut(BaseModel):
    login_url: str
    state: str


class AilosCallbackIn(BaseModel):
    state: str
    code: str


# ── Boletos ─────────────────────────────────────────────────────────────────

class AilosGerarBoletoIn(BaseModel):
    billing_id: int


class AilosGerarLoteIn(BaseModel):
    billing_ids: list[int] = Field(min_length=1)


class AilosBoletoOut(BaseModel):
    id: int
    billing_id: int
    lote_id: int | None = None
    numero_convenio: str
    numero_documento: str | None = None
    nosso_numero: str | None = None
    identificador_unico_titulo: str | None = None
    linha_digitavel: str | None = None
    codigo_barras: str | None = None
    valor_nominal: float | None = None
    data_vencimento: date | None = None
    status_ailos: str | None = None
    pix_emv: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {'from_attributes': True}


class AilosPagamentoOut(BaseModel):
    billing_id: int
    consultado: bool
    pago: bool
    mensagem: str
    data_pagamento: date | None = None
    valor_pago: float | None = None


class AilosLoteOut(BaseModel):
    id: int
    tipo: str
    ticket: str
    numero_convenio: str
    billing_ids: list[int]
    status: str
    created_at: datetime

    model_config = {'from_attributes': True}


class AilosLoteStatusOut(BaseModel):
    ticket: str
    status: str
    boletos: list[AilosBoletoOut] | None = None
    # Progresso estruturado — a tela de acompanhamento do carnê usa isto para
    # mostrar "3 de 12 confirmadas" mesmo enquanto status ainda é 'processing',
    # em vez de um spinner sem informação nenhuma.
    total: int = 0
    prontas: int = 0


# ── Pagadores ───────────────────────────────────────────────────────────────

class AilosPagadorIn(BaseModel):
    client_id: int


class AilosPagadorOut(BaseModel):
    data: dict | list | None = None


# ── Arquivo de retorno ───────────────────────────────────────────────────────

class AilosRetornoSolicitarIn(BaseModel):
    data_movimento: date


class AilosRetornoArquivoOut(BaseModel):
    id: int
    numero_convenio: str
    data_movimento: date
    ticket: str | None = None
    status: str
    storage_object_key: str | None = None
    requested_at: datetime
    downloaded_at: datetime | None = None

    model_config = {'from_attributes': True}


class AilosRetornoListarOut(BaseModel):
    data: dict | list | None = None
