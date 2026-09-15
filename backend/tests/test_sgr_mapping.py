"""
Testes de mapeamento SGR → MasterSat (ETAPA 9) e de detecção de problemas de
qualidade de dado (ETAPA 11). O payload de cliente usado é o "Exemplo
Retorno" literal da doc oficial (/doc/api_data.js, grupo clienteBuscar) —
não foi inventado.
"""
from __future__ import annotations

from app.models.enums import ClientStatus, TrackerStatus, VehicleStatus
from app.services.sgr_migration.mapping import ci_get, map_cliente, map_tracker, map_veiculo

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


class TestMapTracker:
    def test_maps_from_equipamento_shaped_payload(self):
        raw = {
            'Cod_equipamento': '234',
            'Imei_equipamento': '355488020902005',
            'Modelo': {'Descricao': 'ST310', 'Marca': {'Descricao': 'SUNTECH'}},
            'Situacao': {'Descricao': 'ATIVO'},
        }
        mapped, issues = map_tracker(raw)
        assert mapped['imei'] == '355488020902005'
        assert mapped['brand'] == 'SUNTECH'
        assert mapped['model'] == 'ST310'
        assert mapped['status'] == TrackerStatus.INSTALLED.value
        assert not any('IMEI' in i for i in issues)

    def test_missing_imei_is_flagged(self):
        mapped, issues = map_tracker({'Situacao': {'Descricao': 'ATIVO'}})
        assert mapped['imei'] is None
        assert any('IMEI' in i for i in issues)

    def test_case_insensitive_key_lookup(self):
        # A doc mistura PascalCase (rastreador/vinculo) e snake_case (cliente).
        raw = {'imei_equipamento': '123456789'}
        mapped, _issues = map_tracker(raw)
        assert mapped['imei'] == '123456789'


class TestCiGet:
    def test_returns_none_for_non_dict(self):
        assert ci_get('not-a-dict', 'foo') is None
        assert ci_get(['x'], 'foo') is None
        assert ci_get(None, 'foo') is None

    def test_case_insensitive(self):
        assert ci_get({'Foo': 'bar'}, 'foo') == 'bar'

    def test_first_matching_candidate_wins(self):
        assert ci_get({'b': '2'}, 'a', 'b') == '2'
