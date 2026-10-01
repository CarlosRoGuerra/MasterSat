# Migração SGR — política de status, sincronização e conflitos

Vale a partir da Fase 04 (migration `a4c7e2f9b1d6`). Código:
`backend/app/services/sgr_migration/importer.py` (regras) e
`conflitos.py` (decisões). Guia operacional em [guia-de-corte.md](guia-de-corte.md).

## 1. O que é "identidade" e o que é "dado"

| Entidade | Identidade (sgr_vinculos / sgr_documentos) | Chave natural só para ADOTAR registro existente | Atributos sincronizados depois da 1ª importação |
|---|---|---|---|
| Cliente | `cod_cliente` | CPF/CNPJ — e só se ele não estiver ligado a outro `cod_cliente` | Nenhum. CPF diferente na origem vira conflito `documento_divergente` (informativo) |
| Veículo | `cod_veiculo` | Placa — e só se o veículo for **do mesmo cliente** | Nenhum. Placa diferente vira `placa_divergente` |
| Rastreador | `cod_rastreador` | IMEI — e só se não estiver em outro veículo/cliente | Nenhum. IMEI diferente vira `imei_divergente` |
| Contrato | `cod_vinculo` | (cliente, veículo) | Nenhum |
| Plano | código do grupo | nome | Nenhum |
| Documento (boleto) | `cod_boleto` | — (legado: título/competência/valor gravados pelo importador antigo) | Situação, pagamento e vencimento (seção 3) |
| Linha | `cod_boleto:placa:produto:mês:ocorrência` | — | Valor **não** (mudança vira conflito) |

Cadastro (cliente, veículo, rastreador, contrato) **não é sincronizado**: o
MasterSat prevalece depois da primeira importação, porque é onde as
correções manuais são feitas. A divergência é registrada como conflito
informativo para quem quiser conferir.

## 2. Status do boleto SGR → cobrança MasterSat

| Situação no SGR | Status no MasterSat | Grupo |
|---|---|---|
| BAIXADO, BAIXADO COM PENDÊNCIA, PAGO | Paga | pago |
| ABERTO (confirmado em `/buscar_boletos_abertos_cliente` no caminho por cliente) | Pendente; Vencida se o vencimento já passou | aberto |
| APROVADO (só registrado), CANCELADO, REMOVIDO, NEGADO, NEGOCIADO | Cancelada | cancelado |

Pendente e Vencida são o mesmo grupo "aberto" para efeito de comparação: o
worker reclassifica por data, e isso não é edição humana.

## 3. Rodadas seguintes (sincronização versionada)

Cada documento guarda o estado da origem aplicado por último
(`snapshot_origem`/`hash_origem`) e cada linha guarda a impressão digital da
cobrança local (`hash_local`: grupo, valor, vencimento, pago, data de
pagamento, removida, competência liberada, substituída).

| Origem mudou? | MasterSat mudou? | Transição | Resultado |
|---|---|---|---|
| não | — | — | nada (inalterado) |
| sim | não | aberto → pago | **aplicado**: status, data, forma e valor pago (só em boleto de uma linha), com `billing_change_logs` |
| sim | não | aberto → cancelado | **aplicado** se a política bancária permitir (`titulo_bancario.exigir(CANCELAR)`) |
| sim | não | aberto → aberto com outro vencimento | **aplicado** se não houver título no banco (`exigir(ALTERAR_VALOR)`) |
| sim | não | pago → aberto/cancelado, cancelado → aberto/pago | conflito `transicao_nao_automatica` |
| sim | sim, para o mesmo estado | (ex.: baixa manual aqui e pagamento lá) | aceito, sem conflito — o registro local prevalece |
| sim | sim, para outro estado | qualquer | conflito `edicao_local` |
| valor/linhas mudaram | — | — | conflito `documento_alterado_origem`; nada é alterado |
| — | título Ailos ativo/desconhecido | qualquer | conflito `titulo_bancario_local` (a política bancária recusou) |

Reemissão: se o boleto antigo do mês foi cancelado **na origem** e um novo
chegou para o mesmo mês, a competência da cobrança antiga é liberada
(`release_billing_competencia`, com a política bancária) e a nova vira a
mensalidade. Cobrança local, paga, aberta ou com título ativo nunca é
liberada por esta regra.

Insert-only deixou de existir: o importador não "ignora o que já existe" —
compara, aplica o permitido e coloca o resto na fila.

## 4. Tipos de conflito e decisões

`python scripts/sgr_import.py --conflitos` lista; resolver exige
`--apply --operador --justificativa`.

| Tipo | O que significa | Decisões aceitas |
|---|---|---|
| `transferencia_veiculo` | A placa/código do veículo pertence a outro cliente no MasterSat | `aprovar_transferencia` (recusada se o contrato do dono atual estiver **ativo** — encerre-o antes; contratos e cobranças antigas ficam com o dono anterior), `manter_local` |
| `rastreador_em_outro_veiculo` | IMEI instalado em outro veículo/cliente | `manter_local` (mova o rastreador pela tela, se for o caso) |
| `documento_em_outro_cliente_sgr` | Dois códigos de cliente do SGR com o mesmo CPF/CNPJ — o segundo fica bloqueado | `manter_local` após sanear no SGR |
| `edicao_local`, `transicao_nao_automatica`, `divergencia_legado` | Cobrança mudou aqui e lá, ou transição fora da tabela | `aplicar_origem` (só as transições da seção 3; estorno/reabertura é pelo fluxo financeiro normal), `manter_local` |
| `titulo_bancario_local` | A política bancária recusou mudar a cobrança | resolver o título (baixa/consulta) e depois `aplicar_origem` ou `manter_local` |
| `documento_alterado_origem` | Valor ou linhas do boleto mudaram depois de importado | `manter_local` + ajuste pelo fluxo financeiro, se devido |
| `documento_divergente`, `placa_divergente`, `imei_divergente`, `contrato_divergente` | Cadastro difere da origem (informativo) | `manter_local` |
| `cliente_removido_localmente`, `veiculo_removido_localmente`, `cobranca_removida_localmente` | O registro de origem aponta para algo removido aqui | `manter_local` |

`manter_local` vale para aquele registro **naquele estado da origem**: a
próxima rodada não reabre o conflito; se a origem mudar de novo, a regra
volta a ser avaliada a partir do estado local decidido.

## 5. Documento consolidado, obrigação e desconto

- O boleto do SGR é consolidado por cliente; cada linha da discriminação
  vira (no máximo) uma cobrança. `sgr_documentos` guarda o documento;
  `sgr_documento_linhas` guarda cada linha com o valor de origem (com sinal).
- **Antes de liberar cobrança**, a soma das linhas (em centavos) tem de ser
  igual ao total do documento e todo desconto tem de caber nas obrigações.
  Senão o documento fica `bloqueado` com o motivo:
  `total_origem_ausente`, `valor_malformado`, `soma_linhas_diverge`,
  `total_negativo`, `desconto_sem_placa_ambiguo`,
  `desconto_sem_obrigacao_da_placa`, `desconto_maior_que_obrigacoes`,
  `sem_vencimento`, `veiculo_em_conflito`, `placa_de_outro_cliente`,
  `legado_*`.
- Linha sem valor é ignorada só se a soma do documento fecha sem ela.
- Cliente atendido × pagador: se a placa é de outro cliente cujo contrato
  tem este como interveniente, a cobrança é do atendido com
  `payer_client_id` = pagador. Fora disso, placa de outro cliente bloqueia.

## 6. Decisões da empresa

Implementadas como proposta (podem ser revistas; efeito testado):

- **D1 — onde o desconto abate.** Mesma placa; primeiro o mesmo mês; depois
  mensalidade > pró-rata > 1ª mensalidade > carnê > demais; na ordem do
  documento. Desconto sem placa só com placa única no documento.
  Efeito: o total por documento e por placa é exato; a divisão entre
  mensalidade e serviço da MESMA placa segue esta ordem (relevante para
  receita recorrente × avulsa e NFS-e por item).
- **D2 — bonificação integral.** Obrigação zerada vira cobrança
  **cancelada** com o valor bruto e nota "Bonificada integralmente":
  ocupa o mês (o fechamento não cobra de novo) e fica fora dos totais.

Pendentes (o sistema bloqueia até decidir — números da cópia do banco de
desenvolvimento em 30/09/2026, ver [validação](../validacao/fase-04.md)):

- **D3 — soma das linhas ≠ total do documento** (435 documentos
  importados antes da Fase 04): 73 por arredondamento (≤ 2 centavos;
  −R$ 0,22 no total), 261 com desconto não discriminado (−R$ 6.794,99) e
  101 com acréscimo não discriminado (+R$ 3.930,85; ex.: "Tarifas
  bancária"). **Proposta:** arredondamento ajustado na maior obrigação do
  documento; desconto não discriminado alocado pela regra D1; acréscimo
  como cobrança avulsa "Acréscimo não discriminado no SGR". Hoje: bloqueia.
- **D4 — compensar os descontos descartados pela importação antiga**
  (1.079 documentos, R$ 33.758,16): 1.007 pagos (R$ 31.024,36 de receita
  registrada a mais), **14 em aberto (R$ 383,08 cobráveis a mais)**, 58
  cancelados (sem efeito em totais). **Proposta:** compensar pagos e em
  aberto com `scripts/sgr_backfill.py --aplicar --compensar-descontos
  --operador ...` (valor levado ao líquido, histórico antes/depois); em
  aberto primeiro.
- **D5 — linhas idênticas colapsadas pelo importador antigo** (89
  documentos, R$ 7.158,92 não importados: R$ 7.093,93 pagos, R$ 64,99 em
  aberto). O deduplicador antigo (cliente + nosso número + título +
  competência + valor) apagava a 2ª linha igual do mesmo boleto. A
  importação nova preserva por ocorrência; os documentos legados ficam
  bloqueados (`legado_incompleto`). **Proposta:** conferir no SGR se as
  linhas repetidas são cobranças distintas e lançar a diferença como
  cobrança avulsa pelo fluxo normal.
- **D6 — vínculos históricos com dono diferente** (160 cobranças pagas, 7
  veículos, 5 clientes, sem contrato): provável transferência no SGR; o
  vínculo pode estar historicamente certo. **Proposta:** revisar se telas
  do novo dono (linha do tempo do veículo) expõem cobranças do anterior
  antes de liberar o portal do cliente; nada é alterado automaticamente.
- **D7 — hosts de download** ([hosts-permitidos.md](hosts-permitidos.md)).
