"""
Testes de mapeamento SGR → MasterSat (ETAPA 9) e de detecção de problemas de
qualidade de dado (ETAPA 11). O payload de cliente usado é o "Exemplo
Retorno" literal da doc oficial (/doc/api_data.js, grupo clienteBuscar) —
não foi inventado.
"""
from __future__ import annotations

from app.models.enums import ClientStatus, TrackerStatus, VehicleStatus
from app.services.sgr_migration.mapping import (
    ci_get, map_billing_type, map_cliente, map_tracker, map_veiculo,
)

CLIENTE_DOC_EXAMPLE = {
    "cod_cliente": "11946",
    "nota_fiscal_tipo_envio_cliente": None,
    "codigo_secreto_cliente": None,
    "profissao_cliente": None,
    "contato_cliente": None,
    "data_nascimento_cliente": "1995-03-10",
    "idade": "27",
    "formato_boleto_cliente": "U",
    "login_cliente": None,
    "senha_cliente": "24a6e97b043126b4",
    "matriz_filial": "SGRV2 -  DESENVOLVIMENTO",
    "nome_cliente": "TESTE HINOVA BOLETAO",
    "numero_contrato_cliente": "1000",
    "rg_cliente": None,
    "cnh_cliente": None,
    "inscricao_municipal_cliente": None,
    "segmento_cliente": None,
    "senha_cliente_empresa_cliente": None,
    "reter_iss_cliente": "N",
    "porcentagem_iss_cliente": "0.0000",
    "optante_simples_cliente": "N",
    "nota_fiscal_cliente": "N",
    "taxa_emissao_boleto_cliente": "N",
    "nome_fantasia_cliente": None,
    "sexo_cliente": None,
    "estado_civil_cliente": None,
    "ultima_atualizacao": "2021-10-07 11:42:13",
    "cpf_cliente": "721.307.080-05",
    "situacao": {"descricao": "ATIVO"},
    "endereco": {
        "bairro": "PAQUETA",
        "cep": "31340290",
        "cidade": "BELO HORIZONTE",
        "complemento": None,
        "logradouro": "RUA FREI MARTINHO BURNIER",
        "numero": "120",
        "uf": "MG",
        "codigoIbge": "3106200",
    },
    "contatos": [],
    "telefone": [],
    "email": [{"Departamento": "TODOS", "email": "carlos.silva@hinova.com.br", "situacao": "ATIVO"}],
    "obs": [],
    "formato_envio_titulo_cliente": [],
}


class TestMapCliente:
    def test_maps_core_identity_fields(self):
        mapped, _issues = map_cliente(CLIENTE_DOC_EXAMPLE)
        assert mapped['external_id'] == '11946'
        assert mapped['name'] == 'TESTE HINOVA BOLETAO'
        assert mapped['cpf_cnpj'] == '72130708005'
        assert mapped['type'] == 'pf'

    def test_maps_address(self):
        mapped, _issues = map_cliente(CLIENTE_DOC_EXAMPLE)
        assert mapped['zip_code'] == '31340290'
        assert mapped['city'] == 'BELO HORIZONTE'
        assert mapped['state'] == 'MG'

    def test_maps_status_from_situacao_descricao(self):
        mapped, _issues = map_cliente(CLIENTE_DOC_EXAMPLE)
        assert mapped['status'] == ClientStatus.ACTIVE.value

    def test_maps_first_email(self):
        mapped, _issues = map_cliente(CLIENTE_DOC_EXAMPLE)
        assert mapped['email'] == 'carlos.silva@hinova.com.br'
        assert mapped['extra_emails'] is None

    def test_contato_sem_nome_usa_descricao_e_valida_no_schema(self):
        from app.schemas.client import ContactItem
        raw = {**CLIENTE_DOC_EXAMPLE, 'contatos': [
            {'descricao': 'CELULAR', 'contato': '(47) 99999-8888', 'nome': None},
            {'descricao': 'RECADO', 'contato': None, 'nome': None},
            {'descricao': None, 'contato': '4733334444', 'nome': None},
        ]}
        mapped, _issues = map_cliente(raw)
        assert [c['name'] for c in mapped['contacts']] == ['CELULAR', 'Contato']
        for c in mapped['contacts']:
            ContactItem.model_validate(c)

    def test_parses_iso_birth_date(self):
        mapped, _issues = map_cliente(CLIENTE_DOC_EXAMPLE)
        assert mapped['birth_date'] == '1995-03-10'

    def test_flags_missing_phone_as_issue(self):
        _mapped, issues = map_cliente(CLIENTE_DOC_EXAMPLE)
        assert any('telefone' in i for i in issues)

    def test_valid_cpf_does_not_raise_issue(self):
        _mapped, issues = map_cliente(CLIENTE_DOC_EXAMPLE)
        assert not any('dígito verificador' in i for i in issues)

    def test_invalid_cpf_is_flagged(self):
        raw = {**CLIENTE_DOC_EXAMPLE, 'cpf_cliente': '111.111.111-11'}
        _mapped, issues = map_cliente(raw)
        assert any('dígito verificador' in i for i in issues)

    def test_missing_cpf_is_flagged(self):
        raw = {**CLIENTE_DOC_EXAMPLE, 'cpf_cliente': None}
        _mapped, issues = map_cliente(raw)
        assert any('sem CPF/CNPJ' in i for i in issues)

    def test_unknown_situacao_falls_back_to_active_and_flags_issue(self):
        raw = {**CLIENTE_DOC_EXAMPLE, 'situacao': {'descricao': 'ALGO_NOVO'}}
        mapped, issues = map_cliente(raw)
        assert mapped['status'] == ClientStatus.ACTIVE.value
        assert any('sem mapeamento conhecido' in i for i in issues)

    def test_unparseable_birth_date_is_flagged(self):
        raw = {**CLIENTE_DOC_EXAMPLE, 'data_nascimento_cliente': '31-02-2020-x'}
        mapped, issues = map_cliente(raw)
        assert mapped['birth_date'] is None
        assert any('nascimento' in i for i in issues)

    def test_unknown_uf_is_flagged(self):
        raw = {**CLIENTE_DOC_EXAMPLE, 'endereco': {**CLIENTE_DOC_EXAMPLE['endereco'], 'uf': 'ZZ'}}
        _mapped, issues = map_cliente(raw)
        assert any('Estado (UF) desconhecido' in i for i in issues)

    def test_cnpj_length_is_detected_as_pj(self):
        raw = {**CLIENTE_DOC_EXAMPLE, 'cpf_cliente': '11.222.333/0001-81'}
        mapped, issues = map_cliente(raw)
        assert mapped['type'] == 'pj'
        assert not any('dígito verificador' in i for i in issues)

    def test_never_includes_password_field(self):
        mapped, _issues = map_cliente(CLIENTE_DOC_EXAMPLE)
        assert 'senha_cliente' not in mapped
        assert 'password' not in mapped


class TestMapVeiculo:
    def test_maps_plate_and_client_link(self):
        raw = {'cod_veiculo': '55', 'cod_cliente': '11946', 'placa_veiculo': 'abc-1234', 'marca_veiculo': 'FIAT'}
        mapped, issues = map_veiculo(raw)
        assert mapped['plate'] == 'ABC1234'
        assert mapped['client_external_id'] == '11946'
        assert mapped['brand'] == 'FIAT'
        assert not any('placa' in i for i in issues)

    def test_missing_plate_is_flagged(self):
        mapped, issues = map_veiculo({'cod_veiculo': '55'})
        assert mapped['plate'] is None
        assert any('placa' in i for i in issues)

    def test_default_status_is_active_when_no_situacao(self):
        mapped, _issues = map_veiculo({'placa_veiculo': 'ABC1234'})
        assert mapped['status'] == VehicleStatus.ACTIVE.value

    def test_ano_fora_da_faixa_e_descartado(self):
        # Caso real (STGT50): anofab/anomod = '2' derrubava GET /vehicles com 500.
        mapped, issues = map_veiculo({'placa_veiculo': 'STGT50', 'anofab_veiculo': '2', 'anomod_veiculo': '2020'})
        assert mapped['manufacture_year'] is None
        assert mapped['model_year'] == 2020
        assert any('manufacture_year' in i for i in issues)

    def test_campos_invalidos_descartados_e_resultado_valida_no_schema(self):
        from app.schemas.vehicle import VehicleOut
        raw = {
            'placa_veiculo': 'ABC1234', 'chassi_veiculo': '123', 'renavam_veiculo': '12',
            'cep_veiculo': '8922', 'uf_veiculo': 'SANTA CATARINA', 'fipe_valor_veiculo': '-10,00',
            'data_contrato_veiculo': '10/05/2024', 'data_final_contrato_veiculo': '01/01/2024',
        }
        mapped, _issues = map_veiculo(raw)
        for campo in ('chassis', 'renavam', 'address_zip_code', 'state', 'fipe_value', 'contract_end_date'):
            assert mapped[campo] is None, campo
        VehicleOut.model_validate({**mapped, 'id': 1, 'client_id': 1})

    def test_campos_validos_nao_sao_tocados(self):
        raw = {'placa_veiculo': 'ABC1234', 'chassi_veiculo': '9BWZZZ377VT004251', 'uf_veiculo': 'SC',
               'anofab_veiculo': '2019', 'anomod_veiculo': '2020'}
        mapped, issues = map_veiculo(raw)
        assert mapped['chassis'] == '9BWZZZ377VT004251'
        assert mapped['state'] == 'SC'
        assert mapped['manufacture_year'] == 2019
        assert not any('descartado' in i for i in issues)


class TestMapTracker:
    """Formas REAIS capturadas da API (a doc do fornecedor mostrava o exemplo
    de /buscar_equipamento no lugar do de /buscar_vinculo)."""

    VINCULO = {
        'cod_vinculo': '2',
        'cod_rastreador_vinculo': '3',
        'data_instalacao': '31/07/2024',
        'valor_instalacao': '150,00',
        'rastreador': {
            'cod_rastreador_vinculo': '3',
            'cod_equipamento_vinculo': '259',
            'cod_chip_vinculo': '3',
            'equipamento': {'numero_equipamento_vinculo': '912992'},
            'chip_equipamento': {'imei_chip_vinculo': '8955170000000005334'},
        },
        'veiculo': {'placa_vinculo': 'GRA4982', 'chassi_vinculo': '9BD1234'},
    }

    RASTREADOR = {
        'cod_rastreador': '1',
        'situacao': 'ATIVO',
        'disponibilidade': 'VINCULADO',
        'tipo_rastreador': 'NAO INFORMADO',
        'numero_equipamento': '7931096',
        'imei_equipamento': '355488020902005',
        'ddd': '55',
        'telefone': '999990864',
        'placa': 'GRA4982',
    }

    def test_links_vehicle_equipment_and_chip_from_vinculo(self):
        mapped, _issues = map_tracker(self.VINCULO, self.RASTREADOR)
        assert mapped['vehicle_plate'] == 'GRA4982'
        assert mapped['external_id'] == '3'
        assert mapped['equipment_external_id'] == '259'
        assert mapped['chip_external_id'] == '3'
        assert mapped['vinculo_external_id'] == '2'

    def test_imei_comes_from_rastreador_not_from_vinculo(self):
        # O vínculo só tem o IMEI do CHIP; o do equipamento vem do rastreador.
        mapped, issues = map_tracker(self.VINCULO, self.RASTREADOR)
        assert mapped['imei'] == '355488020902005'
        assert mapped['sim_imei'] == '8955170000000005334'
        assert not any('IMEI' in i for i in issues)

    def test_missing_rastreador_flags_absent_imei(self):
        mapped, issues = map_tracker(self.VINCULO, None)
        assert mapped['imei'] is None
        assert any('IMEI' in i for i in issues)
        # o vínculo sozinho ainda resolve os relacionamentos
        assert mapped['vehicle_plate'] == 'GRA4982'

    def test_maps_install_date_and_fee(self):
        mapped, _issues = map_tracker(self.VINCULO, self.RASTREADOR)
        assert mapped['install_date'] == '2024-07-31'
        assert mapped['installation_fee'] == 150.0

    def test_maps_status_and_sim_number(self):
        mapped, _issues = map_tracker(self.VINCULO, self.RASTREADOR)
        assert mapped['status'] == TrackerStatus.INSTALLED.value
        assert mapped['sim_number'] == '55999990864'


class TestCiGet:
    def test_returns_none_for_non_dict(self):
        assert ci_get('not-a-dict', 'foo') is None
        assert ci_get(['x'], 'foo') is None
        assert ci_get(None, 'foo') is None

    def test_case_insensitive(self):
        assert ci_get({'Foo': 'bar'}, 'foo') == 'bar'

    def test_first_matching_candidate_wins(self):
        assert ci_get({'b': '2'}, 'a', 'b') == '2'


class TestMapBoletoEmAberto:
    """A situação do boleto não diz se ele ainda é devido — quem diz é
    /buscar_boletos_abertos_cliente. Tratar 'APROVADO' de 2019 como dívida
    inflaria a inadimplência e poderia gerar cobrança indevida."""

    BOLETO = {
        'cod_boleto': '11751', 'nosso_numero': '11751', 'valor': '44,99',
        'mes_referente': '07/2019', 'data_vencimento': '15/08/2019',
        'situacao': {'descricao': 'APROVADO'},
    }

    def test_aprovado_fora_da_lista_de_abertos_nao_vira_divida(self):
        from app.models.enums import BillingStatus
        from app.services.sgr_migration.mapping import map_boleto

        mapped, _ = map_boleto(self.BOLETO, em_aberto=False)
        assert mapped['status'] == BillingStatus.CANCELED.value

    def test_boleto_listado_como_aberto_vira_pendente(self):
        from app.models.enums import BillingStatus
        from app.services.sgr_migration.mapping import map_boleto

        mapped, _ = map_boleto(self.BOLETO, em_aberto=True)
        assert mapped['status'] == BillingStatus.PENDING.value

    def test_baixado_continua_pago_mesmo_fora_da_lista(self):
        from app.models.enums import BillingStatus
        from app.services.sgr_migration.mapping import map_boleto

        mapped, _ = map_boleto(
            {**self.BOLETO, 'situacao': {'descricao': 'BAIXADO'}}, em_aberto=False,
        )
        assert mapped['status'] == BillingStatus.PAID.value


class TestMapBillingType:
    """Regressão: 'MENSALIDADE EM ABERTO 15/01/2025 EM 5X' (dívida antiga
    renegociada em parcelas) caiu como 'recorrente' por conter a substring
    'MENSALIDADE' -- e colidiu com a mensalidade normal da MESMA placa no
    MESMO mês (caso real: ABR EXPRESS, boleto 1738, R$ 90,98 + R$ 64,99),
    duplicando a cobrança do contrato."""

    def test_mensalidade_normal_e_recorrente(self):
        assert map_billing_type('MENSALIDADE') == 'recorrente'

    def test_mensalidade_em_aberto_parcelada_e_avulsa(self):
        assert map_billing_type('MENSALIDADE EM ABERTO 15/01/2025 EM 5X') == 'avulsa'
        assert map_billing_type('MENSALIDADE EM ABERTO 15/02/2026 EM 5X') == 'avulsa'

    def test_prorata(self):
        assert map_billing_type('DESCONTO PRORATA') == 'prorata'

    def test_instalacao_e_desinstalacao(self):
        assert map_billing_type('INSTALAÇÃO PADRÃO') == 'taxa_instalacao'
        assert map_billing_type('DESINSTALAÇÃO EXTERNA') == 'taxa_desinstalacao'

    def test_desconto_simples_sem_prorata_e_avulsa(self):
        # 'DESCONTO' sozinho (sem 'PRORATA') não é a mesma coisa
        assert map_billing_type('DESCONTO') == 'avulsa'

    def test_produtos_sem_regra_especifica_sao_avulsa(self):
        assert map_billing_type('Tarifas bancária') == 'avulsa'
        assert map_billing_type('TROCA DE VEICULO') == 'avulsa'
        assert map_billing_type('AVULSO') == 'avulsa'

    def test_produto_vazio_e_recorrente(self):
        # Sem produto nenhum (linha única do boleto, sem discriminação) —
        # mantém o comportamento anterior à introdução deste campo.
        assert map_billing_type(None) == 'recorrente'
        assert map_billing_type('') == 'recorrente'


class TestParcelaSgr:
    """O SGR manda "0 de 1"/"2 de 1"/"0 de 0" em boleto de fechamento: não é
    parcelamento e não pode virar parcela fora do intervalo (a migration
    e5c2a9d71f04 recusa subir com isso)."""

    def test_parcela_valida(self):
        from app.services.sgr_migration.mapping import _parse_parcela
        assert _parse_parcela('3 de 10') == (3, 10)
        assert _parse_parcela('1 de 1') == (1, 1)

    def test_parcela_fora_do_intervalo_vira_sem_parcela(self):
        from app.services.sgr_migration.mapping import _parse_parcela
        for texto in ('0 de 1', '2 de 1', '0 de 0', '5 de 4', 'x de 2', ''):
            assert _parse_parcela(texto) == (None, None), texto
