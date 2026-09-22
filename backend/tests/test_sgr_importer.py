"""
Importação SGR -> MasterSat: só insere o que falta, nunca duplica e nunca
sobrescreve dado que já está no MasterSat.
"""
from __future__ import annotations

from decimal import Decimal

from app.models.client import Client
from app.models.contract import Contract
from app.models.enums import ClientStatus, TrackerStatus, VehicleStatus
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
