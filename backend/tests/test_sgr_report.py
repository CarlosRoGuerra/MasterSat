"""
Testes do relatório de compatibilidade (ETAPAS 10/11/13): contagens,
duplicidades e mascaramento de dados sensíveis no JSON/texto final.
"""
from __future__ import annotations

from app.services.sgr_migration.client import RequestLogEntry
from app.services.sgr_migration.poc import ClientNode, PocRunResult, TrackerNode, VehicleNode
from app.services.sgr_migration.report import build_report, render_text_report


def _client(external_id, cpf=None, email=None, phone=None, vehicles=None, issues=None):
    return ClientNode(
        raw={},
        mapped={'external_id': external_id, 'name': f'Cliente {external_id}', 'cpf_cnpj': cpf, 'email': email,
                'extra_emails': None, 'phone': phone},
        issues=issues or [],
        vehicles=vehicles or [],
    )


def _vehicle(external_id, plate, trackers=None, issues=None):
    return VehicleNode(
        raw={}, mapped={'external_id': external_id, 'plate': plate, 'brand': 'FIAT', 'model': 'ARGO'},
        issues=issues or [], trackers=trackers or [],
    )


def _tracker(external_id, imei, issues=None):
    return TrackerNode(raw={}, mapped={'external_id': external_id, 'imei': imei, 'brand': 'SUNTECH', 'model': 'ST310'},
                        issues=issues or [])


class TestBuildReportCounts:
    def test_counts_clients_vehicles_and_trackers(self):
        result = PocRunResult(
            limit=10,
            clients=[
                _client('1', cpf='11144477735', email='a@x.com', phone='31988887777',
                        vehicles=[_vehicle('10', 'AAA1111', trackers=[_tracker('100', '999999999999999')])]),
                _client('2', cpf='11222333000181', email='b@x.com', phone='31977776666'),
            ],
            request_count=3,
            request_log=[RequestLogEntry('GET', '/buscar_cliente', 200)],
        )
        report = build_report(result)

        assert report['clientes']['consultados'] == 2
        assert report['clientes']['processados'] == 2
        assert report['clientes']['com_erro'] == 0
        assert report['clientes']['sem_veiculo'] == 1
        assert report['veiculos']['encontrados'] == 1
        assert report['veiculos']['sem_equipamento'] == 0
        assert report['equipamentos']['encontrados'] == 1
        assert report['requisicoes']['total'] == 3

    def test_fetch_failed_client_counts_as_error(self):
        result = PocRunResult(
            limit=10,
            clients=[ClientNode(raw={}, mapped={}, issues=['boom'], fetch_failed=True)],
            request_count=1,
            request_log=[],
        )
        report = build_report(result)
        assert report['clientes']['com_erro'] == 1
        assert report['clientes']['processados'] == 0


class TestDuplicateDetection:
    def test_detects_duplicate_cpf(self):
        result = PocRunResult(
            limit=10,
            clients=[
                _client('1', cpf='11144477735'),
                _client('2', cpf='11144477735'),
            ],
            request_count=1,
            request_log=[],
        )
        report = build_report(result)
        assert '11144477735' in report['duplicidades']['cpf_cnpj']
        assert set(report['duplicidades']['cpf_cnpj']['11144477735']) == {'1', '2'}
        assert any('cpf_cnpj duplicado' in p for p in report['problemas'])

    def test_detects_duplicate_plate_across_clients(self):
        v1 = _vehicle('10', 'AAA1111')
        v2 = _vehicle('11', 'AAA1111')
        result = PocRunResult(
            limit=10,
            clients=[_client('1', vehicles=[v1]), _client('2', vehicles=[v2])],
            request_count=1,
            request_log=[],
        )
        report = build_report(result)
        assert 'AAA1111' in report['duplicidades']['placa']

    def test_no_false_positive_when_values_absent(self):
        result = PocRunResult(
            limit=10,
            clients=[_client('1'), _client('2')],
            request_count=1,
            request_log=[],
        )
        report = build_report(result)
        assert report['duplicidades']['cpf_cnpj'] == {}
        assert report['duplicidades']['email'] == {}


class TestMaskingInReport:
    def test_email_and_phone_duplicates_are_masked(self):
        result = PocRunResult(
            limit=10,
            clients=[
                _client('1', email='joao@email.com', phone='31999998888'),
                _client('2', email='joao@email.com', phone='31999998888'),
            ],
            request_count=1,
            request_log=[],
        )
        report = build_report(result)
        assert 'joao@email.com' not in report['duplicidades']['email']
        assert any(k.startswith('j***@') for k in report['duplicidades']['email'])
        assert '31999998888' not in report['duplicidades']['telefone']

    def test_tree_masks_cpf_phone_and_imei(self):
        result = PocRunResult(
            limit=10,
            clients=[_client('1', cpf='11144477735', phone='31999998888',
                              vehicles=[_vehicle('10', 'AAA1111', trackers=[_tracker('100', '355488020902005')])])],
            request_count=1,
            request_log=[],
        )
        report = build_report(result)
        node = report['arvore'][0]
        assert node['cpf_cnpj_mascarado'] == '*********35'
        assert node['telefone_mascarado'] == '*******8888'
        tracker_node = node['veiculos'][0]['equipamentos'][0]
        assert tracker_node['imei_mascarado'] == '***********2005'
        # nunca deve serializar o CPF/telefone/IMEI em claro em nenhuma chave da árvore
        import json
        serialized = json.dumps(node)
        assert '11144477735' not in serialized
        assert '31999998888' not in serialized
        assert '355488020902005' not in serialized


class TestRenderTextReport:
    def test_render_includes_all_sections(self):
        result = PocRunResult(limit=10, clients=[_client('1')], request_count=1, request_log=[])
        report = build_report(result)
        text = render_text_report(report)
        assert 'CLIENTES' in text
        assert 'VEÍCULOS' in text
        assert 'EQUIPAMENTOS' in text
        assert 'COMPATIBILIDADE' in text
        assert 'PROBLEMAS' in text
        assert 'REQUISIÇÕES' in text
        assert 'POC concluída.' in text
