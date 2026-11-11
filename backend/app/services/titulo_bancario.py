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

Dois bancos emitem pela API (Ailos e Sicredi). Uma cobrança tem no máximo um
título ativo: os dois registros são lidos aqui e a emissão em qualquer banco
exige ``sem_titulo`` — por isso uma cobrança com boleto no Sicredi não é
emitida na Ailos, e vice-versa.
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
from app.models.sicredi_boleto import (
    STATUS_ERRO_REGISTRO as SICREDI_ERRO_REGISTRO,
    STATUS_REGISTRADO as SICREDI_REGISTRADO,
    STATUS_REGISTRANDO as SICREDI_REGISTRANDO,
    STATUS_NA_FILA as SICREDI_NA_FILA,
    SicrediBoleto,
)
from app.models.sicredi_pix import SicrediPixCobranca

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
EMITIR_SICREDI = 'emitir_sicredi'
EMITIR_CNAB = 'emitir_cnab'
EMITIR_PIX = 'emitir_pix'

CANAL_AILOS = 'ailos_api'
CANAL_SICREDI = 'sicredi_api'
CANAL_CNAB = 'cnab'
CANAL_PIX = 'sicredi_pix'

# Estados em que cada operação é permitida. ``registrado`` em CANCELAR exige
# confirmação explícita (o título continua pagável até a baixa no banco).
_PERMITIDO: dict[str, frozenset[str]] = {
    ALTERAR_VALOR: frozenset({SEM_TITULO}),
    RECEBER: frozenset({SEM_TITULO, REGISTRADO, BAIXADO, REMESSA_CNAB}),
    CANCELAR: frozenset({SEM_TITULO, REGISTRADO, BAIXADO, REMESSA_CNAB}),
    # Exclusão da carteira de cobrança cancelada: o título e a conciliação
    # permanecem preservados, inclusive quando a baixa no banco está pendente.
    EXCLUIR: frozenset({SEM_TITULO, BAIXADO, REGISTRADO, EM_REGISTRO, DESFECHO_DESCONHECIDO, REMESSA_CNAB}),
    LIBERAR_COMPETENCIA: frozenset({SEM_TITULO, BAIXADO, REGISTRADO}),
    REABRIR: frozenset({SEM_TITULO, BAIXADO}),
    EMITIR_AILOS: frozenset({SEM_TITULO}),
    EMITIR_SICREDI: frozenset({SEM_TITULO}),
    EMITIR_CNAB: frozenset({SEM_TITULO}),
    EMITIR_PIX: frozenset({SEM_TITULO}),
}
_EXIGE_CONFIRMACAO = {
    CANCELAR: frozenset({REGISTRADO, REMESSA_CNAB}),
    LIBERAR_COMPETENCIA: frozenset({REGISTRADO}),
}
# Emissão em banco (qualquer canal) — recusada para cobrança só do sistema.
_EMISSAO_BANCARIA = frozenset({EMITIR_AILOS, EMITIR_SICREDI, EMITIR_CNAB, EMITIR_PIX})

# Quando a mesma cobrança aparece em mais de um registro (não deveria), vale o
# estado mais restritivo.
_GRAVIDADE = {
    SEM_TITULO: 0, BAIXADO: 1, REMESSA_CNAB: 2, REGISTRADO: 3, EM_REGISTRO: 4, DESFECHO_DESCONHECIDO: 5,
}


def gravidade(estado: str) -> int:
    return _GRAVIDADE[estado]


def site_do_banco(canal: str | None) -> str:
    """Onde o operador dá a baixa manual do título cancelado no MasterSat."""
    return 'site do Sicredi' if canal == CANAL_SICREDI else 'site da Ailos'


@dataclass(frozen=True)
class TituloBancario:
    billing_id: int
    estado: str
    canal: str | None = None            # 'ailos_api' | 'sicredi_api' | 'cnab'
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


def reserva_orfa_sicredi(boleto: SicrediBoleto, referencia: datetime | None = None) -> bool:
    iniciado = _aware(boleto.registro_iniciado_em) or _aware(boleto.updated_at) or _aware(boleto.created_at)
    if iniciado is None:
        return True
    limite = timedelta(minutes=settings.sicredi_reserva_orfa_minutos)
    return (referencia or agora()) - iniciado > limite


def estado_do_sicredi(boleto: SicrediBoleto | None, referencia: datetime | None = None) -> str:
    if boleto is None:
        return SEM_TITULO
    if boleto.status == SICREDI_NA_FILA:
        return EM_REGISTRO
    if boleto.status == SICREDI_REGISTRADO and boleto.linha_digitavel and boleto.codigo_barras:
        return BAIXADO if boleto.baixa_status == 'confirmada' else REGISTRADO
    if boleto.status == SICREDI_REGISTRANDO:
        return DESFECHO_DESCONHECIDO if reserva_orfa_sicredi(boleto, referencia) else EM_REGISTRO
    if boleto.status == SICREDI_ERRO_REGISTRO:
        return SEM_TITULO
    # DESFECHO_DESCONHECIDO, ou REGISTRADO sem os dados oficiais.
    return DESFECHO_DESCONHECIDO


def estado_do_pix(charge: SicrediPixCobranca) -> str:
    if charge.status in ('CANCELADA', 'REJEITADA', 'EXPIRADA', 'CONCLUIDA'):
        return BAIXADO if charge.enviada_em else SEM_TITULO
    if charge.status == 'DESFECHO_DESCONHECIDO':
        return DESFECHO_DESCONHECIDO
    if charge.status in ('CRIADA', 'ATIVA'):
        return REGISTRADO
    return EM_REGISTRO


def titulos_bancarios(db: Session, billing_ids: Iterable[int], *, lote_sicredi_id: int | None = None,
                     pix_cobranca_id: int | None = None) -> dict[int, TituloBancario]:
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
            canal=CANAL_AILOS if estado != SEM_TITULO else None,
            nosso_numero=boleto.nosso_numero,
            baixa_status=boleto.baixa_status,
            pendencia=boleto.pendencia,
        )
    for boleto in db.scalars(select(SicrediBoleto).where(SicrediBoleto.billing_id.in_(ids))).all():
        if lote_sicredi_id is not None and boleto.status == SICREDI_NA_FILA and boleto.lote_id == lote_sicredi_id:
            # Só o processador do lote pode assumir a própria reserva ainda
            # não enviada; todas as outras situações/bancos continuam valendo.
            continue
        estado = estado_do_sicredi(boleto, referencia)
        if _GRAVIDADE[estado] > _GRAVIDADE[resultado[boleto.billing_id].estado]:
            resultado[boleto.billing_id] = TituloBancario(
                billing_id=boleto.billing_id,
                estado=estado,
                canal=CANAL_SICREDI,
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
                billing_id=item.billing_id, estado=REMESSA_CNAB, canal=CANAL_CNAB,
                nosso_numero=item.nosso_numero,
            )
    for charge in db.scalars(select(SicrediPixCobranca).where(SicrediPixCobranca.billing_id.in_(ids))).all():
        if charge.id == pix_cobranca_id:
            continue
        estado = estado_do_pix(charge)
        if _GRAVIDADE[estado] > _GRAVIDADE[resultado[charge.billing_id].estado]:
            resultado[charge.billing_id] = TituloBancario(billing_id=charge.billing_id, estado=estado,
                canal=CANAL_PIX, nosso_numero=charge.txid, pendencia=charge.pendencia)
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
        'mudam e ela não pode ser emitida de novo; lance uma nova cobrança.',
    ),
    REMESSA_CNAB: (
        'titulo_em_remessa_cnab',
        'Esta cobrança está numa remessa CNAB reservada. Descarte a remessa (se ela não foi '
        'enviada ao banco) antes de alterar, remover ou emitir por outro canal.',
    ),
}


# Mesmos códigos de _MENSAGENS (contrato com o frontend, ex.: o 409
# boleto_ailos_registrado abre a confirmação de cancelamento); só o texto
# muda, porque o Sicredi aceita baixa e consulta pela API.
_MENSAGENS_SICREDI = {
    EM_REGISTRO: (
        'boleto_ailos_em_registro',
        'Há um pedido de registro no Sicredi em andamento para esta cobrança. Aguarde a '
        'resposta antes de alterar, receber, cancelar ou remover.',
    ),
    DESFECHO_DESCONHECIDO: (
        'boleto_ailos_desfecho_desconhecido',
        'O pedido de registro foi enviado ao Sicredi e não houve resposta conclusiva: o boleto '
        'pode existir no banco. Nada pode ser alterado nem emitido de novo até a consulta '
        'confirmar o resultado (botão "Consultar no Sicredi").',
    ),
    REGISTRADO: (
        'boleto_ailos_registrado',
        'Há boleto registrado e ativo no Sicredi para esta cobrança. Valor e vencimento não '
        'mudam e a cobrança não pode ser removida nem emitida de novo: cancele (o título fica '
        'com baixa pendente) e use "Dar baixa no Sicredi".',
    ),
    BAIXADO: (
        'boleto_ailos_baixado',
        'Esta cobrança tem histórico de boleto no Sicredi (baixado). Valor e vencimento não '
        'mudam e ela não pode ser emitida de novo; lance uma nova cobrança.',
    ),
}


def _mensagem(estado: str, canal: str | None) -> tuple[str, str]:
    if canal == CANAL_PIX:
        return ('pix_automatico_ativo', 'Esta cobrança tem agendamento Pix Automático reservado ou enviado. '
                'Consulte e cancele o agendamento Pix antes de alterar, receber ou emitir por outro canal.')
    if canal == CANAL_SICREDI and estado in _MENSAGENS_SICREDI:
        return _MENSAGENS_SICREDI[estado]
    return _MENSAGENS[estado]


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
    lote_sicredi_id: int | None = None,
    pix_cobranca_id: int | None = None,
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
        if operacao != EMITIR_PIX:
            pix_ids = list(db.scalars(select(Billing.id).where(
                Billing.id.in_(billing_ids), Billing.payment_method == 'pix_automatico')).all())
            if pix_ids:
                raise PoliticaBancariaError('cobranca_pix_automatico',
                    'Cobrança destinada ao Pix Automático. Confira a autorização e o agendamento '
                    'no cadastro do responsável financeiro antes de escolher outro meio de pagamento.', pix_ids)
    titulos = titulos_bancarios(db, billing_ids, lote_sicredi_id=lote_sicredi_id if operacao == EMITIR_SICREDI else None,
                              pix_cobranca_id=pix_cobranca_id if operacao == EMITIR_PIX else None)
    return validar_titulos(operacao, titulos, confirmado=confirmado, permitir_em_registro=permitir_em_registro)


def validar_titulos(
    operacao: str,
    titulos: dict[int, TituloBancario],
    *,
    confirmado: bool = False,
    permitir_em_registro: Iterable[int] = (),
) -> dict[int, TituloBancario]:
    """Mesma política para uma situação já carregada sob trava da cobrança."""
    permitidos = _PERMITIDO[operacao]
    retomadas = set(permitir_em_registro)
    bloqueados: dict[str, list[int]] = {}
    for bid, titulo in titulos.items():
        if (titulo.canal == CANAL_PIX and titulo.ativo_no_banco
                and operacao in (CANCELAR, RECEBER, LIBERAR_COMPETENCIA)):
            code, message = _mensagem(titulo.estado, titulo.canal)
            raise PoliticaBancariaError(code, message, [bid], estado=titulo.estado)
        if (titulo.estado == EM_REGISTRO and bid in retomadas
                and operacao == EMITIR_AILOS and titulo.canal == CANAL_AILOS):
            # Exceção histórica para a parcela de lote Ailos, restrita ao mesmo
            # banco. Não autoriza um segundo canal nem um POST Sicredi concorrente.
            continue
        if titulo.estado not in permitidos:
            bloqueados.setdefault(titulo.estado, []).append(bid)
    for estado in (DESFECHO_DESCONHECIDO, EM_REGISTRO, REGISTRADO, REMESSA_CNAB, BAIXADO):
        if estado in bloqueados:
            ids_bloqueados = sorted(bloqueados[estado])
            canal = titulos[ids_bloqueados[0]].canal
            code, message = _mensagem(estado, canal)
            raise PoliticaBancariaError(code, message, ids_bloqueados, estado=estado)
    precisa_confirmar = _EXIGE_CONFIRMACAO.get(operacao, frozenset())
    sem_confirmacao = sorted(bid for bid, t in titulos.items() if t.estado in precisa_confirmar)
    if sem_confirmacao and not confirmado:
        titulo = titulos[sem_confirmacao[0]]
        if operacao == LIBERAR_COMPETENCIA:
            raise PoliticaBancariaError(
                'confirmacao_baixa_origem',
                f'O mês será liberado no MasterSat. Providencie a baixa do boleto no {site_do_banco(titulo.canal)}. '
                'A liberação no sistema não cancela o título no banco. Confirmar?',
                sem_confirmacao, estado=titulo.estado,
            )
        if titulo.canal == CANAL_SICREDI:
            raise PoliticaBancariaError(
                'boleto_ailos_registrado',
                f'Há boleto registrado no Sicredi (nosso número {titulo.nosso_numero or "—"}). O '
                'cancelamento interrompe a cobrança no sistema e desativa o link público, mas o '
                'título continua pagável até a baixa. Providencie a baixa no site do Sicredi. '
                'O cancelamento no MasterSat não cancela o boleto no banco. Cancelar mesmo assim?',
                sem_confirmacao,
                estado=titulo.estado,
            )
        raise PoliticaBancariaError(
            'boleto_ailos_registrado',
            f'Há boleto registrado no banco (nosso número {titulo.nosso_numero or "—"}). O '
            'cancelamento interrompe a cobrança no sistema e desativa o link público, mas o '
            'título continua pagável até a baixa. Providencie a baixa no site da Ailos — o '
            'convênio não oferece baixa pela API. O cancelamento no MasterSat não cancela o '
            'boleto no banco; a baixa fica registrada como pendente e a conciliação continua '
            'acompanhando o título. Cancelar mesmo assim?',
            sem_confirmacao,
            estado=titulo.estado,
        )
    return titulos


def marcar_baixa_pendente(db: Session, billing_ids: Iterable[int], motivo: str) -> list[dict]:
    """A cobrança local deixou de valer, mas o título segue pagável no banco.

    Estrutural (não só nota): enquanto a baixa não é confirmada o título
    continua na conciliação e a tela de pendências lista o nosso número.
    A liberação local da competência não confirma baixa bancária.
    Devolve ``[{billing_id, nosso_numero}]``.
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
    for boleto in db.scalars(
        select(SicrediBoleto).where(SicrediBoleto.billing_id.in_(ids)).order_by(SicrediBoleto.billing_id)
    ).all():
        if not (boleto.status == SICREDI_REGISTRADO and boleto.linha_digitavel and boleto.codigo_barras):
            continue
        if boleto.baixa_status == 'confirmada':
            continue
        if boleto.baixa_status is None:
            # 'solicitada' já está a caminho do banco: não volta para pendente.
            boleto.baixa_status = 'pendente'
            boleto.baixa_solicitada_em = momento
            boleto.baixa_observacao = motivo
        ativos.append({'billing_id': boleto.billing_id, 'nosso_numero': boleto.nosso_numero})
    return ativos


def confirmar_baixa(
    boleto: AilosBoleto | SicrediBoleto, *, origem: str, user_id: int | None = None, observacao: str | None = None,
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


def registrar_pendencia(boleto: AilosBoleto | SicrediBoleto, codigo: str, detalhe: dict) -> bool:
    """Grava divergência para revisão humana. Devolve True se é nova."""
    nova = boleto.pendencia != codigo
    boleto.pendencia = codigo
    boleto.pendencia_detalhe = detalhe
    if nova:
        boleto.pendencia_desde = agora()
    return nova
