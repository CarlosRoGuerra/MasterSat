"""
Testes de orquestração da POC (ETAPA 7): caminha cliente -> veículo ->
equipamento sem assumir que todo cliente tem veículo nem que todo veículo
tem equipamento, e sem que 1 registro com erro derrube a POC inteira.

Usa um dublê de SGRClient (duck typing — poc.py não faz isinstance) para não
depender de rede nem da forma exata dos payloads reais.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.services.sgr_migration.client import RequestLogEntry, SGRApiError
from app.services.sgr_migration.poc import check_connectivity, run_poc


class FakeClient:
    def __init__(self, clientes, veiculos_por_cliente, vinculos_por_placa,
                 veiculo_erro_para=None, rastreadores=None, grupos=None, vencimentos=None,
                 grupos_adesao=None):
        self._clientes = clientes
        self._veiculos_por_cliente = veiculos_por_cliente
        self._vinculos_por_placa = vinculos_por_placa
        self._veiculo_erro_para = veiculo_erro_para or set()
        self._rastreadores = rastreadores or []
        self._grupos = grupos or []
        self._grupos_adesao = grupos_adesao or []
        self._vencimentos = vencimentos or []
        self.request_count = 0
        self.request_log: list[RequestLogEntry] = []

    def buscar_rastreadores(self, total=200, indice=0):
        self._track('/buscar_rastreador')
        return self._rastreadores[indice:indice + total]

    def get_grupo_mensalidade(self, total=200, indice=0):
        self._track('/get_grupo_mensalidade')
        return self._grupos

    def get_grupo_adesao(self, total=200, indice=0):
        self._track('/get_grupo_adesao')
        return self._grupos_adesao

    def get_vencimento(self, cod_cliente=None):
        self._track('/get_vencimento')
        return self._vencimentos

    def _track(self, path):
        self.request_count += 1
        self.request_log.append(RequestLogEntry(method='GET', path=path, status_code=200))

    def buscar_clientes(self, total, indice=0):
        self._track('/buscar_cliente')
        return self._clientes[indice:indice + total]

    def buscar_veiculos_por_cliente(self, cod_cliente, total=200, indice=0):
        self._track('/buscar_veiculo')
        if cod_cliente in self._veiculo_erro_para:
            raise SGRApiError('erro simulado', status_code=500)
        return self._veiculos_por_cliente.get(cod_cliente, [])[indice:indice + total]

    def buscar_vinculos_por_placa(self, placa, total=200, indice=0):
        self._track('/buscar_vinculo')
        return self._vinculos_por_placa.get(placa, [])[indice:indice + total]

    def authenticate(self):
        self._track('/headers_authorization')

    def get(self, path, params=None):
        self._track(path)
        return {'Error': 'false', 'Data': [{'cod_cliente': '1'}]}

    def extract_data(self, body):
        return body.get('Data', [])


# situacao ATIVO em ambos: os testes de relacionamento (veículo/rastreador)
# não são sobre elegibilidade — sem isto, o novo filtro de situação (ver
# TestClientesElegiveis) excluiria os dois por "situação ausente" e
# quebraria tudo que depende deles aparecerem em result.clients.
CLIENTE_A = {
    'cod_cliente': '1', 'nome_cliente': 'Cliente A', 'cpf_cliente': '11144477735',
    'situacao': {'descricao': 'ATIVO'},
}
CLIENTE_B = {
    'cod_cliente': '2', 'nome_cliente': 'Cliente B', 'cpf_cliente': '11222333000181',
    'situacao': {'descricao': 'ATIVO'},
}

VEICULO_1 = {'cod_veiculo': '10', 'cod_cliente': '1', 'placa_veiculo': 'AAA1111', 'situacao_veiculo': 'ATIVO'}
VEICULO_2 = {'cod_veiculo': '11', 'cod_cliente': '1', 'placa_veiculo': 'BBB2222', 'situacao_veiculo': 'ATIVO'}

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

        # Página curta não prova o fim (SGR-05): cada coleção paginada pede a
        # página seguinte, que tem de vir vazia. Clientes 2 + rastreadores 1
        # (1ª página já vazia) + 3 tabelas de domínio (1 chamada cada,
        # independente do tamanho da base) + veículos 2 + vínculos 2.
        assert result.request_count == 10
        assert [e.path for e in result.request_log] == [
            '/buscar_cliente', '/buscar_cliente', '/buscar_rastreador', '/get_grupo_mensalidade',
            '/get_grupo_adesao', '/get_vencimento', '/buscar_veiculo', '/buscar_veiculo',
            '/buscar_vinculo', '/buscar_vinculo',
        ]

    def test_never_fetches_more_clients_than_the_limit(self):
        client = FakeClient(clientes=[CLIENTE_A, CLIENTE_B], veiculos_por_cliente={}, vinculos_por_placa={})
        result = run_poc(client, limit=1)
        assert len(result.clients) == 1


def _cliente(cod, situacao):
    return {
        'cod_cliente': cod, 'nome_cliente': f'Cliente {cod}', 'cpf_cliente': '11144477735',
        'situacao': {'descricao': situacao},
    }


class TestClientesElegiveis:
    """Só migra cliente ATIVO/INADIMPLENTE (ver _SITUACOES_MIGRAVEIS em
    poc.py) — cancelado, suspenso, inativo ou só cadastrado não deveria virar
    contrato/cobrança no MasterSat."""

    def test_pula_cliente_cancelado_suspenso_inativo_e_cadastrado(self):
        clientes = [
            _cliente('1', 'ATIVO'),
            _cliente('2', 'CANCELADO'),
            _cliente('3', 'SUSPENSO'),
            _cliente('4', 'INATIVO'),
            _cliente('5', 'CADASTRADO'),
            _cliente('6', 'INADIMPLENTE'),
        ]
        client = FakeClient(clientes=clientes, veiculos_por_cliente={}, vinculos_por_placa={})
        result = run_poc(client, limit=10)

        codigos = {c.mapped.get('external_id') for c in result.clients}
        assert codigos == {'1', '6'}
        assert result.clients_out_of_scope == {'CANCELADO': 1, 'SUSPENSO': 1, 'INATIVO': 1, 'CADASTRADO': 1}

    def test_ativado_e_tratado_como_sinonimo_de_ativo(self):
        # 1 ocorrência em 520 clientes reais — tudo indica erro de digitação
        # do mesmo estado 'ATIVO'.
        client = FakeClient(clientes=[_cliente('1', 'ATIVADO')], veiculos_por_cliente={}, vinculos_por_placa={})
        result = run_poc(client, limit=10)
        assert len(result.clients) == 1
        assert result.clients_out_of_scope == {}

    def test_cliente_sem_situacao_fica_fora_do_escopo(self):
        sem_situacao = {'cod_cliente': '9', 'nome_cliente': 'Sem situação', 'cpf_cliente': '11144477735'}
        client = FakeClient(clientes=[sem_situacao], veiculos_por_cliente={}, vinculos_por_placa={})
        result = run_poc(client, limit=10)
        assert result.clients == []
        assert result.clients_out_of_scope == {'(SEM SITUAÇÃO)': 1}

    def test_limit_conta_elegiveis_nao_lidos(self):
        # limit=2 com 1 elegível no meio de 3 cancelados: lê os 4, migra só 1.
        clientes = [
            _cliente('1', 'CANCELADO'),
            _cliente('2', 'ATIVO'),
            _cliente('3', 'CANCELADO'),
            _cliente('4', 'CANCELADO'),
        ]
        client = FakeClient(clientes=clientes, veiculos_por_cliente={}, vinculos_por_placa={})
        result = run_poc(client, limit=2)

        assert len(result.clients) == 1
        assert result.clients[0].mapped.get('external_id') == '2'
        assert result.clients_out_of_scope == {'CANCELADO': 3}

    def test_pagina_alem_da_primeira_ate_juntar_o_limite(self, monkeypatch):
        import app.services.sgr_migration.poc as poc_mod
        monkeypatch.setattr(poc_mod, '_PAGINA_CLIENTES', 2)

        # 5 clientes, página de 2: precisa continuar paginando além da 1ª
        # página (que só tem 1 elegível) para achar os 2 pedidos.
        clientes = [
            _cliente('1', 'ATIVO'),
            _cliente('2', 'CANCELADO'),
            _cliente('3', 'ATIVO'),
            _cliente('4', 'CANCELADO'),
            _cliente('5', 'ATIVO'),
        ]
        client = FakeClient(clientes=clientes, veiculos_por_cliente={}, vinculos_por_placa={})
        result = run_poc(client, limit=2)

        assert {c.mapped.get('external_id') for c in result.clients} == {'1', '3'}
        # Para assim que junta o limite, no MEIO da 2ª página: o cliente 4
        # (CANCELADO, mesma página do 3) nunca chega a ser examinado, e o 5
        # nunca é lido. Por isso só 1 CANCELADO é contado, não os 2 que
        # existem na lista.
        assert result.clients_out_of_scope == {'CANCELADO': 1}


class TestVeiculosAtivos:
    def _run(self, veiculos):
        client = FakeClient(
            clientes=[CLIENTE_A], veiculos_por_cliente={'1': veiculos}, vinculos_por_placa={},
        )
        return run_poc(client, limit=1)

    def test_so_ativo_e_inadimplente_entram(self):
        veiculos = [
            {'cod_veiculo': '1', 'placa_veiculo': 'AAA1111', 'situacao_veiculo': 'ATIVO'},
            {'cod_veiculo': '2', 'placa_veiculo': 'BBB2222', 'situacao_veiculo': 'INADIMPLENTE'},
            {'cod_veiculo': '3', 'placa_veiculo': 'CCC3333', 'situacao_veiculo': 'CANCELADO'},
            {'cod_veiculo': '4', 'placa_veiculo': 'DDD4444', 'situacao_veiculo': 'RETIRADA'},
            {'cod_veiculo': '5', 'placa_veiculo': 'EEE5555', 'situacao_veiculo': 'cadastrado'},
            {'cod_veiculo': '6', 'placa_veiculo': 'FFF6666'},
        ]
        result = self._run(veiculos)
        placas = [v.mapped['plate'] for v in result.clients[0].vehicles]
        assert placas == ['AAA1111', 'BBB2222']
        assert result.vehicles_out_of_scope == {
            'CANCELADO': 1, 'RETIRADA': 1, 'CADASTRADO': 1, '(SEM SITUAÇÃO)': 1,
        }

    def test_nao_busca_vinculo_de_veiculo_fora_do_escopo(self):
        result = self._run([{'cod_veiculo': '3', 'placa_veiculo': 'CCC3333', 'situacao_veiculo': 'CANCELADO'}])
        assert not any(e.path == '/buscar_vinculo' for e in result.request_log)

    def test_cliente_so_com_veiculos_cancelados_e_sinalizado(self):
        result = self._run([{'cod_veiculo': '3', 'placa_veiculo': 'CCC3333', 'situacao_veiculo': 'CANCELADO'}])
        assert result.clients[0].vehicles == []
        assert 'Cliente sem veículo ativo' in result.clients[0].issues


class TestCheckConnectivity:
    def test_returns_authenticated_and_request_count(self):
        client = FakeClient(clientes=[], veiculos_por_cliente={}, vinculos_por_placa={})
        result = check_connectivity(client)
        assert result['autenticado'] is True
        assert result['registros_retornados'] == 1
        assert result['requisicoes'] == 2  # authenticate + get


class TestVarreduraPorPeriodo:
    """Um mês que falha some INTEIRO do histórico. Isso precisa falhar alto:
    foi assim que 846 cobranças sumiram sem ninguém perceber."""

    class ClienteInstavel:
        def __init__(self, falhar_em=(), falhas_antes_de_ok=0):
            self.falhar_em = set(falhar_em)
            self.falhas_antes_de_ok = falhas_antes_de_ok
            self.tentativas: dict[str, int] = {}

        def buscar_boletos_periodo(self, inicio, fim, campo='vencimento', linha_digitavel=False):
            mes = inicio[:7]
            self.tentativas[mes] = self.tentativas.get(mes, 0) + 1
            if mes in self.falhar_em:
                raise SGRApiError('indisponível', status_code=502)
            if self.tentativas[mes] <= self.falhas_antes_de_ok:
                raise SGRApiError('timeout', status_code=None)
            return [{'cod_boleto': f'{mes}-1', 'cod_cliente': '7'}]

    def test_mes_que_falha_sempre_interrompe_a_importacao(self):
        from app.services.sgr_migration.poc import SGRIncompleteScan, fetch_boletos_por_periodo

        cliente = self.ClienteInstavel(falhar_em={'2026-02'})
        with pytest.raises(SGRIncompleteScan, match='02/2026'):
            fetch_boletos_por_periodo(cliente, date(2026, 1, 1), date(2026, 3, 1), [])

    def test_falha_passageira_e_repetida_ate_dar_certo(self, monkeypatch):
        from app.services.sgr_migration import poc as poc_mod

        monkeypatch.setattr(poc_mod, '_ESPERA_ENTRE_TENTATIVAS', 0)
        cliente = self.ClienteInstavel(falhas_antes_de_ok=2)
        por_cliente = poc_mod.fetch_boletos_por_periodo(cliente, date(2026, 1, 1), date(2026, 1, 1), [])

        assert len(por_cliente['7']) == 1
        assert cliente.tentativas['2026-01'] == 3

    def test_indexa_por_cod_cliente(self, monkeypatch):
        from app.services.sgr_migration import poc as poc_mod

        monkeypatch.setattr(poc_mod, '_ESPERA_ENTRE_TENTATIVAS', 0)
        por_cliente = poc_mod.fetch_boletos_por_periodo(
            self.ClienteInstavel(), date(2026, 1, 1), date(2026, 3, 1), [],
        )
        assert len(por_cliente['7']) == 3  # jan, fev, mar
