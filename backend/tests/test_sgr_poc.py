"""
Testes de orquestração da POC (ETAPA 7): caminha cliente -> veículo ->
equipamento sem assumir que todo cliente tem veículo nem que todo veículo
tem equipamento, e sem que 1 registro com erro derrube a POC inteira.

Usa um dublê de SGRClient (duck typing — poc.py não faz isinstance) para não
depender de rede nem da forma exata dos payloads reais.
"""
from __future__ import annotations

import pytest

from app.services.sgr_migration.client import RequestLogEntry, SGRApiError
from app.services.sgr_migration.poc import check_connectivity, run_poc


class FakeClient:
    def __init__(self, clientes, veiculos_por_cliente, vinculos_por_placa, veiculo_erro_para=None):
        self._clientes = clientes
        self._veiculos_por_cliente = veiculos_por_cliente
        self._vinculos_por_placa = vinculos_por_placa
        self._veiculo_erro_para = veiculo_erro_para or set()
        self.request_count = 0
        self.request_log: list[RequestLogEntry] = []

    def _track(self, path):
        self.request_count += 1
        self.request_log.append(RequestLogEntry(method='GET', path=path, status_code=200))

    def buscar_clientes(self, total, indice=0):
        self._track('/buscar_cliente')
        return self._clientes[:total]

    def buscar_veiculos_por_cliente(self, cod_cliente, total=200, indice=0):
        self._track('/buscar_veiculo')
        if cod_cliente in self._veiculo_erro_para:
            raise SGRApiError('erro simulado', status_code=500)
        return self._veiculos_por_cliente.get(cod_cliente, [])

    def buscar_vinculos_por_placa(self, placa, total=200, indice=0):
        self._track('/buscar_vinculo')
        return self._vinculos_por_placa.get(placa, [])

    def authenticate(self):
        self._track('/headers_authorization')

    def get(self, path, params=None):
        self._track(path)
        return {'Error': 'false', 'Data': [{'cod_cliente': '1'}]}

    def extract_data(self, body):
        return body.get('Data', [])


CLIENTE_A = {'cod_cliente': '1', 'nome_cliente': 'Cliente A', 'cpf_cliente': '11144477735'}
CLIENTE_B = {'cod_cliente': '2', 'nome_cliente': 'Cliente B', 'cpf_cliente': '11222333000181'}

VEICULO_1 = {'cod_veiculo': '10', 'cod_cliente': '1', 'placa_veiculo': 'AAA1111'}
VEICULO_2 = {'cod_veiculo': '11', 'cod_cliente': '1', 'placa_veiculo': 'BBB2222'}

TRACKER_1 = {'Cod_equipamento': '100', 'Imei_equipamento': '999999999999999'}


class TestRunPocRelationships:
    def test_client_with_two_vehicles_one_without_tracker(self):
        client = FakeClient(
            clientes=[CLIENTE_A],
            veiculos_por_cliente={'1': [VEICULO_1, VEICULO_2]},
            vinculos_por_placa={'AAA1111': [TRACKER_1]},
        )
        result = run_poc(client, limit=10)

        assert len(result.clients) == 1
        node = result.clients[0]
        assert len(node.vehicles) == 2
        assert len(node.vehicles[0].trackers) == 1
        assert len(node.vehicles[1].trackers) == 0
        assert any('sem equipamento' in i for i in node.vehicles[1].issues)
        assert not any('sem veículo' in i for i in node.issues)

    def test_client_without_vehicles_is_not_discarded_and_is_flagged(self):
        client = FakeClient(clientes=[CLIENTE_B], veiculos_por_cliente={}, vinculos_por_placa={})
        result = run_poc(client, limit=10)

        assert len(result.clients) == 1
        node = result.clients[0]
        assert node.vehicles == []
        assert any('sem veículo' in i for i in node.issues)

    def test_vehicle_fetch_failure_does_not_abort_the_whole_run(self):
        client = FakeClient(
            clientes=[CLIENTE_A, CLIENTE_B],
            veiculos_por_cliente={'2': [VEICULO_1]},
            vinculos_por_placa={},
            veiculo_erro_para={'1'},
        )
        result = run_poc(client, limit=10)

        assert len(result.clients) == 2
        client_a_node = next(c for c in result.clients if c.mapped.get('external_id') == '1')
        assert client_a_node.vehicles == []
        assert any('Falha ao buscar veículos' in i for i in client_a_node.issues)
        # o segundo cliente continua sendo processado normalmente
        client_b_node = next(c for c in result.clients if c.mapped.get('external_id') == '2')
        assert len(client_b_node.vehicles) == 1

    def test_client_mapping_exception_does_not_abort_the_whole_run(self, monkeypatch):
        def _boom(_raw):
            raise ValueError('payload corrompido')

        import app.services.sgr_migration.poc as poc_module

        original = poc_module.map_cliente

        def _map_or_boom(raw):
            if raw is CLIENTE_A:
                return _boom(raw)
            return original(raw)

        monkeypatch.setattr(poc_module, 'map_cliente', _map_or_boom)

        client = FakeClient(clientes=[CLIENTE_A, CLIENTE_B], veiculos_por_cliente={}, vinculos_por_placa={})
        result = run_poc(client, limit=10)

        assert len(result.clients) == 2
        assert result.clients[0].fetch_failed is True
        assert result.clients[1].fetch_failed is False

    def test_request_count_and_log_are_propagated(self):
        client = FakeClient(
            clientes=[CLIENTE_A],
            veiculos_por_cliente={'1': [VEICULO_1]},
            vinculos_por_placa={'AAA1111': [TRACKER_1]},
        )
        result = run_poc(client, limit=10)

        # 1 (clientes) + 1 (veiculos do cliente 1) + 1 (vinculo da placa AAA1111)
        assert result.request_count == 3
        assert [e.path for e in result.request_log] == ['/buscar_cliente', '/buscar_veiculo', '/buscar_vinculo']

    def test_never_fetches_more_clients_than_the_limit(self):
        client = FakeClient(clientes=[CLIENTE_A, CLIENTE_B], veiculos_por_cliente={}, vinculos_por_placa={})
        result = run_poc(client, limit=1)
        assert len(result.clients) == 1


class TestCheckConnectivity:
    def test_returns_authenticated_and_request_count(self):
        client = FakeClient(clientes=[], veiculos_por_cliente={}, vinculos_por_placa={})
        result = check_connectivity(client)
        assert result['autenticado'] is True
        assert result['registros_retornados'] == 1
        assert result['requisicoes'] == 2  # authenticate + get
