"""Fase 04 — migração SGR conservando valores e identidade (SGR-01..07).

Tudo sintético e sem rede: boletos crus do SGR passam pelo MESMO caminho da
coleta real (map_boleto via achatar_boletos), downloads usam transporte falso
e o storage é um dublê que só registra as chaves gravadas.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.models.ailos_boleto import AilosBoleto
from app.models.billing import Billing
from app.models.billing_change_log import BillingChangeLog
from app.models.client import Client
from app.models.contract import Contract
from app.models.document import Document
from app.models.enums import BillingStatus, ClientStatus, TrackerStatus, VehicleStatus
from app.models.sgr_migracao import (
    SgrArquivo,
    SgrConflito,
    SgrDocumento,
    SgrDocumentoLinha,
    SgrExecucao,
    SgrExecucaoUnidade,
    SgrVinculo,
)
from app.models.tracker import Tracker
from app.models.vehicle import Vehicle
from app.services.financial import existing_recurring_periods
from app.services.sgr_migration import conflitos, download, importer
from app.services.sgr_migration.importer import import_poc_result, montar_manifesto, processar_arquivos
from app.services.sgr_migration.poc import ClientNode, PocRunResult, TrackerNode, VehicleNode, achatar_boletos

CPF_A, CPF_B = '11144477735', '52998224725'


# ---------------------------------------------------------------------------
# Construção de dados de origem
# ---------------------------------------------------------------------------

def boleto(cod, linhas, *, valor=None, situacao='ABERTO', venc='15/10/2026', pago=None, data_pag=None,
           cliente='1', link=None, mes='10/2026'):
    """Boleto cru do /buscar_boletos. `linhas` = [(valor, placa, produto)]."""
    discriminacao = [
        {'valor': v, 'placa': placa, 'mes_referente': mes, 'produto': produto}
        for v, placa, produto in linhas
    ]
    # Toda linha real vem acompanhada da cópia zerada da placa (ruído do SGR).
    discriminacao.append({'valor': '0,00', 'placa': linhas[0][1] if linhas else None})
    if valor is None:
        total = sum(Decimal(v.replace('.', '').replace(',', '.')) for v, _p, _pr in linhas)
        valor = f'{total:.2f}'.replace('.', ',')
    return {
        'cod_boleto': str(cod), 'cod_cliente': cliente, 'nosso_numero': str(cod), 'valor': valor,
        'valor_pagamento': pago or '0,00', 'data_vencimento': venc, 'data_pagamento': data_pag,
        'forma_pagamento': 'BOLETO' if pago else None, 'situacao': {'descricao': situacao},
        'parcela': '1 de 1', 'mes_referente': mes, 'discriminacao': discriminacao, 'link': link,
    }


def veiculo(cod, placa, imei=None, cod_rastreador=None, *, contrato=True):
    trackers = []
    if imei is not None or contrato:
        trackers.append(TrackerNode(
            raw={}, issues=[],
            mapped={'imei': imei, 'external_id': cod_rastreador, 'status': TrackerStatus.INSTALLED.value},
            contract={'external_id': f'vinc-{cod}', 'plan_external_id': '17', 'start_date': '2024-01-01',
                      'billing_day': 15, 'status': 'ativo', 'billing_modality': 'boleto'} if contrato else {},
        ))
    return VehicleNode(raw={}, issues=[], trackers=trackers, mapped={
        'external_id': cod, 'plate': placa, 'brand': 'FIAT', 'model': 'UNO', 'status': VehicleStatus.ACTIVE.value,
    })


def no(cod='1', cpf=CPF_A, nome='CLIENTE A', veiculos=None, boletos=(), notas=()):
    issues: list[str] = []
    return ClientNode(
        raw={'cod_cliente': cod}, issues=[],
        mapped={'external_id': cod, 'name': nome, 'cpf_cnpj': cpf, 'type': 'pf',
                'status': ClientStatus.ACTIVE.value},
        vehicles=list(veiculos if veiculos is not None else [veiculo('10', 'ABC1234', '355488020902005', 'r1')]),
        billings=achatar_boletos(list(boletos), issues),
        invoices=list(notas),
    )


def resultado(*nos):
    return PocRunResult(
        limit=10, clients=list(nos), request_count=0, request_log=[],
        plans=[{'external_id': '17', 'name': 'MENSALIDADE 100', 'price': 100.0, 'billing_interval_months': 1}],
    )


def importar(db, *nos, **kw):
    kw.setdefault('baixar', _nao_baixa)
    kw.setdefault('upload', _nao_sobe)
    return import_poc_result(db, resultado(*nos), dry_run=kw.pop('dry_run', False), **kw)


def _nao_baixa(url):
    raise AssertionError('nenhum download esperado neste teste')


def _nao_sobe(**kw):
    raise AssertionError('nenhum upload esperado neste teste')


def cobrancas(db):
    return db.query(Billing).filter(Billing.is_deleted.is_(False)).order_by(Billing.id).all()


def conflitos_abertos(db, tipo=None):
    q = db.query(SgrConflito).filter(SgrConflito.status == 'aberto')
    if tipo:
        q = q.filter(SgrConflito.tipo == tipo)
    return q.all()


# ---------------------------------------------------------------------------
# SGR-01 — desconto e documento consolidado
# ---------------------------------------------------------------------------

class TestDescontoSGR01:
    def test_100_menos_20_e_80_no_importador_na_carteira_e_no_relatorio(self, db, http):
        stats = importar(db, no(boletos=[boleto(9001, [('100,00', 'ABC1234', 'MENSALIDADE'),
                                                      ('-20,00', 'ABC1234', 'DESCONTO')])]))

        [cobranca] = cobrancas(db)
        assert cobranca.amount == Decimal('80.00')
        assert stats.descontos_centavos == 2000
        assert stats.manifesto['diferencas'] == []
        assert 'Desconto do boleto SGR 9001' in cobranca.notes

        documento = db.query(SgrDocumento).one()
        assert (documento.status, documento.total_origem_centavos, documento.descontos_centavos) == (
            'conciliado', 8000, 2000)
        desconto = db.query(SgrDocumentoLinha).filter_by(tipo='desconto').one()
        assert desconto.valor_origem_centavos == -2000
        assert desconto.alocacao == [{'linha': '9001:ABC1234:MENSALIDADE:10/2026:1', 'centavos': 2000}]
        obrigacao = db.query(SgrDocumentoLinha).filter_by(tipo='obrigacao').one()
        assert (obrigacao.valor_origem_centavos, obrigacao.desconto_alocado_centavos) == (10000, 2000)

        relatorio = http.get('/api/v1/reports/revenue', params={'date_from': '2026-10-01',
                                                                'date_to': '2026-10-31'}).json()
        assert relatorio['totais']['total_emitido'] == 80.0
        assert relatorio['totais']['total_aberto'] == 80.0

    def test_boleto_pago_com_desconto_recebido_e_o_liquido(self, db, http):
        importar(db, no(boletos=[boleto(9002, [('100,00', 'ABC1234', 'MENSALIDADE'),
                                              ('-20,00', 'ABC1234', 'DESCONTO')],
                                        situacao='BAIXADO', pago='80,00', data_pag='14/10/2026')]))
        [cobranca] = cobrancas(db)
        assert (cobranca.status, cobranca.amount) == (BillingStatus.PAID, Decimal('80.00'))
        relatorio = http.get('/api/v1/reports/revenue', params={'date_from': '2026-10-01',
                                                                'date_to': '2026-10-31'}).json()
        assert relatorio['totais']['total_recebido'] == 80.0

    def test_varias_placas_desconto_abate_so_a_placa_dele(self, db):
        importar(db, no(
            veiculos=[veiculo('10', 'ABC1234', '355488020902005', 'r1'),
                      veiculo('11', 'DEF5678', '355488020902006', 'r2')],
            boletos=[boleto(9003, [('60,00', 'ABC1234', 'MENSALIDADE'), ('50,00', 'DEF5678', 'MENSALIDADE'),
                                   ('-10,00', 'DEF5678', 'DESCONTO')])],
        ))
        por_placa = {db.get(Vehicle, b.vehicle_id).plate: b.amount for b in cobrancas(db)}
        assert por_placa == {'ABC1234': Decimal('60.00'), 'DEF5678': Decimal('40.00')}

    def test_desconto_integral_vira_bonificada_que_ocupa_o_mes(self, db):
        stats = importar(db, no(boletos=[boleto(9004, [('100,00', 'ABC1234', 'MENSALIDADE'),
                                                      ('-100,00', 'ABC1234', 'DESCONTO')])]))
        [cobranca] = cobrancas(db)
        assert cobranca.status == BillingStatus.CANCELED
        assert 'Bonificada integralmente' in cobranca.notes
        assert stats.linhas_bonificadas == 1
        # Cancelada ocupa o mês: o fechamento não cobra de novo o mês dado.
        assert existing_recurring_periods(db, cobranca.contract_id, ['10/2026']) == {'10/2026'}
        assert stats.manifesto['diferencas'] == []

    @pytest.mark.parametrize('linhas,valor,motivo', [
        ([('100,00', 'ABC1234', 'MENSALIDADE')], '90,00', 'soma_linhas_diverge'),
        ([('49,995', 'ABC1234', 'MENSALIDADE')], '49,99', 'valor_malformado'),
        ([('60,00', 'ABC1234', 'MENSALIDADE'), ('-70,00', 'ABC1234', 'DESCONTO'),
          ('20,00', 'DEF5678', 'MENSALIDADE')], '10,00', 'desconto_maior_que_obrigacoes'),
        ([('60,00', 'ABC1234', 'MENSALIDADE'), ('50,00', 'DEF5678', 'MENSALIDADE'),
          ('-10,00', None, 'DESCONTO')], '100,00', 'desconto_sem_placa_ambiguo'),
    ])
    def test_documento_que_nao_fecha_fica_bloqueado_sem_cobranca(self, db, linhas, valor, motivo):
        stats = importar(db, no(
            veiculos=[veiculo('10', 'ABC1234', '355488020902005', 'r1'),
                      veiculo('11', 'DEF5678', '355488020902006', 'r2')],
            boletos=[boleto(9005, linhas, valor=valor)],
        ))
        assert cobrancas(db) == []
        documento = db.query(SgrDocumento).one()
        assert (documento.status, documento.motivo_bloqueio) == ('bloqueado', motivo)
        assert stats.bloqueios == {motivo: 1}
        assert [d['motivo'] for d in stats.manifesto['documentos_bloqueados']] == [motivo]
        assert stats.status_execucao == 'concluida_com_pendencias'

    def test_linha_sem_valor_que_nao_muda_o_total_e_ignorada(self, db):
        bol = boleto(9006, [('100,00', 'ABC1234', 'MENSALIDADE')])
        bol['discriminacao'].insert(0, {'placa': 'ABC1234', 'mes_referente': '10/2026', 'produto': 'OBS'})
        stats = importar(db, no(boletos=[bol]))
        assert [b.amount for b in cobrancas(db)] == [Decimal('100.00')]
        assert any('linha sem valor ignorada' in s for s in stats.skips)

    def test_documento_sem_total_da_origem_bloqueia(self, db):
        bol = boleto(9007, [('100,00', 'ABC1234', 'MENSALIDADE')])
        bol['valor'] = None
        importar(db, no(boletos=[bol]))
        assert cobrancas(db) == []
        assert db.query(SgrDocumento).one().motivo_bloqueio == 'total_origem_ausente'

    def test_reexecucao_com_a_mesma_origem_e_estavel(self, db):
        origem = [boleto(9008, [('100,00', 'ABC1234', 'MENSALIDADE'), ('-20,00', 'ABC1234', 'DESCONTO')])]
        importar(db, no(boletos=origem))
        antes = [(b.id, b.amount, b.status) for b in cobrancas(db)]
        stats = importar(db, no(boletos=origem))
        assert [(b.id, b.amount, b.status) for b in cobrancas(db)] == antes
        assert (stats.billings_created, stats.documentos_inalterados, stats.billings_reused) == (0, 1, 1)
        assert db.query(SgrDocumentoLinha).count() == 2


# ---------------------------------------------------------------------------
# SGR-02 — rodadas seguintes: sincronização com política de conflito
# ---------------------------------------------------------------------------

def _mensalidade(cod=9101, **kw):
    return boleto(cod, [('100,00', 'ABC1234', 'MENSALIDADE')], **kw)


class TestSincronizacaoSGR02:
    def test_pagamento_entre_rodadas_e_aplicado_com_historico(self, db):
        importar(db, no(boletos=[_mensalidade()]))
        stats = importar(db, no(boletos=[_mensalidade(situacao='BAIXADO', pago='100,00', data_pag='10/10/2026')]))

        [cobranca] = cobrancas(db)
        assert cobranca.status == BillingStatus.PAID
        assert cobranca.payment_date == date(2026, 10, 10)
        assert stats.billings_updated == 1
        campos = {c.field_name for c in db.query(BillingChangeLog).filter_by(billing_id=cobranca.id)}
        assert {'status', 'payment_date'} <= campos
        assert stats.manifesto['diferencas'] == []

    def test_cancelamento_na_origem_cancela_a_aberta(self, db):
        importar(db, no(boletos=[_mensalidade()]))
        importar(db, no(boletos=[_mensalidade(situacao='CANCELADO')]))
        assert cobrancas(db)[0].status == BillingStatus.CANCELED

    def test_baixa_manual_no_mastersat_nao_e_desfeita_pela_origem(self, db):
        importar(db, no(boletos=[_mensalidade()]))
        cobranca = cobrancas(db)[0]
        cobranca.status, cobranca.payment_date, cobranca.paid_amount = BillingStatus.PAID, date(2026, 10, 5), Decimal('100')
        db.commit()

        importar(db, no(boletos=[_mensalidade()]))  # origem igual: nada muda
        stats = importar(db, no(boletos=[_mensalidade(situacao='CANCELADO')]))
        db.refresh(cobranca)
        assert cobranca.status == BillingStatus.PAID
        assert stats.conflitos_por_tipo == {'edicao_local': 1}
        assert len(conflitos_abertos(db, 'edicao_local')) == 1

    def test_baixa_nos_dois_lados_converge_sem_conflito(self, db):
        importar(db, no(boletos=[_mensalidade()]))
        cobranca = cobrancas(db)[0]
        cobranca.status, cobranca.payment_date = BillingStatus.PAID, date(2026, 10, 5)
        db.commit()
        stats = importar(db, no(boletos=[_mensalidade(situacao='BAIXADO', pago='100,00', data_pag='10/10/2026')]))
        assert conflitos_abertos(db) == []
        assert stats.billings_updated == 0
        db.refresh(cobranca)
        assert cobranca.payment_date == date(2026, 10, 5)  # o registro local prevalece

    def test_pago_que_volta_a_aberto_na_origem_nao_e_automatico(self, db):
        importar(db, no(boletos=[_mensalidade(situacao='BAIXADO', pago='100,00', data_pag='10/10/2026')]))
        importar(db, no(boletos=[_mensalidade()]))
        assert cobrancas(db)[0].status == BillingStatus.PAID
        assert len(conflitos_abertos(db, 'transicao_nao_automatica')) == 1

    def test_valor_alterado_na_origem_vira_conflito_sem_mudar_nada(self, db):
        importar(db, no(boletos=[_mensalidade()]))
        stats = importar(db, no(boletos=[boleto(9101, [('120,00', 'ABC1234', 'MENSALIDADE')])]))
        assert cobrancas(db)[0].amount == Decimal('100.00')
        assert stats.conflitos_por_tipo == {'documento_alterado_origem': 1}

    def test_titulo_ailos_registrado_impede_cancelamento_automatico(self, db):
        importar(db, no(boletos=[_mensalidade()]))
        cobranca = cobrancas(db)[0]
        db.add(AilosBoleto(billing_id=cobranca.id, numero_convenio='102004', nosso_numero='NN1',
                           linha_digitavel='LD1', codigo_barras='CB1', status_ailos='0'))
        db.commit()
        importar(db, no(boletos=[_mensalidade(situacao='CANCELADO')]))
        db.refresh(cobranca)
        assert cobranca.status in (BillingStatus.PENDING, BillingStatus.OVERDUE)
        assert len(conflitos_abertos(db)) == 1

    def test_manter_local_nao_reabre_para_o_mesmo_estado_da_origem(self, db):
        importar(db, no(boletos=[_mensalidade()]))
        cobranca = cobrancas(db)[0]
        cobranca.status = BillingStatus.PAID
        cobranca.payment_date = date(2026, 10, 5)
        db.commit()
        importar(db, no(boletos=[_mensalidade(situacao='CANCELADO')]))
        [conflito] = conflitos_abertos(db)
        conflitos.resolver(db, conflito.id, 'manter_local', operador='financeiro', justificativa='pago no caixa')
        db.commit()

        importar(db, no(boletos=[_mensalidade(situacao='CANCELADO')]))
        assert conflitos_abertos(db) == []
        db.refresh(cobranca)
        assert cobranca.status == BillingStatus.PAID

    def test_aplicar_origem_resolve_pela_politica(self, db):
        importar(db, no(boletos=[_mensalidade()]))
        cobranca = cobrancas(db)[0]
        cobranca.notes = 'x'
        cobranca.due_date = date(2026, 10, 20)  # edição local
        db.commit()
        importar(db, no(boletos=[_mensalidade(situacao='BAIXADO', pago='100,00', data_pag='10/10/2026')]))
        [conflito] = conflitos_abertos(db, 'edicao_local')
        with pytest.raises(conflitos.ResolucaoRecusada):
            conflitos.resolver(db, conflito.id, 'aprovar_transferencia', operador='fin', justificativa='x')
        conflitos.resolver(db, conflito.id, 'aplicar_origem', operador='fin', justificativa='conferido no extrato')
        db.commit()
        db.refresh(cobranca)
        assert cobranca.status == BillingStatus.PAID
        assert db.get(SgrConflito, conflito.id).resolvido_por == 'fin'

    def test_reemissao_no_mesmo_mes_passa_a_mensalidade_para_o_boleto_novo(self, db):
        importar(db, no(boletos=[_mensalidade(9201)]))
        stats = importar(db, no(boletos=[_mensalidade(9201, situacao='REMOVIDO'), _mensalidade(9202)]))

        antigo, novo = cobrancas(db)
        assert (antigo.status, antigo.competencia_liberada) == (BillingStatus.CANCELED, True)
        assert (novo.receipt_number, novo.billing_type) == ('9202', 'recorrente')
        assert not any('duplicidade' in s for s in stats.skips)


# ---------------------------------------------------------------------------
# SGR-03 / SGR-07 — arquivos, checkpoint e simulação
# ---------------------------------------------------------------------------

PDF = b'%PDF-1.4 boleto sintetico %%EOF'


class Storage:
    def __init__(self, falhar=0):
        self.chaves: list[str] = []
        self.falhar = falhar

    def __call__(self, *, object_name, content, content_type):
        if self.falhar:
            self.falhar -= 1
            raise ConnectionError('minio fora do ar')
        self.chaves.append(object_name)
        return object_name


def _baixa(conteudo=PDF, falhas=0):
    estado = {'falhas': falhas, 'chamadas': 0}

    def baixar(url):
        estado['chamadas'] += 1
        if estado['falhas']:
            estado['falhas'] -= 1
            raise download.DownloadFalhou('Timeout')
        return download.ArquivoBaixado(conteudo, 'sha', 'boletos.exemplo')
    baixar.estado = estado
    return baixar


@pytest.fixture()
def hosts(monkeypatch):
    monkeypatch.setattr(settings, 'sgr_download_hosts', 'boletos.exemplo,.gateway.exemplo')


def _com_link(cod=9301):
    return _mensalidade(cod, link=f'https://boletos.exemplo/{cod}.pdf')


class TestArquivosSGR03e07:
    def test_pdf_que_falhou_e_retomado_mesmo_com_a_cobranca_ja_criada(self, db, hosts):
        storage = Storage()
        importar(db, no(boletos=[_com_link()]), baixar=_baixa(falhas=1), upload=storage)
        assert db.query(Document).count() == 0
        assert db.query(SgrArquivo).one().status == 'falhou'
        assert len(cobrancas(db)) == 1

        stats = importar(db, no(boletos=[_com_link()]), baixar=_baixa(), upload=storage)
        assert stats.boletos_created == 1
        stats = importar(db, no(boletos=[_com_link()]), baixar=_nao_baixa, upload=storage)
        assert stats.boletos_reused == 1
        documento = db.query(Document).one()
        assert documento.file_name == 'boleto-sgr-9301.pdf'
        assert storage.chaves == [documento.object_key]
        assert len(cobrancas(db)) == 1

    def test_storage_fora_do_ar_fica_pendente_e_converge(self, db, hosts):
        storage = Storage(falhar=1)
        importar(db, no(boletos=[_com_link()]), baixar=_baixa(), upload=storage)
        assert db.query(SgrArquivo).one().ultimo_erro == 'storage: ConnectionError'
        importar(db, no(boletos=[_com_link()]), baixar=_baixa(), upload=storage)
        assert db.query(Document).count() == 1

    def test_pdf_invalido_nao_vira_documento(self, db, hosts):
        importar(db, no(boletos=[_com_link()]), baixar=_baixa(b'<html>erro</html>'), upload=Storage())
        assert db.query(Document).count() == 0
        assert db.query(SgrArquivo).one().ultimo_erro == 'conteudo_nao_pdf'

    def test_host_nao_aprovado_fica_bloqueado_ate_ser_liberado(self, db, monkeypatch):
        monkeypatch.setattr(settings, 'sgr_download_hosts', '')
        importar(db, no(boletos=[_com_link()]), baixar=_nao_baixa, upload=Storage())
        assert db.query(SgrArquivo).one().status == 'bloqueado'
        monkeypatch.setattr(settings, 'sgr_download_hosts', 'boletos.exemplo')
        processar_arquivos(db, baixar=_baixa(), upload=Storage())
        assert db.query(Document).count() == 1

    def test_banco_falha_depois_do_upload_sem_objeto_orfao(self, db, hosts, monkeypatch):
        importar(db, no(boletos=[_com_link()]), processar_downloads=False)
        storage = Storage()
        commit_original = db.commit
        falhar = {'n': 1}

        def commit():
            if storage.chaves and falhar['n']:
                falhar['n'] = 0
                raise OperationalError('COMMIT', {}, Exception('conexão perdida'))
            return commit_original()
        monkeypatch.setattr(db, 'commit', commit)

        processar_arquivos(db, baixar=_baixa(), upload=storage)
        assert db.query(Document).count() == 0
        assert db.query(SgrArquivo).one().status == 'pendente'
        processar_arquivos(db, baixar=_baixa(), upload=storage)

        documento = db.query(Document).one()
        assert storage.chaves == [documento.object_key, documento.object_key]  # mesma chave: sobrescreve
        assert db.query(SgrArquivo).one().document_id == documento.id

    def test_documento_do_importador_antigo_e_reaproveitado(self, db, hosts):
        importar(db, no(boletos=[_mensalidade(9302)]))
        cliente = db.query(Client).one()
        db.add(Document(file_name='boleto-sgr-9302.pdf', object_key='clients/1/documents/uuid-boleto-sgr-9302.pdf',
                        content_type='application/pdf', size_bytes=10, reference_type='client',
                        reference_id=cliente.id, category='boleto', active=True))
        db.commit()
        stats = importar(db, no(boletos=[_com_link(9302)]), baixar=_nao_baixa, upload=_nao_sobe)
        assert stats.boletos_reused == 1
        assert db.query(SgrArquivo).one().status == 'baixado'

    def test_simulacao_nao_grava_banco_nem_objeto(self, db, hosts):
        importar(db, no(cod='1'), no(cod='2', cpf=CPF_B, veiculos=[veiculo('20', 'ABC1234')]))  # base existente
        contagem = {m: db.query(m).count() for m in (Client, Vehicle, Contract, Billing, SgrVinculo,
                                                     SgrExecucao, SgrExecucaoUnidade, SgrConflito,
                                                     SgrDocumento, SgrArquivo, Document)}
        stats = importar(db, no(boletos=[_com_link(9303), boleto(9304, [('100,00', 'ABC1234', 'X')], valor='1,00')]),
                         no(cod='3', cpf='12345678909', veiculos=[veiculo('30', 'ABC1234')]),
                         dry_run=True, baixar=_nao_baixa, upload=_nao_sobe)
        assert stats.billings_created == 1 and stats.documentos_bloqueados == 1 and stats.conflitos >= 1
        assert stats.boletos_created == 1  # seria baixado
        assert {m: db.query(m).count() for m in contagem} == contagem

    def test_unidade_que_falha_nao_desfaz_as_outras_e_a_retomada_converge(self, db, monkeypatch):
        original = importer._import_invoices

        def quebra(ctx, stats, node, client):
            if node.mapped['external_id'] == '2':
                raise RuntimeError('falha simulada no meio do cliente 2')
            return original(ctx, stats, node, client)
        monkeypatch.setattr(importer, '_import_invoices', quebra)
        nos = (no(cod='1', boletos=[_mensalidade(9401)]),
               no(cod='2', cpf=CPF_B, veiculos=[veiculo('20', 'DEF5678')],
                  boletos=[boleto(9402, [('50,00', 'DEF5678', 'MENSALIDADE')], cliente='2')]))
        stats = importar(db, *nos)
        assert stats.status_execucao == 'incompleta' and stats.unidades_falharam == 1
        assert [c.cpf_cnpj for c in db.query(Client)] == [CPF_A]
        assert [b.receipt_number for b in cobrancas(db)] == ['9401']
        primeira = stats.execucao_id

        monkeypatch.setattr(importer, '_import_invoices', original)
        stats = importar(db, *nos, retomar_de=primeira)
        assert stats.unidades_reaproveitadas == 1
        assert sorted(b.receipt_number for b in cobrancas(db)) == ['9401', '9402']
        unidades = {u.unidade: u.status for u in db.query(SgrExecucaoUnidade).filter_by(execucao_id=stats.execucao_id)}
        assert unidades == {'planos': 'aplicada', 'cliente:1': 'reaproveitada', 'cliente:2': 'aplicada'}
        assert db.get(SgrExecucao, stats.execucao_id).status == 'concluida'


# ---------------------------------------------------------------------------
# SGR-04 — dono conferido antes de reaproveitar
# ---------------------------------------------------------------------------

class TestProprietarioSGR04:
    def _dois_clientes(self, db, placa_b='ABC1234', imei_b='355488020902009'):
        importar(db, no(cod='1', boletos=[boleto(9501, [('100,00', 'ABC1234', 'MENSALIDADE')])]))
        return importar(db, no(
            cod='2', cpf=CPF_B, nome='CLIENTE B',
            veiculos=[veiculo('20', placa_b, imei_b, 'r20')],
            boletos=[boleto(9502, [('100,00', placa_b, 'MENSALIDADE')], cliente='2')],
        ))

    def _sem_vinculo_cruzado(self, db, desde_cobranca=0):
        # Contrato vigente e cobrança nova sempre do dono atual do veículo.
        # (Contrato encerrado e cobrança antiga de um veículo transferido são
        # histórico do dono anterior — não vínculo cruzado.)
        for contrato in db.query(Contract).filter(Contract.status == 'ativo').all():
            assert contrato.client_id == db.get(Vehicle, contrato.vehicle_id).client_id
        for cobranca in cobrancas(db):
            if cobranca.vehicle_id and cobranca.id > desde_cobranca:
                assert db.get(Vehicle, cobranca.vehicle_id).client_id == cobranca.client_id

    def test_mesma_placa_em_outro_cliente_vira_transferencia_pendente(self, db):
        stats = self._dois_clientes(db)
        self._sem_vinculo_cruzado(db)
        assert db.query(Contract).count() == 1
        assert stats.conflitos_por_tipo.get('transferencia_veiculo') == 1
        assert db.query(SgrDocumento).filter_by(cod_boleto='9502').one().motivo_bloqueio == 'veiculo_em_conflito'
        assert [b.receipt_number for b in cobrancas(db)] == ['9501']

    def test_mesmo_imei_em_outro_veiculo_nao_e_reaproveitado(self, db):
        stats = self._dois_clientes(db, placa_b='DEF5678', imei_b='355488020902005')
        self._sem_vinculo_cruzado(db)
        rastreador = db.query(Tracker).one()
        assert db.get(Vehicle, rastreador.vehicle_id).plate == 'ABC1234'
        assert stats.conflitos_por_tipo.get('rastreador_em_outro_veiculo') == 1
        contrato_b = db.query(Contract).filter(Contract.client_id != rastreador.client_id).one()
        assert contrato_b.tracker_id is None

    def test_transferencia_aprovada_move_o_veiculo_e_preserva_o_historico(self, db):
        self._dois_clientes(db)
        [conflito] = conflitos_abertos(db, 'transferencia_veiculo')
        with pytest.raises(conflitos.ResolucaoRecusada, match='está ativo'):
            conflitos.resolver(db, conflito.id, 'aprovar_transferencia', operador='op', justificativa='venda')
        contrato_a = db.query(Contract).one()
        contrato_a.status = 'inativo'
        db.commit()
        ultima_antes = max(b.id for b in cobrancas(db))
        conflitos.resolver(db, conflito.id, 'aprovar_transferencia', operador='op', justificativa='venda')
        db.commit()

        stats = importar(db, no(cod='2', cpf=CPF_B, nome='CLIENTE B', veiculos=[veiculo('20', 'ABC1234', 'x9', 'r20')],
                                boletos=[boleto(9502, [('100,00', 'ABC1234', 'MENSALIDADE')], cliente='2')]))
        assert stats.billings_created == 1
        cliente_b = db.query(Client).filter_by(cpf_cnpj=CPF_B).one()
        assert db.query(Vehicle).one().client_id == cliente_b.id
        db.refresh(contrato_a)
        assert contrato_a.client_id != cliente_b.id  # contrato e cobrança antigos seguem com o dono anterior
        assert db.query(Billing).filter_by(receipt_number='9501').one().client_id == contrato_a.client_id
        self._sem_vinculo_cruzado(db, desde_cobranca=ultima_antes)
        assert db.query(SgrVinculo).filter_by(entidade='veiculo').one().chave_origem == '20'

    def test_pagador_interveniente_nao_e_vinculo_cruzado(self, db):
        atendido = Client(name='ATENDIDO', cpf_cnpj=CPF_A, type='pf', status=ClientStatus.ACTIVE)
        pagador = Client(name='PAGADOR', cpf_cnpj=CPF_B, type='pf', status=ClientStatus.ACTIVE)
        db.add_all([atendido, pagador])
        db.flush()
        carro = Vehicle(client_id=atendido.id, plate='ABC1234', status=VehicleStatus.ACTIVE)
        db.add(carro)
        db.flush()
        from app.models.plan import Plan
        plano = Plan(name='P', price=Decimal('100'))
        db.add(plano)
        db.flush()
        db.add(Contract(client_id=atendido.id, vehicle_id=carro.id, plan_id=plano.id, start_date=date(2024, 1, 1),
                        interveniente_client_id=pagador.id, status='ativo'))
        db.commit()

        importar(db, no(cod='2', cpf=CPF_B, nome='PAGADOR', veiculos=[],
                        boletos=[boleto(9601, [('100,00', 'ABC1234', 'MENSALIDADE')], cliente='2')]))
        [cobranca] = cobrancas(db)
        assert (cobranca.client_id, cobranca.payer_client_id, cobranca.vehicle_id) == (atendido.id, pagador.id, carro.id)

    def test_dois_codigos_de_origem_com_o_mesmo_cpf_bloqueiam_o_segundo(self, db):
        importar(db, no(cod='1'))
        stats = importar(db, no(cod='99', veiculos=[]))
        assert stats.clientes_bloqueados == 1
        assert len(conflitos_abertos(db, 'documento_em_outro_cliente_sgr')) == 1
        assert db.query(Client).count() == 1

    def test_identidade_de_origem_sobrevive_a_troca_de_cpf(self, db):
        importar(db, no(cod='1'))
        stats = importar(db, no(cod='1', cpf=CPF_B))
        assert db.query(Client).count() == 1  # antes: entraria um cliente novo
        assert db.query(Client).one().cpf_cnpj == CPF_A  # cadastro local não é sobrescrito
        assert stats.conflitos_por_tipo == {'documento_divergente': 1}


# ---------------------------------------------------------------------------
# Dados malformados, placa incomum, escopo e manifesto
# ---------------------------------------------------------------------------

class TestEntradaEManifesto:
    def test_placa_de_maquina_admitida_serializa_na_api(self, db):
        from app.schemas.vehicle import VehicleOut

        importar(db, no(veiculos=[veiculo('10', 'MAQ002', '1', 'r1')],
                        boletos=[boleto(9701, [('80,00', 'MAQ002', 'MENSALIDADE')])]))
        carro = db.query(Vehicle).one()
        VehicleOut.model_validate(carro, from_attributes=True)
        assert cobrancas(db)[0].vehicle_id == carro.id

    def test_campos_nulos_nao_quebram_a_importacao(self, db):
        bol = boleto(9702, [('100,00', None, None)])
        bol.update({'nosso_numero': None, 'parcela': None, 'data_pagamento': '', 'mes_referente': None})
        for item in bol['discriminacao']:
            item['mes_referente'] = None
        stats = importar(db, no(veiculos=[], boletos=[bol]))
        assert [b.amount for b in cobrancas(db)] == [Decimal('100.00')]
        assert stats.manifesto['diferencas'] == []

    def test_manifesto_sem_dado_pessoal_e_denuncia_divergencia(self, db):
        stats = importar(db, no(nome='FULANO DE TAL', boletos=[_mensalidade(9703)]))
        texto = json.dumps(stats.manifesto, ensure_ascii=False)
        assert CPF_A not in texto and 'FULANO' not in texto and 'ABC1234' not in texto
        linha = stats.manifesto['por_cliente_competencia_status'][0]
        assert (linha['cod_cliente'], linha['competencia'], linha['origem_centavos'], linha['diferenca_centavos']) == (
            '1', '10/2026', 10000, 0)

        cobrancas(db)[0].amount = Decimal('90')  # adulteração posterior
        db.commit()
        manifesto = montar_manifesto(db, resultado(no(boletos=[_mensalidade(9703)])), importer.ImportStats())
        assert [d['tipo'] for d in manifesto['diferencas']] == ['valor_divergente']

    def test_cliente_com_coleta_incompleta_nao_e_importado(self, db):
        parcial = no(boletos=[_mensalidade(9704)])
        parcial.coleta_incompleta = ['veículos do cliente: SGRServerError']
        stats = importar(db, parcial)
        assert db.query(Client).count() == 0 and cobrancas(db) == []
        assert stats.status_execucao == 'incompleta'
        assert stats.manifesto['clientes_com_coleta_incompleta'] == ['1']
