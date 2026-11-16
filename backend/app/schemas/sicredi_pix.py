from datetime import date, datetime, timedelta
from decimal import Decimal

from pydantic import BaseModel, Field, computed_field


class PixAutorizacaoIn(BaseModel):
    data_inicial: date
    valor_minimo: Decimal = Field(default=Decimal('0.01'), ge=Decimal('0.01'), max_digits=10, decimal_places=2)
    objeto: str = Field(default='Mensalidades e serviços MasterSat', min_length=1, max_length=35)
    # Pede o envio do QR por e-mail ao responsável financeiro assim que existir.
    enviar_convite: bool = False


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
    created_at: datetime | None = None
    convite_canal: str | None = None
    convite_status: str | None = None
    convite_destino: str | None = None
    convite_tentativas: int = 0
    convite_enviado_em: datetime | None = None
    convite_erro: str | None = None
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

    @computed_field
    @property
    def inicio_janela_envio(self) -> date:
        return self.vencimento - timedelta(days=10)

    @computed_field
    @property
    def fim_janela_envio(self) -> date:
        return self.vencimento - timedelta(days=2)


class PixStatusOut(BaseModel):
    habilitado: bool
    configurado: bool
    ambiente: str
    escrita_habilitada: bool
    erro_configuracao: str | None = None
    smtp_configurado: bool | None = None  # preenchido só por GET /status


class PixClienteOut(BaseModel):
    client_id: int
    forma_cobranca: str | None
    conexao: PixStatusOut
    recorrencia: PixRecorrenciaOut | None
    cobrancas: list[PixCobrancaOut]
    pendentes_adesao: int
    primeiro_vencimento_sugerido: date
    data_minima_autorizacao: date
    smtp_configurado: bool = False
    emails_responsavel: list[str] = []


class PixPainelLinhaOut(BaseModel):
    client_id: int
    cliente: str | None
    telefone: str | None = None
    billing_id: int | None
    cobranca_pix_id: int | None
    vencimento: date | None
    valor: Decimal | None
    situacao: str
    situacao_label: str
    acao: str
    acao_label: str
    gravidade: str
    detalhe: str | None
    convite_status: str | None


class PixPainelResumoOut(BaseModel):
    autorizacoes_pendentes: int
    debitos_nao_realizados: int
    total_aguardando_confirmacao: Decimal
    clientes_a_contatar: int
    falhas_integracao: int
    acoes_necessarias: int


class PixPainelOut(BaseModel):
    ambiente: str
    hoje: date
    habilitado: bool
    escrita_habilitada: bool
    resumo: PixPainelResumoOut
    por_situacao: dict[str, int]
    linhas: list[PixPainelLinhaOut]
