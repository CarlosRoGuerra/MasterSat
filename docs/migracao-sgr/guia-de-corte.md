# Migração SGR → MasterSat — guia de corte, retomada e reconciliação

Para quem executa a migração (TI) e para quem confere o resultado
(financeiro). Política de status e conflitos em
[politica-conflitos.md](politica-conflitos.md); downloads em
[hosts-permitidos.md](hosts-permitidos.md).

## 1. Estratégia: rodadas incrementais versionadas + rodada final congelada

- Cada rodada `--apply` é uma **execução** (`sgr_execucoes`) com checkpoint
  por cliente. A identidade de origem permite repetir quantas vezes for
  preciso: o que já existe é comparado com a origem, não reinserido.
- Antes do corte, rodadas incrementais trazem o grosso e a fila de
  conflitos é trabalhada.
- **Corte:** o SGR é congelado (sem baixa, emissão ou edição), roda-se a
  rodada final e o manifesto precisa cumprir os critérios da seção 4.
  Depois disso o SGR deixa de ser a origem.

Não é um sincronizador contínuo: entre o corte e o desligamento do SGR,
qualquer mudança feita lá precisa de nova rodada.

## 2. Pré-requisitos

1. Backup verificável do banco e do MinIO (Fase 00: `docs/contingencia-recuperacao.md`).
2. Fases 00–03 aplicadas e migration `a4c7e2f9b1d6` (`alembic upgrade head`).
3. Credenciais do SGR no `.env` do ambiente que roda o script (nunca no código).
4. Janela do SGR: a conta de integração só autentica em **dia útil e
   horário comercial**; fora disso responde 401 com "Restrição de data/horário".
5. Hosts de download aprovados (decisão D7) — sem isso os arquivos ficam
   pendentes, o resto importa normalmente.

## 3. Passo a passo

Todos os comandos a partir de `backend/`. Sem `--apply` nada é gravado nem
baixado. Banco não local exige `--permitir-banco-remoto`.

| # | Comando | O que conferir |
|---|---|---|
| 1 | `python scripts/sgr_backfill.py` | Relatório das cobranças já importadas antes da Fase 04: conciliáveis, bloqueadas por motivo, descontos descartados (R$), vínculos cruzados. Decidir D3–D6 |
| 2 | `python scripts/sgr_backfill.py --aplicar` (e, se aprovado, `--compensar-descontos --operador "Nome"`) | Documentos legados ganham proveniência; compensação com histórico em cada cobrança |
| 3 | `python scripts/sgr_import.py --limit 20` | Simulação: contagens, bloqueios, conflitos, **HOSTS DOS ARQUIVOS**, manifesto em `scripts/sgr_saida/` |
| 4 | `python scripts/sgr_import.py --limit N --apply` | Execução gravada; anote o número `EXECUÇÃO #` |
| 5 | `python scripts/sgr_import.py --conflitos` → `--resolver ID --decisao ... --operador ... --justificativa ... --apply` | Fila zerada ou decidida |
| 6 | `python scripts/sgr_import.py --arquivos --apply` | Downloads pendentes/bloqueados resolvidos |
| 7 | Congelar o SGR e repetir 4–6 | Critérios da seção 4 |

Histórico financeiro: sem `--de/--ate` a leitura é por cliente (paginada até
o fim); com `--de AAAA-MM --ate AAAA-MM` é por mês (mais rápida, e um mês
que falha interrompe tudo — nada é gravado).

## 4. Critérios para declarar o corte

O resumo final mostra `EXECUÇÃO #N: <status>`:

| Status | Significa |
|---|---|
| `concluida` | Todas as coleções completas, nenhuma diferença, nenhum documento bloqueado, nenhum conflito aberto, nenhum arquivo pendente |
| `concluida_com_pendencias` | Gravou tudo o que podia; há documento bloqueado, conflito ou arquivo pendente (listados) |
| `incompleta` | Alguma coleção/cliente não foi lido por inteiro ou uma unidade falhou — **não serve para corte**; rode de novo (`--retomar N`) |
| `falhou` | A unidade de planos falhou; nada de cliente foi tentado |

Corte só com `concluida`, ou `concluida_com_pendencias` em que **cada**
pendência tem decisão registrada (conflito resolvido, documento bloqueado
aceito por escrito pelo financeiro com o valor envolvido).

## 5. Reconciliação (manifesto)

Gravado a cada rodada em `scripts/sgr_saida/manifesto-<execução>-<data>.json`
(fora do git) e em `sgr_execucoes.manifesto`. Só códigos de origem,
competências, status e centavos — sem nome, CPF ou placa.

- `diferencas`: por documento conciliado, total da origem × soma das
  cobranças ligadas (`valor_divergente`) e grupo de status
  (`status_divergente`). Tem de ser `[]`.
- `por_cliente_competencia_status`: `origem_centavos`, `local_centavos`,
  `diferenca_centavos` por (cod_cliente, competência, aberto/pago).
- `documentos_bloqueados`: código, motivo e total — cada um é dinheiro que
  NÃO entrou e precisa de decisão.
- `coletas.incompletas` / `clientes_com_coleta_incompleta`: o que não foi
  lido por inteiro.
- `conflitos_abertos`, `arquivos` (pendente/baixado/falhou/bloqueado/sem_url).

Comparar duas rodadas = comparar os dois manifestos por
(cod_cliente, cod_boleto, competência, status, centavos).

## 6. Retomada

- Falha de um cliente desfaz só aquele cliente; os já confirmados ficam.
- `--retomar N` pula os clientes aplicados na execução N **com o mesmo
  dado de origem** (hash) e processa o resto; sempre reavalia os planos e o
  outbox de arquivos.
- Arquivo que falhou (timeout, storage fora, banco caiu depois do upload)
  é tentado de novo em qualquer rodada ou com `--arquivos`, gravando na
  MESMA chave de objeto — sem documento duplicado nem objeto órfão.
- Código de saída 2 = execução incompleta (próprio para agendador repetir).

## 7. Limitações do escopo ativo/inadimplente

- Só entram clientes com situação ATIVO/ATIVADO/INADIMPLENTE e veículos
  ATIVO/ATIVADO/INADIMPLENTE. Fora do escopo aparecem só como contagem.
- **Cliente que sai do escopo depois de importado** (ex.: cancelado no SGR)
  deixa de ser lido: pagamentos/cancelamentos posteriores dele na origem
  NÃO chegam ao MasterSat. Na janela de corte, conferir no SGR os clientes
  que mudaram de situação desde a última rodada.
- Cobranças de veículos fora do escopo entram no cliente, sem veículo.
- `--limit` lê só os N primeiros clientes elegíveis: a coleção de clientes
  fica marcada "leitura interrompida ao juntar o limite" — não é migração
  completa.
- Cadastro não sincroniza (seção 1 da política).
- Premissa não homologada contra a API real: após a última página o SGR
  devolve lista vazia (ou 404). Validar com `--limit 5` na simulação antes
  do corte; se o SGR devolver erro, a coleção aparece incompleta (não passa
  despercebido).

## 8. Rollback

Ensaiado em 30/09/2026 ([validação](../validacao/fase-04.md), seção 5):

1. `alembic downgrade b7d3e1f5a902` **com a imagem nova** (a antiga não sobe
   com revisão desconhecida). As tabelas `sgr_*` com dados viram
   `fase04_preservado_*`; cobranças, clientes, veículos e documentos ficam
   onde estão.
2. Subir a imagem antiga. Telas e API leem os dados importados normalmente.
3. **Não rodar o importador antigo** sobre dados da Fase 04: ele compara
   pelo valor bruto e pela placa sem dono — no ensaio, criaria 2 cobranças
   (desconto duplicado e documento bloqueado por transferência).
4. Para voltar: `alembic upgrade head` com a imagem nova restaura as
   tabelas preservadas; a rodada seguinte não duplica nada.
5. Correção de dado importado é sempre operação compensatória registrada
   (`billing_change_logs`, resolução de conflito, compensação do backfill)
   — nunca apagar e reimportar.
