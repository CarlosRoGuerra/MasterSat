"""
Cliente HTTP isolado e READ-ONLY para a API do SGR (Hinova).

Único ponto do código que fala com o SGR — nenhuma chamada HTTP ao SGR deve
existir fora deste módulo (ETAPA 4 da POC de migração).

Autenticação (conforme doc oficial, grupo "autenticacao" /headers_authorization):
  1. POST /headers_authorization com {"cliente": codMobile, "nome": usuario,
     "senha": senha} no corpo. A doc não declara explicitamente o content-type
     do corpo — assumimos JSON (padrão do restante da API, que só aceita
     "Accept: application/json"); ver nota em `authenticate()`.
  2. O "Exemplo Retorno" da doc mostra ``X-Auth-Token``/``Authorization``
     DENTRO do corpo JSON, num objeto ``"Headers": {...}`` — não como headers
     HTTP de verdade (confirmado batendo contra a API real: eles não vêm em
     ``resp.headers``). A doc grafa "Autorization" — aceitamos ambas as
     grafias. ``authenticate()`` checa o corpo primeiro e os headers HTTP como
     reserva. Esses valores devem ser reenviados em toda chamada seguinte.
     A doc também instrui "guarde os cookies retornados" — por isso usamos
     ``requests.Session()``, que persiste cookies automaticamente.
  3. Toda chamada além da autenticação usa a "chave API" (gerada ao cadastrar
     o Fornecedor de Serviços no SGR — distinta do codMobile) como segmento
     de PATH: ``/buscar_cliente/{chave_api}``.
  4. **NÃO DOCUMENTADO**: o codMobile precisa ser reenviado em TODA chamada,
     na query string como ``cliente=<codMobile>``. Sem isso a API responde
     401 "Código de cliente inválido" em todos os endpoints — inclusive nas
     tabelas de domínio —, o que NÃO é falta de permissão (essa devolve outra
     mensagem, explícita: "Você não tem permissão para a função X"). Só a
     query funciona: um header ``codMobile`` é ignorado (verificado em sessão
     isolada). A apidoc não cita esse parâmetro em endpoint nenhum; foi
     descoberto testando contra a API real.

Apenas métodos GET (consulta) são implementados — de propósito. Esta POC é
somente leitura; não existem wrappers para os endpoints de inserir/editar do
SGR.

JANELA DE ACESSO (descoberto na prática, não está em doc nenhuma): a conta de
integração só autentica em dia útil e em horário comercial. Fora disso o SGR
responde 401 — o mesmo código de credencial inválida —, e a causa real só
aparece no campo `msg` do corpo:
  - sábado/domingo: "Restrição de data: Você não tem permissão para acessar o
    sistema hoje."
  - de madrugada:   "Restrição de horário: Você não tem permissão para acessar
    o sistema no momento."
É por isso que _raise_for_status() repassa o `msg`: sem ele, os dois casos
viram "credencial recusada" e se perde muito tempo procurando erro onde não
tem. Importação em lote precisa ser agendada dentro dessa janela.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

_PATH_AUTH = '/headers_authorization'
# A varredura de boletos com linha digitável é a consulta mais pesada da API
# deles — 30s não bastam nos meses cheios de boleto aberto.
_TIMEOUT_BOLETOS = 120


# ---------------------------------------------------------------------------
# Exceções — cada uma corresponde a uma causa raiz distinta (ETAPA 15)
# ---------------------------------------------------------------------------

class SGRError(Exception):
    """Erro de configuração — nenhuma chamada de rede foi feita."""


class SGRApiError(Exception):
    """Erro retornado pela API do SGR (ou falha ao interpretar a resposta)."""

    def __init__(self, message: str, status_code: int | None = None):
        self.status_code = status_code
        super().__init__(message)


class SGRAuthenticationError(SGRApiError):
    """401/403 — credenciais ou chave de API inválidas/expiradas."""


class SGRNotFoundError(SGRApiError):
    """404 — recurso inexistente."""


class SGRRateLimitError(SGRApiError):
    """429 — limite de requisições atingido."""


class SGRServerError(SGRApiError):
    """5xx — erro no servidor do SGR (não é responsabilidade do MasterSat)."""


class SGRConnectionError(SGRApiError):
    """Timeout ou falha de conexão — nenhuma resposta HTTP foi recebida."""


class SGRInvalidResponseError(SGRApiError):
    """A resposta não é JSON, não tem o formato esperado, ou Error=true."""


class SGRPaginacaoError(SGRApiError):
    """A paginação não conseguiu provar que leu tudo: página repetida quando
    a anterior veio cheia, registros deslocados entre páginas ou teto de
    páginas atingido. Resultado parcial NÃO pode ser tratado como completo."""


# ---------------------------------------------------------------------------
# Paginação exaustiva (SGR-05)
#
# Antes, cada caminho decidia sozinho: o período paginava, clientes paginava,
# mas veículos, vínculos e boletos por cliente liam UMA página de 200 e
# paravam — um cliente com 201 veículos perdia o último sem aviso. Aqui fica a
# regra única:
#   * `indice` é deslocamento (é como todos os endpoints do SGR foram usados);
#     avança pelo número de registros RECEBIDOS, não pelo pedido — a API pode
#     limitar o lote abaixo do `total` solicitado;
#   * página curta não prova o fim: pede-se a seguinte, que tem de vir vazia
#     (ou 404). Custa 1 requisição por coleção e é o que cobre o limite real
#     menor que o pedido;
#   * página idêntica à anterior: se a anterior veio curta, o endpoint ignora
#     `indice` e já devolveu tudo (fica anotado); se veio cheia, é erro;
#   * registro repetido entre páginas diferentes = origem mudou durante a
#     leitura (inserção desloca o índice) → coleta marcada incompleta.
# ---------------------------------------------------------------------------

_MAX_PAGINAS = 2000


@dataclass
class ColetaPaginada:
    """Manifesto de completude de uma coleção paginada. Sem dado pessoal:
    `escopo` usa só códigos de origem (cod_cliente, cod_veiculo, mês)."""

    endpoint: str
    escopo: str
    paginas: int = 0
    registros: int = 0
    repetidos: int = 0
    completa: bool = False
    observacao: str | None = None
    erro: str | None = None

    def as_dict(self) -> dict:
        return {
            'endpoint': self.endpoint, 'escopo': self.escopo, 'paginas': self.paginas,
            'registros': self.registros, 'repetidos': self.repetidos, 'completa': self.completa,
            'observacao': self.observacao, 'erro': self.erro,
        }


def _impressao(registro) -> str:
    return hashlib.sha1(
        json.dumps(registro, sort_keys=True, default=str, ensure_ascii=False).encode('utf-8'),
    ).hexdigest()


def iterar_paginas(buscar, tamanho: int, coleta: ColetaPaginada, max_paginas: int = _MAX_PAGINAS):
    """Gera os lotes de `buscar(total, indice)` até provar o fim da coleção.

    Quem interrompe a iteração antes do fim (ex.: limite de clientes) deixa a
    coleta com `completa=False` — é leitura parcial por escolha, e o
    manifesto mostra isso.
    """
    indice = 0
    vistos: set[str] = set()
    anterior: list[str] | None = None
    anterior_curta = False
    while True:
        if coleta.paginas >= max_paginas:
            coleta.erro = f'teto de {max_paginas} páginas atingido sem chegar ao fim'
            raise SGRPaginacaoError(f'{coleta.endpoint} ({coleta.escopo}): {coleta.erro}')
        try:
            lote = buscar(tamanho, indice)
        except SGRNotFoundError:
            if coleta.paginas and anterior_curta:
                coleta.completa = True
                coleta.observacao = 'fim sinalizado por 404 depois de página curta'
                return
            coleta.erro = 'SGRNotFoundError'
            raise
        except SGRApiError as exc:
            coleta.erro = type(exc).__name__
            raise
        coleta.paginas += 1
        if not lote:
            coleta.completa = True
            return
        impressoes = [_impressao(r) for r in lote]
        if impressoes == anterior:
            if anterior_curta:
                coleta.completa = True
                coleta.observacao = 'endpoint ignora o índice; a página única já trazia tudo'
                return
            coleta.erro = 'página repetida depois de página cheia'
            raise SGRPaginacaoError(f'{coleta.endpoint} ({coleta.escopo}): {coleta.erro}')
        repetidos = sum(1 for imp in impressoes if imp in vistos)
        if repetidos:
            coleta.repetidos += repetidos
            coleta.erro = 'registros repetidos entre páginas (a origem mudou durante a leitura?)'
            raise SGRPaginacaoError(f'{coleta.endpoint} ({coleta.escopo}): {coleta.erro}')
        vistos.update(impressoes)
        anterior = impressoes
        anterior_curta = len(lote) < tamanho
        coleta.registros += len(lote)
        indice += len(lote)
        yield lote


def paginar(buscar, tamanho: int, coleta: ColetaPaginada, max_paginas: int = _MAX_PAGINAS) -> list[dict]:
    registros: list[dict] = []
    for lote in iterar_paginas(buscar, tamanho, coleta, max_paginas):
        registros.extend(lote)
    return registros


def _ci_get(payload: dict, *keys: str):
    """Busca uma chave em um dict de forma case-insensitive (a doc do SGR mistura
    PascalCase e snake_case entre endpoints e até dentro do mesmo endpoint)."""
    lowered = {str(k).lower(): v for k, v in payload.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value is not None:
            return value
    return None


@dataclass
class RequestLogEntry:
    method: str
    path: str
    status_code: int | None


@dataclass
class SGRClient:
    """Cliente HTTP para a API do SGR. Uma instância = uma sessão autenticada."""

    session: requests.Session = field(default_factory=requests.Session)
    request_count: int = field(default=0, init=False)
    request_log: list[RequestLogEntry] = field(default_factory=list, init=False)
    _auth_headers: dict[str, str] = field(default_factory=dict, init=False)

    # -- configuração ------------------------------------------------------

    @staticmethod
    def _require_config() -> None:
        missing = [
            name for name, value in (
                ('SGR_BASE_URL', settings.sgr_base_url),
                ('SGR_COD_MOBILE', settings.sgr_cod_mobile),
                ('SGR_USERNAME', settings.sgr_username),
                ('SGR_PASSWORD', settings.sgr_password),
                ('SGR_API_KEY', settings.sgr_api_key),
            ) if not value
        ]
        if missing:
            raise SGRError(
                'Credenciais do SGR ausentes: ' + ', '.join(missing)
                + '. Configure-as via variáveis de ambiente (.env) — nunca no código.'
            )

    @property
    def is_authenticated(self) -> bool:
        return bool(self._auth_headers)

    # -- infraestrutura HTTP -------------------------------------------------

    def _track(self, method: str, path: str, status_code: int | None) -> None:
        self.request_count += 1
        # Nunca registra query params/corpo aqui — podem conter CPF/placa/etc.
        self.request_log.append(RequestLogEntry(method=method, path=path, status_code=status_code))

    def _send(
        self, method: str, url: str, *, context: str, timeout: int | None = None, **kwargs,
    ) -> requests.Response:
        try:
            resp = self.session.request(
                method, url, timeout=timeout or settings.sgr_timeout_seconds, **kwargs,
            )
        except requests.Timeout as exc:
            self._track(method, context, None)
            raise SGRConnectionError(f'Timeout ao consultar o SGR ({context})') from exc
        except requests.RequestException as exc:
            self._track(method, context, None)
            raise SGRConnectionError(f'Falha de conexão com o SGR ({context})') from exc
        self._track(method, context, resp.status_code)
        return resp

    @staticmethod
    def _extract_msg(resp: requests.Response) -> str | None:
        """Best-effort: o SGR devolve {"error":true,"msg":"..."} mesmo em 401/403
        (ex.: "Restrição de data: você não tem permissão para acessar o sistema
        hoje" — não tem nada a ver com credencial errada). Sem isso a causa real
        fica escondida atrás de uma mensagem genérica de "credencial recusada"."""
        try:
            body = resp.json()
        except ValueError:
            return None
        if not isinstance(body, dict):
            return None
        msg = _ci_get(body, 'msg', 'message', 'mensagem')
        return str(msg) if msg else None

    @classmethod
    def _raise_for_status(cls, resp: requests.Response, context: str) -> None:
        msg = cls._extract_msg(resp)
        detail = f' — {msg}' if msg else ''
        if resp.status_code in (401, 403):
            raise SGRAuthenticationError(
                f'SGR recusou a credencial/chave de API ({context}): HTTP {resp.status_code}{detail}',
                status_code=resp.status_code,
            )
        if resp.status_code == 404:
            raise SGRNotFoundError(f'Recurso não encontrado no SGR ({context}){detail}', status_code=404)
        if resp.status_code == 429:
            raise SGRRateLimitError(f'Limite de requisições do SGR atingido ({context}){detail}', status_code=429)
        if resp.status_code >= 500:
            raise SGRServerError(
                f'Erro no servidor do SGR ({context}): HTTP {resp.status_code}{detail}', status_code=resp.status_code,
            )
        if resp.status_code >= 400:
            raise SGRApiError(
                f'Erro ao consultar o SGR ({context}): HTTP {resp.status_code}{detail}', status_code=resp.status_code,
            )

    @staticmethod
    def _parse_json(resp: requests.Response, context: str) -> dict:
        try:
            body = resp.json()
        except ValueError as exc:
            # Sem o trecho do corpo, "não é JSON válido" esconde a causa real.
            # Já vimos HTTP 200 com corpo HTML de erro interno deles (ex.:
            # "Can't connect to local MySQL server" — banco deles fora do ar,
            # nem a autenticação funciona). Prévia curta: é página de erro do
            # servidor deles, não dado de cliente — mas trunca por segurança.
            preview = (resp.text or '').strip().replace('\n', ' ')[:200]
            detalhe = f' — corpo da resposta: {preview}' if preview else ''
            raise SGRInvalidResponseError(
                f'Resposta do SGR não é JSON válido ({context}){detalhe}'
            ) from exc
        if not isinstance(body, dict):
            raise SGRInvalidResponseError(f'Formato de resposta inesperado do SGR ({context})')
        error_flag = _ci_get(body, 'error')
        if isinstance(error_flag, str) and error_flag.strip().lower() == 'true':
            raise SGRInvalidResponseError(f'SGR retornou Error=true ({context})')
        return body

    # -- autenticação --------------------------------------------------------

    def authenticate(self) -> None:
        """POST /headers_authorization — obtém X-Auth-Token/Authorization."""
        self._require_config()
        url = f'{settings.sgr_base_url.rstrip("/")}{_PATH_AUTH}'
        resp = self._send(
            'POST', url, context='autenticação',
            headers={'Accept': 'application/json'},
            json={
                'cliente': settings.sgr_cod_mobile,
                'nome': settings.sgr_username,
                'senha': settings.sgr_password,
            },
        )
        self._raise_for_status(resp, context='autenticação')
        body = self._parse_json(resp, context='autenticação')

        # O "Exemplo Retorno" da doc mostra X-Auth-Token/Autorization DENTRO do
        # corpo JSON, num objeto "Headers" — não como headers HTTP de verdade.
        # Checamos os dois lugares (corpo primeiro, headers HTTP como reserva)
        # porque a doc está longe de confiável (ver mapping.py) e o comportamento
        # real só se confirma rodando contra a API viva.
        headers_obj = _ci_get(body, 'headers')
        token = _ci_get(headers_obj, 'x-auth-token') if isinstance(headers_obj, dict) else None
        authorization = _ci_get(headers_obj, 'authorization', 'autorization') if isinstance(headers_obj, dict) else None
        token = token or resp.headers.get('X-Auth-Token')
        authorization = authorization or resp.headers.get('Authorization') or resp.headers.get('Autorization')
        if not token or not authorization:
            raise SGRInvalidResponseError(
                'Resposta de autenticação do SGR sem X-Auth-Token/Authorization (nem no corpo, nem nos headers HTTP)'
            )
        self._auth_headers = {'X-Auth-Token': token, 'Authorization': authorization}
        logger.info('SGR: autenticação OK')

    def _ensure_authenticated(self) -> None:
        if not self.is_authenticated:
            self.authenticate()

    # -- consultas genéricas ---------------------------------------------------

    def get(
        self, path: str, params: dict | None = None, *, retry_auth: bool = True,
        timeout: int | None = None,
    ) -> dict:
        """GET {base}{path}/{chave_api} com os headers de autenticação."""
        self._ensure_authenticated()
        self._require_config()
        url = f'{settings.sgr_base_url.rstrip("/")}{path}/{settings.sgr_api_key}'
        # 'cliente' (codMobile) em TODA chamada — ver item 4 do docstring do módulo.
        clean_params = {'cliente': settings.sgr_cod_mobile}
        clean_params.update({k: v for k, v in (params or {}).items() if v not in (None, '')})
        headers = {'Accept': 'application/json', **self._auth_headers}

        resp = self._send('GET', url, context=path, headers=headers, params=clean_params, timeout=timeout)

        if resp.status_code in (401, 403) and retry_auth:
            # Token pode ter expirado no meio da execução — reautentica 1x.
            self._auth_headers = {}
            self.authenticate()
            return self.get(path, params, retry_auth=False, timeout=timeout)

        self._raise_for_status(resp, context=path)
        return self._parse_json(resp, context=path)

    @staticmethod
    def extract_data(body: dict) -> list[dict]:
        """Normaliza o envelope {Error, Data} / {error, data} em uma lista de registros."""
        data = _ci_get(body, 'data')
        if data is None:
            return []
        if isinstance(data, dict):
            return [data]
        if isinstance(data, list):
            return data
        raise SGRInvalidResponseError('Campo Data da resposta do SGR em formato inesperado')

    # -- endpoints usados pela POC (só leitura) ---------------------------------

    def buscar_clientes(self, total: int, indice: int = 0) -> list[dict]:
        body = self.get('/buscar_cliente', {'total': total, 'indice': indice})
        return self.extract_data(body)

    def buscar_veiculos_por_cliente(self, cod_cliente, total: int = 200, indice: int = 0) -> list[dict]:
        body = self.get('/buscar_veiculo', {'cod_cliente': cod_cliente, 'total': total, 'indice': indice})
        return self.extract_data(body)

    def buscar_vinculos_por_placa(self, placa: str, total: int = 200, indice: int = 0) -> list[dict]:
        body = self.get('/buscar_vinculo', {'placa_vinculo': placa, 'total': total, 'indice': indice})
        return self.extract_data(body)

    def buscar_equipamento(self, cod_equipamento) -> list[dict]:
        body = self.get('/buscar_equipamento', {'cod_equipamento': cod_equipamento})
        return self.extract_data(body)

    def buscar_rastreadores(self, total: int = 200, indice: int = 0) -> list[dict]:
        """Rastreadores em lote. Este endpoint é o único que traz IMEI do
        equipamento, situação e PLACA no mesmo registro — por isso a POC
        indexa o resultado por placa em vez de fazer 1 chamada por veículo."""
        body = self.get('/buscar_rastreador', {'total': total, 'indice': indice})
        return self.extract_data(body)

    def buscar_vendas(
        self, cod_venda=None, ultima_atualizacao=None, total: int = 200, indice: int = 0,
    ) -> list[dict]:
        """GET /buscar_venda — é onde o SGR guarda o que no MasterSat vira
        Plano/Contrato: valor_parcela, quantidade_parcela, forma_pagamento,
        vencimento e periodo, vinculados a placa_veiculo/cpf (conforme a doc
        viva em https://api.hinova.com.br/api/sgr/servico/doc/, grupo
        vendaBuscar).

        CONFIRMADO contra a API real em 21/09: `cod_venda` é mesmo
        obrigatório — sem ele (só com `ultima_atualizacao`) o servidor
        devolve 500, não uma lista. E, ao contrário do resto do módulo, aqui
        não basta chave natural: nos ~30 veículos de 9 clientes reais
        testados, só 1 tinha `contrato_veiculo` preenchido (o candidato mais
        óbvio a cod_venda) — e mesmo esse não bateu com nenhuma venda
        existente (0 registros). Ou seja: pra maioria dos veículos já
        migrados HOJE NÃO EXISTE, nos dados que conseguimos ler, um jeito de
        descobrir o cod_venda correspondente. Perguntar pra Hinova qual é o
        campo/endpoint certo pra achar a venda de um veículo antes de tentar
        usar isto em massa.
        """
        body = self.get('/buscar_venda', {
            'cod_venda': cod_venda,
            'ultima_atualizacao': ultima_atualizacao,
            'total': total,
            'indice': indice,
        })
        return self.extract_data(body)

    # ONDE MORA O "CONTRATO" DO SGR (confirmado em 21/09 contra a API real)
    #
    # Não é no cliente nem no veículo: `numero_contrato_cliente` e
    # `contrato_veiculo` vêm vazios em praticamente toda a base, e o módulo de
    # vendas nunca foi usado (/buscar_venda devolve lista vazia). O contrato
    # mora no VÍNCULO (/buscar_vinculo), que esta POC já busca para montar o
    # rastreador. Além do equipamento, o vínculo traz todo o lado financeiro
    # que vira Contract no MasterSat:
    #   cod_grupo_vinculo ......... o plano -> valor em /get_grupo_mensalidade
    #   cod_vencimento_vinculo .... dia de vencimento -> /get_vencimento
    #   gerar_cobranca ('S'/'N') .. se o vínculo gera cobrança
    #   cod_interveniente_vinculo + interveniente{nome,cpf} .. responsável financeiro
    #   cod_banco_vinculo / formato_geracao_boleto ........... banco e formato
    #   mes_inicio_cobranca / mes_final_carne / mes_referente  ciclo de cobrança
    #   acrescimo / desconto (+ mes_*) ....................... ajustes de valor
    #   produtos[] ...... cobranças avulsas/negociações, NÃO a mensalidade
    #   valor_instalacao / data_instalacao
    # A cobrança é consolidada por cliente (formato_boleto_cliente = 'U' em
    # todos os clientes lidos), mas configurada por vínculo — um boleto cobre
    # vários veículos, e `discriminacao[]` do boleto detalha o valor por placa.

    def get_vencimento(self, cod_cliente=None) -> list[dict]:
        """GET /get_vencimento — dia de vencimento. Confirmado: 15 registros,
        {'descricao': '10', 'cod_vencimento': '126'}. Traduz o
        `cod_vencimento_vinculo` do vínculo para o dia do mês."""
        body = self.get('/get_vencimento', {'cod_cliente': cod_cliente})
        return self.extract_data(body)

    def get_grupo_mensalidade(self, total: int = 200, indice: int = 0) -> list[dict]:
        """GET /get_grupo_mensalidade — OS PLANOS, com valor. Confirmado:
        {'cod_grupo_mensalidade': '17', 'descricao': 'MENSALIDADE 64,99',
        'valor': '64,99'}. É o que traduz o `cod_grupo_vinculo` de cada
        vínculo no valor mensal — a peça central para montar Plan/Contract.

        Note que /get_plano (nome mais óbvio) existe mas está SEM permissão
        para o nosso usuário; este aqui está liberado e resolve o problema."""
        body = self.get('/get_grupo_mensalidade', {'total': total, 'indice': indice})
        return self.extract_data(body)

    def get_grupo_adesao(self, total: int = 200, indice: int = 0) -> list[dict]:
        """GET /get_grupo_adesao — taxas de adesão, mesmo formato do grupo de
        mensalidade (ex.: {'cod_grupo_adesao': '20', 'descricao':
        'ANUAL 749,99', 'valor': '749,99'})."""
        body = self.get('/get_grupo_adesao', {'total': total, 'indice': indice})
        return self.extract_data(body)

    def get_condicao_pagamento(self, total: int = 200, indice: int = 0) -> list[dict]:
        """GET /get_condicao_pagamento — formas de pagamento (confirmado:
        cheque, BOLETO, cartao, Dinheiro). Substitui /get_forma_pagamento,
        que existe mas está sem permissão para o nosso usuário."""
        body = self.get('/get_condicao_pagamento', {'total': total, 'indice': indice})
        return self.extract_data(body)

    def buscar_boletos_cliente(
        self, cpf_cnpj: str, total: int = 200, indice: int = 0, **filtros,
    ) -> list[dict]:
        """GET /buscar_boletos_cliente — HISTÓRICO DE COBRANÇA do cliente.

        ATENÇÃO: `cpf_cnpj` tem que ir SÓ COM DÍGITOS. O SGR devolve o CPF
        mascarado ('063.234.889-57') em /buscar_cliente, e mandar de volta
        com máscara faz este endpoint responder 0 registros — sem erro
        nenhum, o que dá a falsa impressão de que o cliente não tem boleto.
        Com os dígitos limpos, o mesmo cliente devolveu 57 boletos.

        Cada boleto traz cod_boleto, nosso_numero, parcela ('1 de 10'),
        mes_referente, valor, valor_pagamento, forma_pagamento, as datas
        (emissão, vencimento, vencimento_original, pagamento, crédito no
        banco), numero_nf, tipo_boleto, dados bancários, situacao.descricao
        (BAIXADO / ABERTO / CANCELADO / NEGADO / NEGOCIADO / APROVADO /
        REMOVIDO / BAIXADO COM PENDÊNCIA), placas[] e — o mais útil para
        conferência — `discriminacao[]`, com valor, mês e PLACA de cada item
        dentro do boleto consolidado.

        `filtros` aceita os recortes documentados: data_emissao_inicio/_fim,
        data_vencimento_inicio/_fim, data_pagamento_inicio/_fim,
        data_credito_banco_inicio/_fim, cod_boleto, nosso_numero, numero_nf
        (datas em dd/mm/aaaa). O endpoint irmão /buscar_boletos (todos os
        boletos, sem cpf) responde HTTP 500 no servidor deles — por isso a
        varredura tem que ser cliente a cliente.
        """
        digitos = ''.join(ch for ch in str(cpf_cnpj or '') if ch.isdigit())
        body = self.get('/buscar_boletos_cliente', {
            'cpf_cnpj': digitos, 'total': total, 'indice': indice, **filtros,
        })
        return self.extract_data(body)

    _PAGINA_BOLETOS = 50  # teto do endpoint: pedir mais não traz mais

    def buscar_boletos_periodo(
        self, data_inicio: str, data_fim: str, campo: str = 'vencimento', linha_digitavel: bool = False,
        coleta: ColetaPaginada | None = None,
    ) -> list[dict]:
        """GET /buscar_boletos — boletos de TODOS os clientes num período.

        Duas regras não óbvias, que estão só nas notas em <small> da doc e
        fazem o endpoint responder 500 quando desrespeitadas:
          - as datas vão em ISO (aaaa-mm-dd); no formato brasileiro dá 500;
          - o intervalo é de no máximo 1 MÊS, e o par início/fim é
            obrigatório (por emissão, vencimento, pagamento ou crédito).

        Vale mais que /buscar_boletos_cliente para migração: além de cobrir a
        base inteira sem 1 chamada por cliente, traz `cod_cliente` no próprio
        boleto e, na discriminação, `produto` e `situacao_veiculo` — que a
        versão por cliente não devolve. `linha_digitavel=True` acrescenta o
        `pix_copia_cola`.

        `campo` escolhe a data usada no filtro: 'vencimento', 'emissao',
        'pagamento' ou 'credito_banco'.
        """
        prefixo = {
            'vencimento': 'data_vencimento',
            'emissao': 'data_emissao',
            'pagamento': 'data_pagamento',
            'credito_banco': 'data_credito_banco',
        }[campo]

        def _pagina(total: int, indice: int) -> list[dict]:
            params = {
                f'{prefixo}_inicio': data_inicio,
                f'{prefixo}_fim': data_fim,
                'total': total,
                'indice': indice,
            }
            if linha_digitavel:
                params['linha_digitavel'] = 'S'
            # Gerar linha digitável e PIX é caro do lado deles: os meses com
            # muitos boletos em aberto (os futuros) estouravam o timeout
            # padrão de 30s de forma consistente.
            return self.extract_data(self.get('/buscar_boletos', params, timeout=_TIMEOUT_BOLETOS))

        coleta = coleta or ColetaPaginada('/buscar_boletos', f'{campo}:{data_inicio[:7]}')
        return paginar(_pagina, self._PAGINA_BOLETOS, coleta)

    def buscar_xml_nota_fiscal(self, cod_boleto) -> str | None:
        """GET /buscar_xml_nota_fiscal — devolve a URL do XML da NFS-e do
        boleto (o arquivo fica num gateway de terceiros, não no SGR).

        É o único caminho que funciona para nota fiscal: o endpoint de
        listagem, /buscar_notas_fiscais, responde 500 ("Undefined index:
        numero" em ApiNFController.php) ou estoura o tempo, em todas as
        combinações de parâmetro testadas. Como já temos o cod_boleto de
        cada boleto, a listagem não faz falta.
        """
        registros = self.extract_data(self.get('/buscar_xml_nota_fiscal', {'cod_boleto': cod_boleto}))
        for registro in registros:
            url = _ci_get(registro, 'xml') if isinstance(registro, dict) else None
            if url:
                return str(url)
        return None

    def buscar_boletos_abertos_cliente(
        self, cpf_cnpj: str, total: int = 200, indice: int = 0, **filtros,
    ) -> list[dict]:
        """GET /buscar_boletos_abertos_cliente — só os boletos em aberto do
        cliente. Mesma regra do CPF sem máscara de buscar_boletos_cliente."""
        digitos = ''.join(ch for ch in str(cpf_cnpj or '') if ch.isdigit())
        body = self.get('/buscar_boletos_abertos_cliente', {
            'cpf_cnpj': digitos, 'total': total, 'indice': indice, **filtros,
        })
        return self.extract_data(body)
