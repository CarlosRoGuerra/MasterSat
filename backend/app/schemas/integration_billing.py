"""Contrato de leitura de cobranças para a integração de notificações."""
from datetime import date, datetime

from pydantic import BaseModel

from app.models.enums import BillingStatus


class IntegrationPayerOut(BaseModel):
    id: int
    nome: str
    cpf_cnpj: str | None
    telefone: str | None
    email: str | None


class IntegrationNfseOut(BaseModel):
    nota_id: int
    billing_id: int
    status: str
    numero_nfse: str | None
    serie_nfse: str | None
    codigo_verificacao: str | None
    chave_acesso: str | None
    link_visualizacao: str | None
    data_emissao: datetime | None
    competencia: date | None
    ambiente: str | None
    pdf_disponivel: bool
    xml_disponivel: bool
    motivo_indisponibilidade: str | None
    pdf_url: str | None
    xml_url: str | None


class IntegrationBillingOut(BaseModel):
    id: int
    titulo: str | None
    cliente: IntegrationPayerOut
    valor: float
    vencimento: date | None
    status: BillingStatus
    pagamento_confirmado: bool
    data_pagamento: date | None
    valor_pago: float | None
    forma_pagamento: str | None
    forma_envio: str
    enviar_boleto_whatsapp: bool
    nosso_numero: str | None
    linha_digitavel: str | None
    codigo_barras: str | None
    pix_copia_cola: str | None
    boleto_registrado: bool
    boleto_disponivel: bool
    motivo_boleto_indisponivel: str | None
    boleto_pdf_url: str | None
    boleto_link_cliente: str | None
    valor_com_juros: float | None
    nfse: IntegrationNfseOut | None = None


class IntegrationBillingPage(BaseModel):
    # Mantém a semântica da versão 1.0: total é o tamanho desta página.
    total: int
    total_registros: int
    limit: int
    offset: int
    has_more: bool
    next_offset: int | None
    cobrancas: list[IntegrationBillingOut]
