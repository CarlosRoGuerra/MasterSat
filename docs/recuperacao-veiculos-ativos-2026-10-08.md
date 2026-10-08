# Recuperação de TRATOR e BEY4J77 e exclusão por seleção

Seleção solicitada em 08/10/2026: **TRATOR e BEY4J77**. A nova consulta ao SGR
confirmou os dois veículos e seus responsáveis como ATIVO. BEY4J77 estava
SUSPENSO na consulta inicial; seu cadastro já está ATIVO na consulta refeita.

| Identificador | Cliente SGR | Situação do cliente na origem | Cadastro no MasterSat |
|---|---:|---|---|
| TRATOR | 399 | ATIVO | Equipamento sem placa oficial |
| BEY4J77 | 437 | ATIVO | Veículo ativo com placa oficial |

A recuperação consulta novamente o SGR na execução. Um veículo que deixou de
estar ATIVO bloqueia o lote. Os comandos abaixo selecionam somente TRATOR e
BEY4J77; os outros 28 identificadores do relatório ficam fora desta operação.

O script cria somente clientes ausentes e os dois cadastros de veículos.
Preserva os clientes locais existentes, seus dados e situação. Não cria
contratos, vincula rastreadores nem gera mensalidades ou cobranças antigas.
Esses cadastros, isoladamente, não incluem novas mensalidades no fechamento;
o vínculo e o plano devem ser definidos pelo fluxo normal do sistema.

Os códigos do SGR ficam em `sgr_vinculos`, com auditoria por veículo criado.
Repetir o comando reaproveita os registros. Identidade divergente, registro
excluído, dono diferente ou origem incompleta bloqueiam todo o lote e desfazem
as alterações. Documento inválido impede criar um novo responsável; para um
cliente local existente, o documento é usado apenas para identificar o cadastro,
sem alterá-lo. O relatório informa `documento_origem_valido`.

## Comandos na VPS

Execute na pasta `~/MasterSat`, usando o `.env` de produção já configurado.
As credenciais `SGR_*` precisam estar nesse ambiente; não cole senhas no terminal
nem substitua o arquivo `.env` pelo de desenvolvimento.

### 1. Atualizar, guardar backup e implantar

```bash
(
  set -e
  cd ~/MasterSat
  git pull --ff-only origin fix/rastreador-dia-vencimento
  dc() { docker compose -f docker-compose.yml -f docker-compose.prod.yml "$@"; }
  mkdir -p backups-manuais
  BACKUP="backups-manuais/antes_veiculos_ativos_$(date +%Y%m%d_%H%M%S).dump"
  dc exec -T db sh -c 'pg_dump -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-rastreamento}" -Fc' > "$BACKUP"
  test -s "$BACKUP"
  echo "Backup salvo em $BACKUP"
  dc build backend backend-worker frontend
  dc run --rm --no-deps backend python -m alembic upgrade head
  dc up -d --no-deps backend backend-worker frontend
  dc exec -T nginx nginx -s reload
)
```

A migration `c8f2a6d41e90` amplia o identificador para 40 caracteres e adiciona
`is_non_road_asset`. Deve estar aplicada antes de executar a recuperação.
O backend e o worker precisam usar a versão nova. Uma tentativa de downgrade
com equipamentos sem placa existentes é recusada para preservar seus dados.

### 2. Conferir sem gravar

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T backend \
  python scripts/sgr_recuperar_veiculos_ativos.py \
  --operator administrator \
  --identificadores TRATOR BEY4J77
```

O resultado deve conter `"modo": "SIMULACAO_SEM_GRAVACAO"`, os dois
identificadores e `"situacao": "ativo"`. IDs mostrados na simulação são
provisórios; sequências PostgreSQL podem avançar mesmo com rollback.

### 3. Incluir em produção

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T backend \
  python scripts/sgr_recuperar_veiculos_ativos.py \
  --operator administrator \
  --identificadores TRATOR BEY4J77 \
  --apply
```

Sucesso retorna `"modo": "APLICADO"` depois do commit, com os dois IDs
persistidos. Erro retorna código de saída 1, sem aplicar parte do lote.
Depois, no menu **Veículos**, limpe filtros de cliente/status e busque cada
identificador. O menu exibe e permite editar máquina ou equipamento sem
placa oficial sem cortar seus identificadores.

### 4. Conferir os registros locais

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T backend python - <<'PY'
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models import registry_all
from app.models.vehicle import Vehicle
from app.models.client import Client
from app.models.enums import VehicleStatus
from app.schemas.vehicle import VehicleOut
ids = {'TRATOR', 'BEY4J77'}
with SessionLocal() as db:
    rows = db.scalars(select(Vehicle).where(Vehicle.plate.in_(ids), Vehicle.is_deleted.is_(False))).all()
    assert len(rows) == 2 and {v.plate for v in rows} == ids
    for v in rows:
        assert v.status == VehicleStatus.ACTIVE
        VehicleOut.model_validate(v)
        client = db.get(Client, v.client_id)
        assert client and not client.is_deleted
        print(v.id, v.plate, v.status.value, 'cliente', client.id, client.status.value)
print('OK: dois cadastros ativos, vínculos e identificadores válidos.')
PY
```

## Exclusão de veículos selecionados

Disponível para administrador, assim como a exclusão individual de veículos.
Marque linhas ou o cabeçalho para selecionar todos os veículos do filtro,
incluindo outras páginas. Ao trocar o filtro, a seleção é limpa.

Clique **Excluir selecionados**. A janela consulta o backend, informa quantos
podem ser excluídos e mostra os motivos dos bloqueios. Confirme na janela.
A aplicação confere os vínculos novamente e remove apenas os liberados.
Veículos com rastreador vinculado ou contrato ativo exigem desinstalação.
A exclusão é lógica: preserva cadastro histórico, contratos e cobranças.

API: `POST /api/v1/vehicles/lote/excluir`, corpo
`{"ids": [1, 2], "simular": true}` para conferir; `simular: false` para aplicar.
Máximo de 2.000 IDs por solicitação, deduplicados pelo backend. A resposta
inclui `aplicados`, `ignorados` e o resultado individual de cada veículo.

## Validação

A migração e a recuperação foram executadas em PostgreSQL 16 descartável,
sem portas publicadas, usando o backup `mastersat_20261008_163712.dump`.
A cópia passou de 686 para 688 veículos; os 347 clientes permaneceram. Contratos
continuaram em 647, cobranças em 38.198 e rastreadores em 625. A simulação
desfez alterações; repetir a inclusão não duplicou cadastros ou auditoria.
A seleção recuperou exclusivamente TRATOR e BEY4J77, reutilizando seus clientes
locais. Esses resultados são da cópia isolada, não da VPS.

Os testes cobrem recuperação atômica, conflitos, origem incompleta,
idempotência, clientes cancelados, cadastro/edição sem truncamento, permissões,
simulação e exclusão por seleção com vínculos conferidos novamente.

Resultado: 610 testes de backend passaram em Python 3.12 com as dependências
da imagem de produção. Seis casos financeiros que já falhavam antes desta
alteração foram excluídos dessa rodada (a lista está em
`docs/validacao-troca-cliente-pagador-2026-10-08.md`). No frontend, 21 testes
passaram; TypeScript passou e ESLint não apresentou erros, apenas nove avisos
preexistentes na página de veículos.
