"""
Endpoints da NFS-e.

O provedor é escolhido por ``NFSE_PROVEDOR``:
  - ``nacional``  → Emissor Nacional (Sefin Nacional). Padrão desde 20/07/2026,
    quando Joinville encerrou a emissão municipal.
  - ``joinville`` → webservice municipal legado. Só serve para consultar notas
    antigas; emitir por ele hoje retorna E930.

GET  /nfse/                       → lista notas (filtro por client_id) p/ o painel
POST /nfse/emitir/{billing_id}    → emite a NFS-e da cobrança (ADMIN/FINANCEIRO)
GET  /nfse/{billing_id}           → status/dados da NFS-e da cobrança
POST /nfse/consultar/{billing_id} → reconsulta a nota junto ao provedor

PDF da nota, dois caminhos:
  GET /nfse/{billing_id}/danfse       → DANFSE oficial, baixado do ADN
  GET /nfse/{billing_id}/danfse-local → montado por nós a partir do XML, para
      quando o serviço do governo está fora (ver app/services/nfse_danfse.py)
"""
from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.crypto import CryptoError
from app.db.session import get_db
from app.models.billing import Billing
from app.models.client import Client
from app.models.enums import UserRole
from app.models.nfse_nota import NfseNota
from app.models.user import User
from app.schemas.nfse import (
    CertificadoOut,
    ElegiveisOut,
    LoteDetalhe,
    LoteEmitirIn,
    LoteResumo,
    NfseClientItem,
    NfseOut,
    NotasOut,
    ResumoOut,
)
from app.services import (
    nfse_certificado,
    nfse_danfse,
    nfse_joinville,
    nfse_lote,
    nfse_nacional,
    nfse_provider,
)

router = APIRouter()

ALLOWED_ROLES = (UserRole.ADMIN, UserRole.FINANCIAL)

# Os dois módulos definem NfseError/NfseApiError próprios; o handler trata ambos.
_ERROS_CONFIG = nfse_provider.ErrosConfig
_ERROS_API = nfse_provider.ErrosApi


def _provedor():
    return nfse_provider.modulo()


def _raise_nfse_error(exc: Exception) -> NoReturn:
    if isinstance(exc, _ERROS_API):
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if isinstance(exc, _ERROS_CONFIG):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise exc


@router.get('/', response_model=list[NfseClientItem])
def listar(
    client_id: int | None = None,
    limit: int = Query(default=100, le=300),
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """Lista as NFS-e emitidas/em processamento, com filtro por cliente
    (usado no botão de nota fiscal da grid de clientes)."""
    query = (
        db.query(NfseNota, Billing)
        .join(Billing, Billing.id == NfseNota.billing_id)
        .filter(Billing.is_deleted.is_(False))
    )
    if client_id:
        query = query.filter(Billing.client_id == client_id)
    rows = query.order_by(NfseNota.id.desc()).limit(limit).all()
    return [
        NfseClientItem(
            **NfseOut.model_validate(nota).model_dump(),
            valor=float(billing.amount) if billing.amount is not None else None,
            titulo=billing.title,
        )
        for nota, billing in rows
    ]


@router.post('/emitir/{billing_id}', response_model=NfseOut)
def emitir(
    billing_id: int,
    cod_trib_nacional: str | None = Query(
        default=None, description='Código de tributação nacional (ex.: 110201); vazio usa o padrão'),
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    billing = db.get(Billing, billing_id)
    if billing is None or getattr(billing, 'is_deleted', False):
        raise HTTPException(status_code=404, detail='Cobrança não encontrada')
    client = db.get(Client, billing.client_id)
    if client is None:
        raise HTTPException(status_code=404, detail='Cliente da cobrança não encontrado')
    try:
        return _provedor().emitir_nfse(db, billing, client, cod_trib_nacional=cod_trib_nacional)
    except (*_ERROS_CONFIG, *_ERROS_API) as exc:
        _raise_nfse_error(exc)


# ── Painel e listagem geral ─────────────────────────────────────────────────
# Estas rotas de path fixo precisam vir ANTES de GET /{billing_id} (que espera
# int), senão o path param as sombreia.

@router.get('/resumo', response_model=ResumoOut)
def resumo(
    competencia: str | None = Query(default=None, description='YYYY-MM; vazio = mês corrente'),
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """Balanço do mês (autorizadas × negadas) para o painel."""
    return nfse_lote.resumo(db, competencia)


@router.get('/notas', response_model=NotasOut)
def notas(
    busca: str | None = None,
    situacao: str | None = Query(default=None, pattern='^(emitida|erro|pending|processing)$'),
    period_label: str | None = None,
    limit: int = Query(default=25, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """Listagem paginada de todas as NFS-e (tela "Notas")."""
    return nfse_lote.listar_notas(
        db, busca=busca, situacao=situacao, period_label=period_label,
        limit=limit, offset=offset,
    )


# ── Certificado digital ─────────────────────────────────────────────────────

@router.get('/certificado', response_model=CertificadoOut | None)
def certificado_atual(
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """Certificado A1 ativo (sem expor arquivo nem senha). None = nenhum."""
    return nfse_certificado.para_dict(nfse_certificado.obter_ativo(db))


@router.post('/certificado', response_model=CertificadoOut, status_code=201)
async def certificado_cadastrar(
    arquivo: UploadFile = File(..., description='e-CNPJ A1 (.pfx / .p12)'),
    senha: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """
    Cadastra o certificado. Titular, CNPJ, emissor e validade são LIDOS do
    próprio arquivo — o operador só envia o .pfx e a senha. Só ADMIN.
    """
    conteudo = await arquivo.read()
    if not conteudo:
        raise HTTPException(status_code=422, detail='Arquivo do certificado vazio.')
    if len(conteudo) > 512 * 1024:
        raise HTTPException(status_code=422, detail='Arquivo muito grande para um .pfx (máx. 512 KB).')
    try:
        registro = nfse_certificado.salvar(
            db, conteudo, senha, nome_arquivo=arquivo.filename, enviado_por=user.id,
        )
    except nfse_certificado.CertificadoError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CryptoError as exc:
        # Sem chave de criptografia não dá para guardar o .pfx com segurança.
        raise HTTPException(
            status_code=503,
            detail='Armazenamento seguro indisponível: AILOS_TOKEN_ENCRYPTION_KEY '
                   f'não configurada ou inválida no servidor. ({exc})',
        ) from exc
    return nfse_certificado.para_dict(registro)


# ── Emissão em lote ─────────────────────────────────────────────────────────

@router.get('/lotes/elegiveis', response_model=ElegiveisOut)
def lote_elegiveis(
    period_label: str,
    busca: str | None = None,
    tipo: str | None = Query(default=None, pattern='^(pf|pj)$'),
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """Etapa de conferência: clientes do lote de fechamento com 'Emitir NF = Sim'."""
    return nfse_lote.listar_elegiveis(db, period_label, busca=busca, tipo=tipo)


@router.post('/lotes', response_model=LoteResumo, status_code=201)
def lote_emitir(
    payload: LoteEmitirIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*ALLOWED_ROLES)),
):
    """Confirma a emissão em massa das cobranças selecionadas (assíncrona)."""
    try:
        lote = nfse_lote.criar_lote(
            db,
            payload.period_label,
            payload.billing_ids,
            competencia=payload.competencia,
            codigo_servico=payload.codigo_servico,
            discriminacao=payload.discriminacao,
            criado_por=user.id,
        )
    except nfse_lote.LoteError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return nfse_lote._lote_resumo(lote)


@router.get('/lotes', response_model=list[LoteResumo])
def lote_listar(
    limit: int = 100,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """Histórico de lotes emitidos (monitoramento)."""
    return nfse_lote.listar_lotes(db, min(limit, 300))


@router.get('/lotes/{lote_id}', response_model=LoteDetalhe)
def lote_detalhe(
    lote_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """Drill-down: status individual de cada nota do lote."""
    detalhe = nfse_lote.consultar_lote(db, lote_id)
    if detalhe is None:
        raise HTTPException(status_code=404, detail='Lote não encontrado')
    return detalhe


@router.get('/{billing_id}/danfse')
def danfse(
    billing_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """DANFSE — o PDF visual/imprimível da NFS-e (o que se envia ao tomador)."""
    nota = db.query(NfseNota).filter_by(billing_id=billing_id).first()
    if nota is None or nota.status != 'emitida' or not nota.chave_acesso:
        raise HTTPException(status_code=404, detail='NFS-e emitida não encontrada para esta cobrança')
    if _provedor() is not nfse_nacional:
        raise HTTPException(status_code=400, detail='DANFSE disponível apenas no Emissor Nacional')
    try:
        pdf = nfse_nacional.baixar_danfse(nota.chave_acesso)
    except (*_ERROS_CONFIG, *_ERROS_API) as exc:
        _raise_nfse_error(exc)
    return Response(
        content=pdf,
        media_type='application/pdf',
        headers={'Content-Disposition': f'inline; filename=nfse-{nota.numero_nfse or billing_id}.pdf'},
    )


@router.get('/{billing_id}/danfse-local')
def danfse_local(
    billing_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """
    O mesmo PDF, montado por nós a partir do XML guardado.

    Existe porque o DANFSE oficial depende de um serviço do governo que cai
    (503) e que nem sequer está implantado na produção restrita. Aqui basta ter
    o XML — e ele está sempre no banco.
    """
    nota = db.query(NfseNota).filter_by(billing_id=billing_id).first()
    if nota is None or not nota.xml_retorno:
        raise HTTPException(status_code=404, detail='XML da NFS-e não disponível para esta cobrança')
    try:
        pdf = nfse_danfse.gerar_danfse_pdf(
            nota.xml_retorno,
            nfse_nacional.url_consulta_publica(nota.chave_acesso)
            if nota.chave_acesso else None,
        )
    except nfse_danfse.DanfseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(
        content=pdf,
        media_type='application/pdf',
        headers={'Content-Disposition':
                 f'inline; filename=nfse-{nota.numero_nfse or billing_id}.pdf'},
    )


@router.get('/{billing_id}/xml')
def xml_nfse(
    billing_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """XML da NFS-e — o documento fiscal em si (o que se guarda/transmite)."""
    nota = db.query(NfseNota).filter_by(billing_id=billing_id).first()
    if nota is None or not nota.xml_retorno:
        raise HTTPException(status_code=404, detail='XML da NFS-e não disponível para esta cobrança')
    return Response(
        content=nota.xml_retorno,
        media_type='application/xml',
        headers={'Content-Disposition':
                 f'attachment; filename=nfse-{nota.numero_nfse or billing_id}.xml'},
    )


@router.get('/{billing_id}', response_model=NfseOut)
def obter(
    billing_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    nota = db.query(NfseNota).filter_by(billing_id=billing_id).first()
    if nota is None:
        raise HTTPException(status_code=404, detail='NFS-e não encontrada para esta cobrança')
    return nota


@router.post('/consultar/{billing_id}', response_model=NfseOut)
def consultar(
    billing_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    nota = db.query(NfseNota).filter_by(billing_id=billing_id).first()
    if nota is None:
        raise HTTPException(status_code=404, detail='NFS-e não encontrada para esta cobrança')
    try:
        # No Emissor Nacional a geração é síncrona: não existe protocolo a
        # reconsultar. Se a nota saiu, revalidamos o XML pela chave de acesso.
        if _provedor() is nfse_nacional:
            if not nota.chave_acesso:
                raise HTTPException(
                    status_code=400,
                    detail='Emissão pelo Emissor Nacional é síncrona — '
                           'não há protocolo a consultar. Emita novamente.',
                )
            nota.xml_retorno = nfse_nacional.consultar_por_chave(nota.chave_acesso)
            db.commit()
            return nota

        if not nota.protocolo:
            raise HTTPException(status_code=400, detail='NFS-e ainda não enviada (sem protocolo)')
        return nfse_joinville.consultar(db, nota)
    except (*_ERROS_CONFIG, *_ERROS_API) as exc:
        _raise_nfse_error(exc)
