"""
Importação SGR -> MasterSat: não duplica, não sobrescreve correção local e
só libera cobrança de documento conciliado (soma das linhas = total da
origem). As entradas de cobrança aqui imitam a saída de map_boleto: cada uma
carrega o documento de origem em ``sgr_payload`` (código e valor total).
"""
from __future__ import annotations

from decimal import Decimal

from app.models.billing import Billing
from app.models.client import Client
from app.models.contract import Contract
from app.models.document import Document
from app.models.enums import BillingStatus, ClientStatus, TrackerStatus, VehicleStatus
from app.models.plan import Plan
from app.models.tracker import Tracker
from app.models.vehicle import Vehicle
from app.services.sgr_migration.importer import import_poc_result
from app.services.sgr_migration.poc import ClientNode, PocRunResult, TrackerNode, VehicleNode


def _resultado(client_over=None, vehicle_over=None, tracker_over=None, com_tracker=True,
               contract_over=None, planos=None):
    contrato = {'plan_external_id': '17', 'billing_day': 15, 'start_date': '2018-06-01',
                'status': 'ativo', 'billing_modality': 'boleto', 'installation_fee': 150.0,
                'vehicle_plate': 'ABC1234', **(contract_over or {})}
    tracker = TrackerNode(
        raw={}, issues=[], contract=contrato,
        mapped={'imei': '355488020902005', 'serial_number': '912992', 'model': 'ST310',
                'status': TrackerStatus.INSTALLED.value, 'sim_number': '55999990864',
                'install_date': '2024-07-31', 'installation_fee': 150.0, **(tracker_over or {})},
    )
    vehicle = VehicleNode(
        raw={}, issues=[], trackers=[tracker] if com_tracker else [],
        mapped={'plate': 'ABC1234', 'brand': 'GM - CHEVROLET', 'model': 'ONIX',
                'color': 'BRANCA', 'manufacture_year': 2013, 'model_year': 2014,
                'city': 'JOINVILLE', 'state': 'SC', 'contract_number': '1',
                'contract_date': '2018-06-01', 'status': VehicleStatus.ACTIVE.value,
                **(vehicle_over or {})},
    )
    client = ClientNode(
        raw={}, issues=[], vehicles=[vehicle],
        mapped={'external_id': '1', 'name': 'EDSON ROBERTO SIMAS', 'cpf_cnpj': '11144477735',
                'type': 'pf', 'status': ClientStatus.ACTIVE.value, 'email': 'a@x.com',
                'phone': '5547999990000', 'city': 'JOINVILLE', 'state': 'SC',
                **(client_over or {})},
    )
    return PocRunResult(
        limit=10, clients=[client], request_count=0, request_log=[],
        plans=planos if planos is not None else [
            {'external_id': '17', 'name': 'MENSALIDADE 64,99', 'price': 64.99,
             'billing_interval_months': 1},
        ],
    )


class TestDryRun:
    def test_dry_run_nao_grava_nada(self, db):
        stats = import_poc_result(db, _resultado(), dry_run=True)
        assert stats.clients_created == 1
        assert db.query(Client).count() == 0
        assert db.query(Vehicle).count() == 0
        assert db.query(Tracker).count() == 0


class TestImportacao:
    def test_cria_cliente_veiculo_e_rastreador_vinculados(self, db):
        stats = import_poc_result(db, _resultado(), dry_run=False)
        assert (stats.clients_created, stats.vehicles_created, stats.trackers_created) == (1, 1, 1)

        cliente = db.query(Client).one()
        veiculo = db.query(Vehicle).one()
        rastreador = db.query(Tracker).one()
        assert veiculo.client_id == cliente.id
        assert rastreador.vehicle_id == veiculo.id
        assert rastreador.client_id == cliente.id

    def test_converte_tipos_do_sgr(self, db):
        import_poc_result(db, _resultado(), dry_run=False)
        veiculo = db.query(Vehicle).one()
        rastreador = db.query(Tracker).one()
        assert veiculo.manufacture_year == 2013
        assert veiculo.contract_date.isoformat() == '2018-06-01'
        assert veiculo.status == VehicleStatus.ACTIVE
        assert rastreador.install_date.isoformat() == '2024-07-31'

    def test_veiculo_sem_placa_e_ignorado(self, db):
        stats = import_poc_result(db, _resultado(vehicle_over={'plate': None}), dry_run=False)
        assert stats.vehicles_skipped == 1
        assert db.query(Vehicle).count() == 0
        assert db.query(Client).count() == 1  # o cliente entra mesmo assim

    def test_rastreador_sem_imei_e_ignorado(self, db):
        stats = import_poc_result(db, _resultado(tracker_over={'imei': None}), dry_run=False)
        assert stats.trackers_skipped == 1
        assert db.query(Tracker).count() == 0
        assert db.query(Vehicle).count() == 1  # o veículo entra mesmo assim

    def test_cliente_sem_cpf_e_ignorado_com_seus_veiculos(self, db):
        stats = import_poc_result(db, _resultado(client_over={'cpf_cnpj': None}), dry_run=False)
        assert stats.clients_skipped == 1
        assert db.query(Client).count() == 0
        assert db.query(Vehicle).count() == 0

    def test_veiculo_sem_rastreador_ainda_e_importado(self, db):
        stats = import_poc_result(db, _resultado(com_tracker=False), dry_run=False)
        assert stats.vehicles_created == 1
        assert stats.trackers_created == 0


class TestNaoGravaDadoQueApiNaoServe:
    """O importador escreve pelo ORM e contorna o Pydantic — mas o schema de
    RESPOSTA valida na saída. Gravar dado inválido aqui derruba o GET
    /vehicles inteiro (aconteceu: 26 de 70 veículos quebraram a listagem)."""

    def test_renavam_placeholder_do_sgr_entra_vazio(self, db):
        import_poc_result(db, _resultado(vehicle_over={'renavam': '00000000000000000000'}), dry_run=False)
        assert db.query(Vehicle).one().renavam is None

    def test_renavam_curto_entra_vazio(self, db):
        import_poc_result(db, _resultado(vehicle_over={'renavam': '000089'}), dry_run=False)
        assert db.query(Vehicle).one().renavam is None

    def test_renavam_valido_e_preservado(self, db):
        import_poc_result(db, _resultado(vehicle_over={'renavam': '12345678901'}), dry_run=False)
        assert db.query(Vehicle).one().renavam == '12345678901'

    def test_chassi_curto_entra_vazio(self, db):
        import_poc_result(db, _resultado(vehicle_over={'chassis': '4509'}), dry_run=False)
        assert db.query(Vehicle).one().chassis is None

    def test_cep_invalido_entra_vazio(self, db):
        import_poc_result(db, _resultado(vehicle_over={'address_zip_code': '123'}), dry_run=False)
        assert db.query(Vehicle).one().address_zip_code is None

    def test_placa_fora_do_padrao_e_recusada(self, db):
        # máquina pesada rastreada sem placa (CASE580H) — o modelo não comporta
        stats = import_poc_result(db, _resultado(vehicle_over={'plate': 'CASE580H'}), dry_run=False)
        assert stats.vehicles_skipped == 1
        assert db.query(Vehicle).count() == 0
        assert any('fora do padrão' in s for s in stats.skips)

    def test_tudo_que_entra_serializa_na_api(self, db):
        """Trava de regressão: o que o importador grava, a API devolve."""
        from app.schemas.vehicle import VehicleOut

        import_poc_result(db, _resultado(vehicle_over={
            'renavam': '00000000000000000000', 'chassis': '4509', 'address_zip_code': '123',
        }), dry_run=False)
        for veiculo in db.query(Vehicle).all():
            VehicleOut.model_validate(veiculo, from_attributes=True)


class TestIdempotencia:
    def test_rodar_duas_vezes_nao_duplica(self, db):
        import_poc_result(db, _resultado(), dry_run=False)
        stats = import_poc_result(db, _resultado(), dry_run=False)

        assert (stats.clients_created, stats.vehicles_created, stats.trackers_created) == (0, 0, 0)
        assert (stats.clients_reused, stats.vehicles_reused, stats.trackers_reused) == (1, 1, 1)
        assert db.query(Client).count() == 1
        assert db.query(Vehicle).count() == 1
        assert db.query(Tracker).count() == 1

    def test_nao_sobrescreve_registro_ja_existente_no_mastersat(self, db):
        db.add(Client(name='NOME AJUSTADO NA MAO', cpf_cnpj='11144477735',
                      type='pf', status=ClientStatus.ACTIVE))
        db.commit()

        import_poc_result(db, _resultado(), dry_run=False)

        # o SGR traz outro nome, mas a correção manual do MasterSat prevalece
        assert db.query(Client).one().name == 'NOME AJUSTADO NA MAO'

    def test_reaproveita_veiculo_existente_pela_placa(self, db):
        cliente = Client(name='X', cpf_cnpj='11144477735', type='pf', status=ClientStatus.ACTIVE)
        db.add(cliente)
        db.flush()
        db.add(Vehicle(client_id=cliente.id, plate='ABC1234', status=VehicleStatus.ACTIVE))
        db.commit()

        stats = import_poc_result(db, _resultado(), dry_run=False)
        assert stats.vehicles_reused == 1
        assert db.query(Vehicle).count() == 1


class TestPlanosEContratos:
    def test_cria_plano_e_contrato_ligados_ao_cliente_e_veiculo(self, db):
        stats = import_poc_result(db, _resultado(), dry_run=False)

        assert stats.plans_created == 1
        assert stats.contracts_created == 1

        contrato = db.query(Contract).one()
        plano = db.query(Plan).one()
        veiculo = db.query(Vehicle).one()
        assert contrato.plan_id == plano.id
        assert contrato.vehicle_id == veiculo.id
        assert contrato.client_id == veiculo.client_id
        assert contrato.billing_day == 15
        assert plano.price == Decimal('64.99')

    def test_grupo_que_so_existe_na_tabela_de_adesao_vira_contrato(self, db):
        # Caso real: 16 veículos de um cliente usam cod_grupo_vinculo=5, que
        # não está em /get_grupo_mensalidade, só em /get_grupo_adesao. Ler uma
        # tabela só deixaria todos esses contratos de fora.
        stats = import_poc_result(db, _resultado(
            contract_over={'plan_external_id': '5'},
            planos=[{'external_id': '5', 'name': 'MASTER ESPECIAL 49,99', 'price': 49.99,
                     'billing_interval_months': 1}],
        ), dry_run=False)

        assert stats.contracts_created == 1
        assert db.query(Contract).one().plan_id == db.query(Plan).one().id

    def test_grupo_desconhecido_nao_cria_contrato_e_e_reportado(self, db):
        stats = import_poc_result(db, _resultado(
            contract_over={'plan_external_id': '999'},
        ), dry_run=False)

        assert stats.contracts_created == 0
        assert stats.contracts_skipped == 1
        assert any('999' in motivo for motivo in stats.skips)
        assert db.query(Contract).count() == 0

    def test_contrato_sem_data_de_inicio_e_ignorado(self, db):
        stats = import_poc_result(db, _resultado(
            contract_over={'start_date': None},
        ), dry_run=False)

        assert stats.contracts_skipped == 1
        assert db.query(Contract).count() == 0

    def test_rodar_duas_vezes_nao_duplica_plano_nem_contrato(self, db):
        import_poc_result(db, _resultado(), dry_run=False)
        stats = import_poc_result(db, _resultado(), dry_run=False)

        assert stats.plans_reused == 1
        assert stats.contracts_reused == 1
        assert db.query(Plan).count() == 1
        assert db.query(Contract).count() == 1

    def test_gerar_cobranca_nao_vira_contrato_inativo(self, db):
        import_poc_result(db, _resultado(contract_over={'status': 'inativo'}), dry_run=False)
        assert db.query(Contract).one().status == 'inativo'


class TestHistoricoDeCobranca:
    def _com_boleto(self, **over):
        boleto = {'external_id': '7370', 'amount': 49.99, 'vehicle_plate': 'ABC1234',
                  'period_label': '08/2026', 'due_date': '2026-08-15', 'payment_date': None,
                  'paid_amount': None, 'payment_method': None, 'receipt_number': '211558',
                  'installment_number': 1, 'installment_total': 10, 'status': 'pendente',
                  'title': 'Boleto SGR 211558', 'sgr_payload': {'cod_boleto': '7370', 'valor': '49,99'},
                  **over}
        resultado = _resultado()
        resultado.clients[0].billings = [boleto]
        return resultado

    def test_cobranca_e_ligada_ao_contrato_do_veiculo(self, db):
        # Sem contract_id a tela do financeiro não consegue mostrar o plano
        # nem o status do contrato da cobrança.
        import_poc_result(db, self._com_boleto(), dry_run=False)

        billing = db.query(Billing).one()
        contrato = db.query(Contract).one()
        assert billing.contract_id == contrato.id
        assert billing.vehicle_id == contrato.vehicle_id

    def test_pendente_ja_vencida_entra_como_vencida(self, db):
        # 'ABERTO' no SGR não distingue vencido; as telas de pendência e
        # inadimplência do MasterSat filtram por VENCIDA.
        import_poc_result(db, self._com_boleto(
            status='pendente', due_date='2020-01-10',
        ), dry_run=False)

        assert db.query(Billing).one().status == BillingStatus.OVERDUE

    def test_pendente_a_vencer_continua_pendente(self, db):
        import_poc_result(db, self._com_boleto(
            status='pendente', due_date='2099-01-10',
        ), dry_run=False)

        assert db.query(Billing).one().status == BillingStatus.PENDING

    def test_boleto_pago_nao_vira_vencido(self, db):
        import_poc_result(db, self._com_boleto(
            status='paga', due_date='2020-01-10', payment_date='2020-01-09',
        ), dry_run=False)

        assert db.query(Billing).one().status == BillingStatus.PAID

    def test_cobranca_sem_veiculo_conhecido_entra_no_cliente(self, db):
        # Máquina sem placa no padrão não é migrada, mas o valor continua
        # fazendo parte do histórico do cliente.
        import_poc_result(db, self._com_boleto(vehicle_plate='CASE580H'), dry_run=False)

        billing = db.query(Billing).one()
        assert billing.vehicle_id is None
        assert billing.contract_id is None
        assert billing.client_id == db.query(Client).one().id

    def _mes_com_reemissoes(self, boletos):
        resultado = _resultado()
        resultado.clients[0].billings = [
            {'amount': 59.99, 'vehicle_plate': 'ABC1234', 'period_label': '03/2021',
             'due_date': '2021-04-12', 'payment_date': None, 'paid_amount': None,
             'payment_method': None, 'installment_number': 1, 'installment_total': 1,
             'billing_type': 'recorrente', 'title': f'Boleto SGR {cod} - ABC1234',
             'receipt_number': cod, 'status': status, 'sgr_payload': {'cod_boleto': cod, 'valor': '59,99'}}
            for cod, status in boletos
        ]
        return resultado

    def test_reemissoes_canceladas_do_mes_nao_viram_segunda_mensalidade(self, db):
        # Caso real (ARV0682, 03/2021): 3 reemissões canceladas/removidas e 1
        # pago no mesmo mês — gravar todos como mensalidade violava
        # uq_billings_contract_period_recurring e abortava a importação.
        # O pago vem por ÚLTIMO de propósito: é ele que tem de ficar.
        stats = import_poc_result(db, self._mes_com_reemissoes([
            ('5888', 'cancelada'), ('5950', 'cancelada'), ('7761', 'cancelada'), ('7787', 'paga'),
        ]), dry_run=False)

        billing = db.query(Billing).one()
        assert billing.receipt_number == '7787'
        assert billing.status == BillingStatus.PAID
        assert billing.billing_type == 'recorrente'
        assert stats.billings_replaced == 3

    def test_mes_so_com_canceladas_fica_com_a_mais_recente(self, db):
        import_poc_result(db, self._mes_com_reemissoes([
            ('5950', 'cancelada'), ('7761', 'cancelada'),
        ]), dry_run=False)

        assert db.query(Billing).one().receipt_number == '7761'

    def test_segundo_boleto_pago_no_mesmo_mes_entra_como_avulsa(self, db):
        # Pagamento em duplicidade é dinheiro real: não some, mas também não
        # pode ocupar o mês como uma 2ª mensalidade.
        stats = import_poc_result(db, self._mes_com_reemissoes([
            ('7787', 'paga'), ('7790', 'paga'),
        ]), dry_run=False)

        tipos = {b.receipt_number: b.billing_type for b in db.query(Billing).all()}
        assert tipos == {'7790': 'recorrente', '7787': 'avulsa'}
        assert any('duplicidade' in (b.notes or '') for b in db.query(Billing).all())
        assert any('confira' in motivo for motivo in stats.skips)

    def test_rodar_duas_vezes_nao_duplica_cobranca(self, db):
        import_poc_result(db, self._com_boleto(), dry_run=False)
        stats = import_poc_result(db, self._com_boleto(), dry_run=False)

        assert stats.billings_reused == 1
        assert db.query(Billing).count() == 1

    def test_placas_nao_migradas_no_mesmo_boleto_nao_colapsam(self, db):
        # Caso real (boleto 11751): um boleto consolidado com várias placas,
        # três delas máquinas sem placa no padrão. Todas caem com vehicle_id
        # nulo, e uma chave de dedup sem a placa de origem descartaria duas
        # cobranças de R$ 44,99 que existem de verdade.
        resultado = _resultado()
        resultado.clients[0].billings = [
            {'external_id': '11751', 'amount': 44.99, 'vehicle_plate': p,
             'period_label': '07/2019', 'due_date': '2019-08-15', 'payment_date': None,
             'paid_amount': None, 'payment_method': None, 'receipt_number': '11751',
             'installment_number': None, 'installment_total': None, 'status': 'paga',
             'title': f'Boleto SGR 11751 - {p}', 'sgr_payload': {'cod_boleto': '11751', 'valor': '134,97'}}
            for p in ('MAQ2501', 'MAQ2502', 'MAQ2503')
        ]
        stats = import_poc_result(db, resultado, dry_run=False)

        assert stats.billings_created == 3
        assert db.query(Billing).count() == 3

    def test_mesma_placa_com_desconto_no_mesmo_boleto_soma_o_liquido(self, db):
        # SGR-01 — caso real (MGU2E86 em 07/2026): mensalidade, desconto e
        # serviço no mesmo documento, mesma placa e competência. Antes o
        # desconto era descartado e o MasterSat ficava com R$ 169,99 de um
        # boleto de R$ 128,34. Agora o desconto abate a mensalidade da placa
        # e a soma das cobranças é exatamente o total do documento.
        resultado = _resultado()
        resultado.clients[0].billings = [
            {'external_id': '19133', 'amount': valor, 'vehicle_plate': 'ABC1234',
             'period_label': '07/2026', 'due_date': '2026-07-15', 'payment_date': None,
             'paid_amount': None, 'payment_method': None, 'receipt_number': '19133',
             'installment_number': None, 'installment_total': None, 'status': 'paga',
             'billing_type': tipo, 'produto': produto,
             'title': 'Boleto SGR 19133 - ABC1234', 'sgr_payload': {'cod_boleto': '19133', 'valor': '128,34'}}
            for valor, tipo, produto in ((49.99, 'recorrente', 'MENSALIDADE'),
                                         (-41.65, 'prorata', 'DESCONTO PRORATA'),
                                         (120.00, 'avulsa', 'SERVICO'))
        ]
        stats = import_poc_result(db, resultado, dry_run=False)

        assert stats.billings_created == 2
        assert stats.descontos_centavos == 4165
        assert not any('negativo' in motivo for motivo in stats.skips)
        valores = {b.billing_type: b.amount for b in db.query(Billing).all()}
        assert valores == {'recorrente': Decimal('8.34'), 'avulsa': Decimal('120.00')}
        assert sum(valores.values()) == Decimal('128.34')

    def test_boleto_nao_pago_entra_sem_valor_pago(self, db):
        # O SGR manda valor_pagamento '0,00' em boleto não pago. Gravar 0.0
        # viola BillingOut (paid_amount > 0) e derruba a LISTAGEM inteira de
        # cobranças com 500 — não apenas o registro ruim.
        import_poc_result(db, self._com_boleto(paid_amount=0.0), dry_run=False)
        assert db.query(Billing).one().paid_amount is None

    def test_toda_cobranca_importada_serializa_na_api(self, db):
        """Trava de regressão: o que o importador grava, a listagem devolve."""
        from app.api.v1.endpoints.billings import base_query, serialize_billing

        resultado = self._com_boleto(sgr_payload={'cod_boleto': '7370', 'valor': '8,34'})
        resultado.clients[0].billings.append({
            **resultado.clients[0].billings[0],
            'amount': -41.65, 'paid_amount': 0.0, 'title': 'Boleto SGR 211558 - ABC1234 (ajuste)',
            'produto': 'DESCONTO',
        })
        import_poc_result(db, resultado, dry_run=False)

        linhas = base_query(db).all()
        assert linhas
        for linha in linhas:
            serialize_billing(linha)


class TestNotasFiscais:
    def _com_nota(self, **over):
        resultado = _resultado()
        resultado.clients[0].invoices = [
            {'cod_boleto': '10200', 'numero_nf': '2210', 'data_emissao': '01/06/2022',
             'url': 'https://gateway.exemplo/nf/2210/xml', 'erro': None, **over},
        ]
        return resultado

    def test_nota_sem_xml_e_contabilizada_como_ignorada(self, db):
        # O SGR devolve resposta inválida para algumas notas. Antes isso virava
        # só um aviso interno e a nota sumia do resumo sem explicação.
        stats = import_poc_result(db, self._com_nota(
            url=None, erro='SGRInvalidResponseError ao pedir o XML ao SGR',
        ), dry_run=False)

        assert stats.invoices_created == 0
        assert stats.invoices_skipped == 1
        assert any('2210' in motivo for motivo in stats.skips)

    def test_dry_run_nao_baixa_nem_grava_no_storage(self, db, monkeypatch):
        # O rollback desfaz o banco, mas não removeria o objeto do MinIO.
        from app.core.config import settings
        monkeypatch.setattr(settings, 'sgr_download_hosts', 'gateway.exemplo')
        stats = import_poc_result(db, self._com_nota(), dry_run=True)
        assert stats.invoices_created == 1
        assert db.query(Document).count() == 0


class TestDocumentoDoBoleto:
    """O SGR só devolve link/linha digitável enquanto o boleto está ABERTO;
    depois de baixado não há mais documento, só a baixa."""

    def _com_boleto(self, **over):
        boleto = {'external_id': '17946', 'amount': 64.99, 'vehicle_plate': 'ABC1234',
                  'period_label': '09/2026', 'due_date': '2026-09-10', 'payment_date': None,
                  'paid_amount': None, 'payment_method': None, 'receipt_number': '17946',
                  'installment_number': None, 'installment_total': None, 'status': 'pendente',
                  'title': 'Boleto SGR 17946 - ABC1234',
                  'sgr_payload': {'cod_boleto': '17946', 'valor': '64,99'}, **over}
        resultado = _resultado()
        resultado.clients[0].billings = [boleto]
        return resultado

    def test_boleto_aberto_guarda_linha_digitavel_na_cobranca(self, db):
        import_poc_result(db, self._com_boleto(
            linha_digitavel='34191.09685 07422.520937 75008.900005 6 15650000006499',
            cod_barras='34191096850742252093775008900005615650000006499',
        ), dry_run=False)

        notes = db.query(Billing).one().notes
        assert 'Linha digitável (SGR)' in notes
        assert '34191.09685' in notes
        # o aviso evita que alguém reenvie um boleto que o SGR ainda cobra
        assert 'não reenviar' in notes

    def test_boleto_pago_nao_ganha_aviso_de_reenvio(self, db):
        import_poc_result(db, self._com_boleto(), dry_run=False)
        notes = db.query(Billing).one().notes
        assert notes == 'Importado do SGR (Hinova).'

    def test_pdf_do_boleto_nao_e_baixado_em_dry_run(self, db, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, 'sgr_download_hosts', 'sgr.exemplo')
        stats = import_poc_result(db, self._com_boleto(
            link_boleto='https://sgr.exemplo/boleto/abc?download=true',
        ), dry_run=True)
        assert stats.boletos_created == 1
        assert db.query(Document).count() == 0
