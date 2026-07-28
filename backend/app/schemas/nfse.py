from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


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


# ── Emissão em lote ─────────────────────────────────────────────────────────

class LoteEmitirIn(BaseModel):
    """Confirmação de emissão em massa (etapa 'Confirmar e Emitir')."""

    period_label: str = Field(..., description="Lote de fechamento, ex.: '07/2026'")
    billing_ids: list[int] = Field(..., min_length=1, description='Cobranças selecionadas')
    competencia: date | None = None
    codigo_servico: str | None = None
    discriminacao: str | None = None


class LoteResumo(BaseModel):
    """Visão geral de um lote de emissão (listagem)."""

    id: int
    period_label: str
    competencia: str | None = None
    codigo_servico: str | None = None
    discriminacao: str | None = None
    status: str
    total_notas: int
    total_autorizadas: int
    total_erro: int
    criado_em: str | None = None
    concluido_em: str | None = None


class LoteNotaItem(BaseModel):
    """Linha do drill-down: status individual de uma nota do lote."""

    nota_id: int
    billing_id: int
    tomador: str
    cpf_cnpj: str | None = None
    valor: float
    numero_nfse: str | None = None
    status: str
    chave_acesso: str | None = None
    link_visualizacao: str | None = None
    erro_codigo: str | None = None
    erro_mensagem: str | None = None


class LoteDetalhe(LoteResumo):
    itens: list[LoteNotaItem] = []


class ElegivelItem(BaseModel):
    billing_id: int
    client_id: int
    tomador: str
    cpf_cnpj: str | None = None
    tipo: str | None = None
    valor: float
    titulo: str | None = None
    reprocessamento: bool = False


class ElegiveisOut(BaseModel):
    period_label: str
    total_elegiveis: int
    ja_emitidas: int
    itens: list[ElegivelItem] = []


class NotaListItem(BaseModel):
    """Linha da listagem geral de notas (tela "Notas")."""

    nota_id: int
    billing_id: int
    lote_id: int | None = None
    tomador: str
    cpf_cnpj: str | None = None
    valor: float
    nosso_numero: str | None = None
    numero_nfse: str | None = None
    status: str
    chave_acesso: str | None = None
    link_visualizacao: str | None = None
    erro_codigo: str | None = None
    erro_mensagem: str | None = None
    tem_xml: bool = False
    data_ocorrencia: str | None = None


class NotasOut(BaseModel):
    total: int
    limit: int
    offset: int
    itens: list[NotaListItem] = []


class CertificadoOut(BaseModel):
    """Certificado A1 cadastrado — nunca inclui o arquivo nem a senha."""

    id: int
    titular: str
    cnpj: str | None = None
    emissor: str | None = None
    nome_arquivo: str | None = None
    valido_de: str | None = None
    valido_ate: str | None = None
    dias_para_vencer: int | None = None
    vencido: bool = False
    ativo: bool = True
    enviado_em: str | None = None


class ResumoOut(BaseModel):
    """Balanço do mês para o painel."""

    competencia: str
    autorizadas: int
    negadas: int
    processando: int
    total: int
    total_geral: int
