"""Convite ao pagador para autorizar o Pix Automático (e-mail).

O convite reaproveita o QR/Copia e Cola oficial já devolvido pelo Sicredi para a
autorização existente. Nunca cria outra recorrência, não envia CPF/CNPJ, idRec,
txid ou segredos, e o envio é registrado com reserva prévia: um resultado SMTP
incerto vira ``desconhecido`` e não é repetido automaticamente.

WhatsApp: o sistema só abre a conversa (wa.me) no navegador do operador. Isso
não é registrado como envio porque o MasterSat não confirma a entrega.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from io import BytesIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.client import Client
from app.models.sicredi_pix import SicrediPixRecorrencia
from app.services.sicredi_pix_client import PixError

CANAL_EMAIL = 'email'
PENDENTE = 'pendente'          # pedido registrado; QR ainda não disponível ou aguardando intervalo do SMTP
ENVIANDO = 'enviando'          # reservado; transmissão em andamento
ENVIADO = 'enviado'
ERRO = 'erro'                  # nada foi transmitido (configuração, destinatário, situação)
DESCONHECIDO = 'desconhecido'  # transmissão iniciada sem confirmação: conferir antes de reenviar
DISPENSADO = 'dispensado'      # autorização deixou de aguardar o cliente antes do envio
ENVIO_INTERROMPIDO = timedelta(minutes=15)


class ConviteError(PixError):
    """Convite recusado antes de qualquer transmissão."""


def agora() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def smtp_configurado(db: Session) -> bool:
    from app.services.email_smtp import load_config
    cfg = load_config(db)
    return bool(cfg['host'] and cfg['from_email'])


def destinatarios(db: Session, client_id: int) -> list[str]:
    from app.services.email_smtp import emails_cadastrados
    payer = db.get(Client, client_id)
    return emails_cadastrados(payer) if payer and not payer.is_deleted else []


def qr_png(value: str, *, escala: int = 8, margem: int = 4) -> bytes:
    """PNG do mesmo código Copia e Cola (clientes de e-mail não exibem SVG)."""
    from PIL import Image, ImageDraw
    from reportlab.graphics.barcode.qr import QrCodeWidget
    widget = QrCodeWidget(value, barLevel='M')
    widget.getBounds()  # força a montagem da matriz
    matriz = widget.qr
    n = matriz.getModuleCount()
    lado = (n + 2 * margem) * escala
    imagem = Image.new('1', (lado, lado), 1)
    desenho = ImageDraw.Draw(imagem)
    for linha in range(n):
        for coluna in range(n):
            if matriz.isDark(linha, coluna):
                x, y = (coluna + margem) * escala, (linha + margem) * escala
                desenho.rectangle((x, y, x + escala - 1, y + escala - 1), fill=0)
    saida = BytesIO()
    imagem.save(saida, format='PNG')
    return saida.getvalue()


def montar_mensagem(payer: Client, rec: SicrediPixRecorrencia) -> tuple[str, str, str, list[tuple[str, bytes, str]]]:
    nome = (payer.name or '').strip()
    inicio = rec.data_inicial.strftime('%d/%m/%Y')
    codigo = rec.pix_copia_cola or ''
    assunto = 'MasterSat — autorize o Pix Automático no aplicativo do seu banco'
    corpo = (
        f'Olá, {nome}.\n\n'
        'A MasterSat passará a cobrar suas mensalidades e serviços por Pix Automático. '
        'Para isso, precisamos da sua autorização no aplicativo do seu banco.\n\n'
        'Como autorizar:\n'
        '1. Abra o aplicativo do seu banco e acesse a área Pix.\n'
        '2. Leia o QR Code anexo ou use a opção "Pix Copia e Cola" com o código abaixo.\n'
        '3. Confira os dados da MasterSat e confirme a autorização.\n\n'
        f'Código Pix Copia e Cola:\n{codigo}\n\n'
        f'Periodicidade: mensal. Autorização válida a partir de {inicio}.\n'
        'Autorizar não gera pagamento imediato. Cada débito acontece somente na data de vencimento '
        'da respectiva cobrança, e você pode consultar ou cancelar a autorização no aplicativo do banco.\n\n'
        'Se você não reconhece este pedido, não autorize e entre em contato com a MasterSat.\n'
    )
    html = (
        '<div style="font-family:Arial,Helvetica,sans-serif;color:#0f172a;max-width:560px">'
        f'<p>Olá, {escape(nome)}.</p>'
        '<p>A MasterSat passará a cobrar suas mensalidades e serviços por <strong>Pix Automático</strong>. '
        'Para isso, precisamos da sua autorização no aplicativo do seu banco.</p>'
        '<ol><li>Abra o aplicativo do seu banco e acesse a área Pix.</li>'
        '<li>Leia o QR Code anexo (autorizacao-pix-automatico.png) ou use a opção <em>Pix Copia e Cola</em> '
        'com o código abaixo.</li>'
        '<li>Confira os dados da MasterSat e confirme a autorização.</li></ol>'
        '<p style="font-size:12px">Código Pix Copia e Cola:<br>'
        f'<code style="word-break:break-all">{escape(codigo)}</code></p>'
        f'<p>Periodicidade: mensal. Autorização válida a partir de <strong>{inicio}</strong>.</p>'
        '<p>Autorizar não gera pagamento imediato. Cada débito acontece somente na data de vencimento da '
        'respectiva cobrança, e você pode consultar ou cancelar a autorização no aplicativo do banco.</p>'
        '<p style="font-size:12px;color:#475569">Se você não reconhece este pedido, não autorize e entre em '
        'contato com a MasterSat.</p></div>'
    )
    return assunto, corpo, html, [('autorizacao-pix-automatico.png', qr_png(codigo), 'image/png')]


def _reservar(db: Session, rec_id: int, *, manual: bool) -> tuple[SicrediPixRecorrencia, list[str]] | None:
    rec = db.scalar(select(SicrediPixRecorrencia).where(SicrediPixRecorrencia.id == rec_id)
                    .with_for_update().execution_options(populate_existing=True))
    if rec is None:
        raise ConviteError('Autorização Pix não encontrada.')
    if rec.convite_status == ENVIANDO:
        iniciado = _utc(rec.convite_iniciado_em)
        if iniciado and agora() - iniciado < ENVIO_INTERROMPIDO:
            db.rollback()
            if manual:
                raise ConviteError('Já existe um envio deste convite em andamento.')
            return None
        # Queda no meio da transmissão: não presumir que o SMTP recusou.
        rec.convite_status = DESCONHECIDO
        rec.convite_erro = 'Envio interrompido; confira a caixa do cliente antes de reenviar.'
        db.commit()
        if not manual:
            return None
        rec = db.scalar(select(SicrediPixRecorrencia).where(SicrediPixRecorrencia.id == rec_id)
                        .with_for_update().execution_options(populate_existing=True))
    if not manual and rec.convite_status != PENDENTE:
        db.rollback()
        return None
    if not (rec.ativa and rec.status == 'CRIADA' and rec.pix_copia_cola):
        if rec.convite_status == PENDENTE and (not rec.ativa or rec.status == 'APROVADA'):
            rec.convite_status = DISPENSADO
            rec.convite_erro = 'A autorização não está mais aguardando o cliente.'
            db.commit()
        else:
            db.rollback()
        if manual:
            raise ConviteError('O convite só pode ser enviado enquanto a autorização aguarda o cliente e o QR está disponível.')
        return None
    emails = destinatarios(db, rec.client_id)
    motivo = None
    if not emails:
        motivo = 'Responsável financeiro sem e-mail cadastrado.'
    elif not smtp_configurado(db):
        motivo = 'SMTP não configurado para envio por e-mail.'
    if motivo:
        rec.convite_canal = CANAL_EMAIL
        rec.convite_status = ERRO
        rec.convite_erro = motivo
        db.commit()
        if manual:
            raise ConviteError(motivo)
        return None
    rec.convite_canal = CANAL_EMAIL
    rec.convite_status = ENVIANDO
    rec.convite_iniciado_em = agora()
    rec.convite_tentativas = (rec.convite_tentativas or 0) + 1
    rec.convite_destino = ', '.join(emails)
    rec.convite_erro = None
    db.commit()  # Reserva durável antes do SMTP; libera a trava da linha.
    return rec, emails


def enviar_convite(db: Session, rec_id: int, *, manual: bool = False) -> SicrediPixRecorrencia | None:
    """Envia o convite por e-mail.

    ``manual=False`` (fluxo do cadastro e worker): só envia um convite
    ``pendente``. ``manual=True`` (botão Reenviar): envia de novo usando a mesma
    autorização. Devolve a autorização atualizada ou ``None`` quando nada foi
    tentado; ``manual`` levanta ``ConviteError`` quando recusado.
    """
    from app.services.email_smtp import EmailConfigError, EmailRateLimitError, enviar_email
    reserva = _reservar(db, rec_id, manual=manual)
    if reserva is None:
        return None
    rec, emails = reserva
    payer = db.get(Client, rec.client_id)
    transmitindo = False
    try:
        assunto, corpo, html, anexos = montar_mensagem(payer, rec)
        transmitindo = True
        enviar_email(db, destinatario=', '.join(emails), assunto=assunto, corpo=corpo, html=html, anexos=anexos)
    except EmailRateLimitError as exc:
        rec.convite_status = PENDENTE  # nada transmitido; o worker retoma após o intervalo
        rec.convite_erro = str(exc)
        rec.convite_tentativas = max(0, (rec.convite_tentativas or 1) - 1)  # não houve tentativa real
    except EmailConfigError as exc:
        rec.convite_status = ERRO
        rec.convite_erro = str(exc)
    except Exception as exc:  # noqa: BLE001 — registrar e nunca repetir às cegas
        rec.convite_status = DESCONHECIDO if transmitindo else ERRO
        detalhe = str(exc)[:400]
        rec.convite_erro = (f'Resultado do envio incerto: {detalhe}. Confira antes de reenviar.' if transmitindo
                            else f'Falha ao preparar o convite: {detalhe}')
    else:
        rec.convite_status = ENVIADO
        rec.convite_enviado_em = agora()
        rec.convite_erro = None
    db.commit()
    if manual and rec.convite_status in (ERRO, PENDENTE):
        raise ConviteError(rec.convite_erro or 'Convite não enviado.')
    return rec


def processar_convites(db: Session, ambiente: str, *, limite: int = 20) -> dict:
    """Worker: envia convites pedidos no cadastro cujo QR ficou disponível depois."""
    result = {'enviados': 0, 'falhas': 0}
    ids = db.scalars(select(SicrediPixRecorrencia.id).where(
        SicrediPixRecorrencia.ambiente == ambiente,
        SicrediPixRecorrencia.convite_canal == CANAL_EMAIL,
        SicrediPixRecorrencia.convite_status.in_((PENDENTE, ENVIANDO)),
    ).order_by(SicrediPixRecorrencia.id).limit(limite)).all()
    db.rollback()
    for rec_id in ids:
        try:
            rec = enviar_convite(db, rec_id)
        except PixError:
            db.rollback()
            result['falhas'] += 1
            continue
        if rec is None:
            continue
        if rec.convite_status == ENVIADO:
            result['enviados'] += 1
        elif rec.convite_status in (ERRO, DESCONHECIDO):
            result['falhas'] += 1
        # PENDENTE (limite do SMTP) não é falha: será retomado na próxima rodada.
    return result
