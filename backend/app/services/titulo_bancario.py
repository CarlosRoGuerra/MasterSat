"""Situação bancária de uma cobrança e a política única de mutação (Fase 03).

Antes desta fase cada escritor decidia sozinho o que fazer com uma cobrança
que tem boleto: o PUT recusava título registrado, o DELETE só olhava registro
"em andamento", o cancelamento pedia confirmação, a desinstalação de veículo
mudava o valor sem olhar nada. Resultado (FIN-02/FIN-03): título ativo no
banco sem obrigação local, e edição liberada depois de um timeout em que a
Ailos pode ter registrado o boleto.

Aqui fica a regra inteira. ``exigir`` é chamado por TODO escritor antes de
mudar valor, vencimento, situação ou existência de uma cobrança; ele devolve a
situação de cada título ou levanta ``PoliticaBancariaError`` (409 na API).

Estados (``TituloBancario.estado``):

* ``sem_titulo`` — nunca foi ao banco, ou o banco recusou de forma definitiva;
* ``em_registro`` — pedido enviado há pouco, esperando resposta/lote;
* ``desfecho_desconhecido`` — o pedido pode ter chegado e não se sabe o
  resultado (timeout, 5xx, falha local depois do envio, reserva órfã);
* ``registrado`` — título ativo no banco;
* ``baixado`` — título registrado com baixa confirmada (consulta devolveu
  situação 3/5 ou administrador confirmou): não é mais pagável;
* ``remessa_cnab`` — incluído numa remessa CNAB ainda reservada.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.ailos_boleto import AilosBoleto
from app.models.billing import Billing
from app.models.cnab_remessa import CnabRemessaItem

SEM_TITULO = 'sem_titulo'
EM_REGISTRO = 'em_registro'
DESFECHO_DESCONHECIDO = 'desfecho_desconhecido'
REGISTRADO = 'registrado'
BAIXADO = 'baixado'
REMESSA_CNAB = 'remessa_cnab'

# Valores de AilosBoleto.status_ailos gravados por este sistema.
STATUS_EM_REGISTRO = ('REGISTRANDO', 'PROCESSANDO')
STATUS_DESFECHO_DESCONHECIDO = 'DESFECHO_DESCONHECIDO'
STATUS_ERRO_REGISTRO = 'ERRO_REGISTRO'

# Operações que mudam uma cobrança.
ALTERAR_VALOR = 'alterar_valor'          # valor, vencimento, unificar, regravar
RECEBER = 'receber'
CANCELAR = 'cancelar'
EXCLUIR = 'excluir'
LIBERAR_COMPETENCIA = 'liberar_competencia'
REABRIR = 'reabrir'                      # reverter substituição (originais voltam)
EMITIR_AILOS = 'emitir_ailos'
EMITIR_CNAB = 'emitir_cnab'

# Estados em que cada operação é permitida. ``registrado`` em CANCELAR exige
# confirmação explícita (o título continua pagável até a baixa no banco).
_PERMITIDO: dict[str, frozenset[str]] = {
    ALTERAR_VALOR: frozenset({SEM_TITULO}),
    RECEBER: frozenset({SEM_TITULO, REGISTRADO, BAIXADO, REMESSA_CNAB}),
    CANCELAR: frozenset({SEM_TITULO, REGISTRADO, BAIXADO, REMESSA_CNAB}),
    EXCLUIR: frozenset({SEM_TITULO}),
    LIBERAR_COMPETENCIA: frozenset({SEM_TITULO, BAIXADO}),
    REABRIR: frozenset({SEM_TITULO, BAIXADO}),
    EMITIR_AILOS: frozenset({SEM_TITULO}),
    EMITIR_CNAB: frozenset({SEM_TITULO}),
}
_EXIGE_CONFIRMACAO = {CANCELAR: frozenset({REGISTRADO, REMESSA_CNAB})}
# Emissão em banco (qualquer canal) — recusada para cobrança só do sistema.
_EMISSAO_BANCARIA = frozenset({EMITIR_AILOS, EMITIR_CNAB})


@dataclass(frozen=True)
class TituloBancario:
    billing_id: int
    estado: str
    canal: str | None = None            # 'ailos_api' | 'cnab'
    nosso_numero: str | None = None
    baixa_status: str | None = None
    pendencia: str | None = None

    @property
    def ativo_no_banco(self) -> bool:
        """Pode estar pagável no banco (ou não se sabe)."""
        return self.estado in (REGISTRADO, EM_REGISTRO, DESFECHO_DESCONHECIDO, REMESSA_CNAB)

    def as_dict(self) -> dict:
        return {
            'estado': self.estado,
            'canal': self.canal,
            'nosso_numero': self.nosso_numero,
            'baixa_status': self.baixa_status,
            'pendencia': self.pendencia,
        }


class PoliticaBancariaError(Exception):
    """Operação recusada pela situação bancária da cobrança (vira 409)."""

    def __init__(self, code: str, message: str, billing_ids: list[int], *, estado: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.billing_ids = billing_ids
        self.estado = estado

    def detail(self) -> dict:
        detail = {'code': self.code, 'message': self.message, 'billing_ids': self.billing_ids}
        if self.estado:
            detail['estado_bancario'] = self.estado
        return detail


def agora() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def reserva_orfa(boleto: AilosBoleto, referencia: datetime | None = None) -> bool:
    """Reserva de registro sem resposta há mais que o prazo configurado."""
    iniciado = _aware(boleto.registro_iniciado_em) or _aware(boleto.updated_at) or _aware(boleto.created_at)
    if iniciado is None:
        return True
    limite = timedelta(minutes=settings.ailos_reserva_orfa_minutos)
    return (referencia or agora()) - iniciado > limite


def estado_do_boleto(boleto: AilosBoleto | None, referencia: datetime | None = None) -> str:
    if boleto is None:
        return SEM_TITULO
    if boleto.linha_digitavel and boleto.codigo_barras:
        return BAIXADO if boleto.baixa_status == 'confirmada' else REGISTRADO
    status = boleto.status_ailos
    if status in STATUS_EM_REGISTRO:
        return DESFECHO_DESCONHECIDO if reserva_orfa(boleto, referencia) else EM_REGISTRO
    if status == STATUS_ERRO_REGISTRO:
        return SEM_TITULO
    if status is None and not boleto.nosso_numero and not boleto.payload_response:
        # Linha criada sem pedido (não acontece pelo fluxo atual): nada foi enviado.
        return SEM_TITULO
    # DESFECHO_DESCONHECIDO, ou dado parcial do banco sem linha digitável.
    return DESFECHO_DESCONHECIDO


def titulos_bancarios(db: Session, billing_ids: Iterable[int]) -> dict[int, TituloBancario]:
    ids = sorted({int(b) for b in billing_ids if b is not None})
    if not ids:
        return {}
    referencia = agora()
    resultado = {bid: TituloBancario(billing_id=bid, estado=SEM_TITULO) for bid in ids}
    for boleto in db.scalars(select(AilosBoleto).where(AilosBoleto.billing_id.in_(ids))).all():
        estado = estado_do_boleto(boleto, referencia)
        resultado[boleto.billing_id] = TituloBancario(
            billing_id=boleto.billing_id,
            estado=estado,
            canal='ailos_api' if estado != SEM_TITULO else None,
            nosso_numero=boleto.nosso_numero,
            baixa_status=boleto.baixa_status,
            pendencia=boleto.pendencia,
        )
    for item in db.scalars(
        select(CnabRemessaItem).where(
            CnabRemessaItem.billing_id.in_(ids),
            CnabRemessaItem.status == 'reservado',
        )
    ).all():
        if resultado[item.billing_id].estado == SEM_TITULO:
            resultado[item.billing_id] = TituloBancario(
                billing_id=item.billing_id, estado=REMESSA_CNAB, canal='cnab',
                nosso_numero=item.nosso_numero,
            )
    return resultado


_MENSAGENS = {
    EM_REGISTRO: (
        'boleto_ailos_em_registro',
        'Há um pedido de registro na Ailos em andamento para esta cobrança. Aguarde a '
        'resposta (ou a conclusão do lote) antes de alterar, receber, cancelar ou remover.',
    ),
    DESFECHO_DESCONHECIDO: (
        'boleto_ailos_desfecho_desconhecido',
        'O pedido de registro foi enviado à Ailos e não houve resposta conclusiva: o boleto '
        'pode existir no banco. Nada pode ser alterado nem emitido de novo até a consulta '
        'confirmar o resultado (botão "Consultar desfecho" na aba Ailos).',
    ),
    REGISTRADO: (
        'boleto_ailos_registrado',
        'Há boleto registrado e ativo no banco para esta cobrança. Valor e vencimento não '
        'mudam e a cobrança não pode ser removida nem emitida de novo: cancele (o título '
        'fica com baixa pendente) e dê baixa no internet banking da Ailos.',
    ),
    BAIXADO: (
        'boleto_ailos_baixado',
        'Esta cobrança tem histórico de boleto no banco (baixado). Valor e vencimento não '
        'mudam e ela não pode ser removida nem emitida de novo; lance uma nova cobrança.',
    ),
    REMESSA_CNAB: (
        'titulo_em_remessa_cnab',
        'Esta cobrança está numa remessa CNAB reservada. Descarte a remessa (se ela não foi '
        'enviada ao banco) antes de alterar, remover ou emitir por outro canal.',
    ),
}


def recusar_somente_sistema(db: Session, billing_ids: Iterable[int]) -> None:
    """Cobrança marcada para não ir ao banco (carnê simples) não é emitida."""
    ids = sorted({int(b) for b in billing_ids if b is not None})
    if not ids:
        return
    so_sistema = list(db.scalars(
        select(Billing.id).where(Billing.id.in_(ids), Billing.somente_sistema.is_(True)).order_by(Billing.id)
    ).all())
    if so_sistema:
        raise PoliticaBancariaError(
            'cobranca_somente_sistema',
            'Esta cobrança foi gerada somente no sistema (carnê simples) e não é emitida '
            'no banco. Receba pelo "Registrar pagamento"; para cobrar por boleto, '
            'cancele e gere um carnê pela Ailos.',
            so_sistema,
        )


def exigir(
    db: Session,
    operacao: str,
    billing_ids: Iterable[int],
    *,
    confirmado: bool = False,
    permitir_em_registro: Iterable[int] = (),
) -> dict[int, TituloBancario]:
    """Aplica a política de ``operacao`` a cada cobrança; levanta no primeiro
    estado que a proíbe, listando todas as cobranças nesse estado.

    ``confirmado``: o operador aceitou cancelar com título ativo no banco.
    ``permitir_em_registro``: retomada manual de uma reserva em andamento
    (retry individual da parcela de um lote).
    """
    billing_ids = list(billing_ids)
    if operacao in _EMISSAO_BANCARIA:
        recusar_somente_sistema(db, billing_ids)
    titulos = titulos_bancarios(db, billing_ids)
    permitidos = _PERMITIDO[operacao]
    retomadas = set(permitir_em_registro)
    bloqueados: dict[str, list[int]] = {}
    for bid, titulo in titulos.items():
        if titulo.estado == EM_REGISTRO and bid in retomadas:
            continue
        if titulo.estado not in permitidos:
            bloqueados.setdefault(titulo.estado, []).append(bid)
    for estado in (DESFECHO_DESCONHECIDO, EM_REGISTRO, REGISTRADO, REMESSA_CNAB, BAIXADO):
        if estado in bloqueados:
            code, message = _MENSAGENS[estado]
            if operacao == LIBERAR_COMPETENCIA and estado == REGISTRADO:
                code = 'baixa_bancaria_pendente'
                message = (
                    'O boleto desta cobrança continua ativo no banco. Liberar o mês agora '
                    'permitiria cobrar o cliente duas vezes. Dê baixa no internet banking '
                    'da Ailos e confirme a baixa (consulta automática ou "Confirmar baixa").'
                )
            raise PoliticaBancariaError(code, message, sorted(bloqueados[estado]), estado=estado)
    precisa_confirmar = _EXIGE_CONFIRMACAO.get(operacao, frozenset())
    sem_confirmacao = sorted(bid for bid, t in titulos.items() if t.estado in precisa_confirmar)
    if sem_confirmacao and not confirmado:
        titulo = titulos[sem_confirmacao[0]]
        raise PoliticaBancariaError(
            'boleto_ailos_registrado',
            f'Há boleto registrado no banco (nosso número {titulo.nosso_numero or "—"}). O '
            'cancelamento interrompe a cobrança no sistema e desativa o link público, mas o '
            'título continua pagável até a baixa no internet banking da Ailos — o convênio não '
            'oferece baixa pela API. A baixa fica registrada como pendente e a conciliação '
            'continua acompanhando o título. Cancelar mesmo assim?',
            sem_confirmacao,
            estado=titulo.estado,
        )
    return titulos


def marcar_baixa_pendente(db: Session, billing_ids: Iterable[int], motivo: str) -> list[dict]:
    """A cobrança local deixou de valer, mas o título segue pagável no banco.

    Estrutural (não só nota): enquanto a baixa não é confirmada o título
    continua na conciliação, a competência não pode ser liberada e a tela de
    pendências lista o nosso número. Devolve ``[{billing_id, nosso_numero}]``.
    """
    ids = sorted(set(billing_ids))
    if not ids:
        return []
    ativos: list[dict] = []
    momento = agora()
    for boleto in db.scalars(
        select(AilosBoleto).where(AilosBoleto.billing_id.in_(ids)).order_by(AilosBoleto.billing_id)
    ).all():
        if not (boleto.linha_digitavel and boleto.codigo_barras):
            continue
        if boleto.baixa_status == 'confirmada':
            continue
        if boleto.baixa_status != 'pendente':
            boleto.baixa_status = 'pendente'
            boleto.baixa_solicitada_em = momento
            boleto.baixa_observacao = motivo
        ativos.append({'billing_id': boleto.billing_id, 'nosso_numero': boleto.nosso_numero})
    return ativos


def confirmar_baixa(
    boleto: AilosBoleto, *, origem: str, user_id: int | None = None, observacao: str | None = None,
) -> bool:
    """Registra a baixa como confirmada. Devolve False se já estava."""
    if boleto.baixa_status == 'confirmada':
        return False
    boleto.baixa_status = 'confirmada'
    boleto.baixa_confirmada_em = agora()
    boleto.baixa_confirmada_por_user_id = user_id
    texto = f'[{origem}] {observacao}' if observacao else f'[{origem}]'
    boleto.baixa_observacao = f'{boleto.baixa_observacao} | {texto}' if boleto.baixa_observacao else texto
    return True


def registrar_pendencia(boleto: AilosBoleto, codigo: str, detalhe: dict) -> bool:
    """Grava divergência para revisão humana. Devolve True se é nova."""
    nova = boleto.pendencia != codigo
    boleto.pendencia = codigo
    boleto.pendencia_detalhe = detalhe
    if nova:
        boleto.pendencia_desde = agora()
    return nova
