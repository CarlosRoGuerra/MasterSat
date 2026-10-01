"""Proveniência e controle da migração SGR → MasterSat (Fase 04).

Antes desta fase o importador só conhecia chaves naturais mutáveis (CPF,
placa, IMEI, nosso número + título + valor): não sabia de onde viera cada
registro, reexecutar não atualizava nada, desconto do boleto sumia e o PDF
que falhava não era tentado de novo. Estas tabelas guardam o que falta:

* ``sgr_vinculos`` — identidade de origem de cliente, veículo, rastreador,
  contrato (vínculo) e plano. Uma chave de origem aponta para UM registro
  local; nunca é inventada quando a origem não traz o código.
* ``sgr_documentos`` / ``sgr_documento_linhas`` — o boleto consolidado do SGR
  e cada linha da discriminação, separados das obrigações (``billings``). A
  linha de desconto fica registrada com a alocação que reduziu a obrigação;
  documento cuja soma não fecha com o total da origem não libera cobrança.
* ``sgr_execucoes`` / ``sgr_execucao_unidades`` — cada rodada gravada, com
  checkpoint por cliente (unidade) e manifesto de completude.
* ``sgr_conflitos`` — divergência entre origem e MasterSat que exige decisão
  humana (transferência de veículo, pagamento local × origem, valor alterado).
* ``sgr_arquivos`` — outbox dos PDFs de boleto e XMLs de NFS-e: o download é
  reconciliado à parte da criação da cobrança, com chave de objeto estável.

Política completa em docs/migracao-sgr/politica-conflitos.md.
"""
from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin

SGR_ENTIDADES = ('cliente', 'veiculo', 'rastreador', 'contrato', 'plano')
SGR_EXECUCAO_STATUS = ('em_andamento', 'concluida', 'concluida_com_pendencias', 'incompleta', 'falhou')
SGR_UNIDADE_STATUS = ('aplicada', 'bloqueada', 'falhou', 'reaproveitada')
SGR_DOCUMENTO_STATUS = ('conciliado', 'bloqueado')
# obrigacao   — virou (ou virará) uma cobrança;
# desconto    — valor negativo alocado em obrigações do mesmo documento;
# bonificada  — obrigação zerada por desconto: cobrança CANCELADA ocupando o mês;
# descartada  — reemissão cancelada de um mês que já tem o boleto que valeu.
SGR_LINHA_TIPOS = ('obrigacao', 'desconto', 'bonificada', 'descartada')
SGR_CONFLITO_STATUS = ('aberto', 'resolvido')
SGR_ARQUIVO_TIPOS = ('boleto_pdf', 'nfse_xml')
SGR_ARQUIVO_STATUS = ('pendente', 'baixado', 'falhou', 'bloqueado', 'sem_url')


def _in(coluna: str, valores: tuple[str, ...]) -> str:
    return f"{coluna} IN ({', '.join(repr(v) for v in valores)})"


class SgrExecucao(Base, TimestampMixin):
    """Uma rodada de importação gravada (--apply). Simulação não grava nada."""

    __tablename__ = 'sgr_execucoes'
    __table_args__ = (
        CheckConstraint(_in('status', SGR_EXECUCAO_STATUS), name='ck_sgr_execucoes_status'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(30), default='em_andamento')
    # Parâmetros da coleta (limite, período, flags). Nunca credencial.
    parametros: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    retoma_execucao_id: Mapped[int | None] = mapped_column(
        ForeignKey('sgr_execucoes.id', name='fk_sgr_execucoes_retoma'), nullable=True,
    )
    versao_codigo: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Completude por endpoint/escopo, contagens e resultado da reconciliação.
    manifesto: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    concluida_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SgrExecucaoUnidade(Base, TimestampMixin):
    """Checkpoint: uma unidade (cliente do SGR, ou os planos) por execução.

    Cada unidade é confirmada numa transação curta. ``hash_origem`` permite a
    retomada pular o que já foi aplicado com o mesmo dado de origem.
    """

    __tablename__ = 'sgr_execucao_unidades'
    __table_args__ = (
        Index('uq_sgr_execucao_unidades', 'execucao_id', 'unidade', unique=True),
        CheckConstraint(_in('status', SGR_UNIDADE_STATUS), name='ck_sgr_execucao_unidades_status'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    execucao_id: Mapped[int] = mapped_column(ForeignKey('sgr_execucoes.id'), index=True)
    unidade: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20))
    hash_origem: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resumo: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)


class SgrVinculo(Base, TimestampMixin):
    """Identidade de origem → registro local (cliente, veículo, rastreador,
    contrato, plano).

    ``criado_por``: ``importacao`` (o importador criou o registro) ou
    ``adocao`` (o registro já existia pela chave natural e passou nas
    verificações de dono — nunca por inferência ambígua).
    """

    __tablename__ = 'sgr_vinculos'
    __table_args__ = (
        Index('uq_sgr_vinculos_origem', 'entidade', 'chave_origem', unique=True),
        # Um registro local não pode ser duas entidades de origem ao mesmo
        # tempo. Plano fica de fora: grupos de mensalidade e de adesão com a
        # mesma descrição já caem no mesmo plano (chave natural = nome).
        Index(
            'uq_sgr_vinculos_local', 'entidade', 'local_id', unique=True,
            postgresql_where=text("entidade <> 'plano'"),
            sqlite_where=text("entidade <> 'plano'"),
        ),
        CheckConstraint(_in('entidade', SGR_ENTIDADES), name='ck_sgr_vinculos_entidade'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entidade: Mapped[str] = mapped_column(String(20))
    chave_origem: Mapped[str] = mapped_column(String(120))
    local_id: Mapped[int] = mapped_column(Integer)
    hash_origem: Mapped[str | None] = mapped_column(String(64), nullable=True)
    criado_por: Mapped[str] = mapped_column(String(20), default='importacao')
    primeira_execucao_id: Mapped[int | None] = mapped_column(
        ForeignKey('sgr_execucoes.id', name='fk_sgr_vinculos_primeira'), nullable=True,
    )
    ultima_execucao_id: Mapped[int | None] = mapped_column(
        ForeignKey('sgr_execucoes.id', name='fk_sgr_vinculos_ultima'), nullable=True,
    )


class SgrDocumento(Base, TimestampMixin):
    """Boleto consolidado do SGR (``cod_boleto``), separado das obrigações.

    ``total_origem_centavos`` é o ``valor`` do documento na origem; o
    documento só fica ``conciliado`` (e só então gera cobranças) quando a soma
    das linhas bate com ele e todo desconto foi alocado. ``snapshot_origem`` é
    o estado normalizado aplicado por último: a próxima rodada compara com ele
    para decidir o que mudou na origem.
    """

    __tablename__ = 'sgr_documentos'
    __table_args__ = (
        Index('uq_sgr_documentos_cod_boleto', 'cod_boleto', unique=True),
        CheckConstraint(_in('status', SGR_DOCUMENTO_STATUS), name='ck_sgr_documentos_status'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    cod_boleto: Mapped[str] = mapped_column(String(40))
    client_id: Mapped[int | None] = mapped_column(ForeignKey('clients.id'), nullable=True, index=True)
    cod_cliente: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    motivo_bloqueio: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    detalhe_bloqueio: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    situacao_origem: Mapped[str | None] = mapped_column(String(40), nullable=True)
    total_origem_centavos: Mapped[int | None] = mapped_column(Integer, nullable=True)
    descontos_centavos: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    hash_origem: Mapped[str | None] = mapped_column(String(64), nullable=True)
    snapshot_origem: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 'importacao' ou 'backfill' (reconstruído do sgr_payload preservado).
    criado_por: Mapped[str] = mapped_column(String(20), default='importacao')
    ultima_execucao_id: Mapped[int | None] = mapped_column(
        ForeignKey('sgr_execucoes.id', name='fk_sgr_documentos_execucao'), nullable=True,
    )


class SgrDocumentoLinha(Base, TimestampMixin):
    """Uma linha da discriminação do documento (valor com sinal).

    ``chave_linha`` = ``{cod_boleto}:{placa}:{produto}:{mês}:{ocorrência}`` —
    não usa o valor, para que uma mudança de valor na origem apareça como
    alteração (e conflito), não como linha nova.
    """

    __tablename__ = 'sgr_documento_linhas'
    __table_args__ = (
        Index('uq_sgr_documento_linhas_chave', 'chave_linha', unique=True),
        CheckConstraint(_in('tipo', SGR_LINHA_TIPOS), name='ck_sgr_documento_linhas_tipo'),
        CheckConstraint(
            "(tipo = 'desconto') = (valor_origem_centavos < 0)",
            name='ck_sgr_documento_linhas_desconto_negativo',
        ),
        Index(
            'uq_sgr_documento_linhas_billing', 'billing_id', unique=True,
            postgresql_where=text('billing_id IS NOT NULL'),
            sqlite_where=text('billing_id IS NOT NULL'),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    documento_id: Mapped[int] = mapped_column(ForeignKey('sgr_documentos.id'), index=True)
    chave_linha: Mapped[str] = mapped_column(String(160))
    placa: Mapped[str | None] = mapped_column(String(20), nullable=True)
    produto: Mapped[str | None] = mapped_column(String(120), nullable=True)
    mes_referente: Mapped[str | None] = mapped_column(String(20), nullable=True)
    tipo: Mapped[str] = mapped_column(String(20))
    valor_origem_centavos: Mapped[int] = mapped_column(Integer)
    # Obrigação: quanto de desconto recebeu. Desconto: em quais linhas foi
    # alocado ([{chave_linha, centavos}]).
    desconto_alocado_centavos: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    alocacao: Mapped[list | None] = mapped_column(JSON, nullable=True)
    billing_id: Mapped[int | None] = mapped_column(ForeignKey('billings.id'), nullable=True)
    # Impressão digital da cobrança quando o importador a gravou/atualizou por
    # último. Diferente agora = alguém mexeu no MasterSat (pagamento local,
    # negociação, edição) e a origem não pode sobrescrever.
    hash_local: Mapped[str | None] = mapped_column(String(64), nullable=True)


class SgrConflito(Base, TimestampMixin):
    """Divergência origem × MasterSat que não se resolve sozinha."""

    __tablename__ = 'sgr_conflitos'
    __table_args__ = (
        CheckConstraint(_in('status', SGR_CONFLITO_STATUS), name='ck_sgr_conflitos_status'),
        # Um conflito aberto por assunto: a rodada seguinte atualiza o mesmo.
        Index(
            'uq_sgr_conflitos_aberto', 'entidade', 'chave_origem', 'tipo', unique=True,
            postgresql_where=text("status = 'aberto'"),
            sqlite_where=text("status = 'aberto'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    execucao_id: Mapped[int | None] = mapped_column(ForeignKey('sgr_execucoes.id'), nullable=True, index=True)
    entidade: Mapped[str] = mapped_column(String(20))
    chave_origem: Mapped[str] = mapped_column(String(160))
    tipo: Mapped[str] = mapped_column(String(40), index=True)
    tabela_local: Mapped[str | None] = mapped_column(String(40), nullable=True)
    local_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Só ids, códigos de origem, status e centavos — nunca CPF/nome/telefone.
    detalhe: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    hash_origem: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default='aberto', index=True)
    resolucao: Mapped[str | None] = mapped_column(String(40), nullable=True)
    resolvido_por: Mapped[str | None] = mapped_column(String(120), nullable=True)
    justificativa: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolvido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SgrArquivo(Base, TimestampMixin):
    """Outbox de download (PDF do boleto, XML da NFS-e).

    O pedido é gravado na mesma transação da cobrança; o download roda
    depois, fora dela, e grava o objeto numa chave ESTÁVEL
    (``object_key``). Se o banco falhar depois do upload, a próxima tentativa
    regrava o mesmo objeto e cria o documento — sem objeto órfão com UUID novo.
    """

    __tablename__ = 'sgr_arquivos'
    __table_args__ = (
        Index('uq_sgr_arquivos_origem', 'tipo', 'chave_origem', unique=True),
        CheckConstraint(_in('tipo', SGR_ARQUIVO_TIPOS), name='ck_sgr_arquivos_tipo'),
        CheckConstraint(_in('status', SGR_ARQUIVO_STATUS), name='ck_sgr_arquivos_status'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tipo: Mapped[str] = mapped_column(String(20))
    chave_origem: Mapped[str] = mapped_column(String(120))
    client_id: Mapped[int] = mapped_column(ForeignKey('clients.id'), index=True)
    # URL devolvida pela origem (pode carregar token): nunca vai para relatório.
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default='pendente', index=True)
    tentativas: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    ultimo_erro: Mapped[str | None] = mapped_column(String(255), nullable=True)
    object_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey('documents.id'), nullable=True)
    tamanho_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
