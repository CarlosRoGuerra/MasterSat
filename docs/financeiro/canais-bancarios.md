# Matriz de canais bancários

Fase 03 (FIN-07). Uma obrigação usa **um** canal de emissão por vez. A
política é a mesma para todos (`app/services/titulo_bancario.py`): título em
qualquer canal bloqueia emissão pelo outro.

| Canal | Estado | Emissão | Conciliação | Desfecho desconhecido | Baixa |
|---|---|---|---|---|---|
| **API Ailos** (individual, lote, carnê) | **ativo** | `POST /ailos/boletos`, `/boletos/lote`, `/carne/lote` | consulta por número do documento, worker horário com checkpoint | tratado (consulta resolve; ver runbook) | manual no internet banking; confirmada pela consulta (situação 3/5) ou pelo administrador |
| **CNAB 240** | **desligado** (`CNAB_REMESSA_HABILITADA=false`) | `POST /boletos/cnab240` → 409 `canal_cnab_indisponivel` | **não existe** (retorno CNAB não é lido) | não se aplica (arquivo, não chamada) | não rastreada |
| **CNAB 400** | **desligado** | `POST /boletos/cnab400` → 409 | **não existe** | — | não rastreada |
| Retorno Ailos (ZIP) | ativo, só armazenamento | `POST /ailos/retorno/solicitar` | o ZIP vai para o MinIO **sem leitura** | — | — |

`GET /boletos/canais` devolve esta matriz para a tela (os botões CNAB ficam
desligados com o motivo).

## Por que o CNAB está desligado

- O layout nunca foi homologado com a cooperativa: os testes provam formato e
  comprimento de linha, **não** que o banco aceite o arquivo.
- Não há leitura do arquivo de retorno: título enviado por remessa nunca seria
  baixado aqui como pago — o cliente pagaria e continuaria inadimplente.
- Antes desta fase a remessa aceitava cobrança paga, cancelada, repetida e já
  registrada pela API, e toda remessa saía com sequência 1.

## Para ligar (homologação)

1. Ambiente de homologação acordado com a Ailos; `CNAB_REMESSA_HABILITADA=true`
   **só** nele.
2. Fixtures de remessa/retorno fornecidas pelo banco (não geradas pelo próprio
   código) nos testes de `cnab240`/`cnab400`.
3. Implementar a leitura do retorno CNAB e ligá-la à mesma política de baixa
   da API (`marcar_billing_pago`, pendências).
4. Com o canal ligado já valem: seleção só de cobrança em aberto, sem
   repetição e sem título em outro canal; sequência própria por layout;
   SHA-256 e conteúdo guardados (`GET /boletos/remessas`,
   `/remessas/{id}/arquivo` devolve os mesmos bytes); itens reservados;
   descarte de remessa **não enviada** pelo administrador
   (`POST /boletos/remessas/{id}/descartar`).

## Rollback do canal

O flag é por ambiente e não altera dados. Remessas e itens gravados continuam
no banco (também preservados num downgrade da migration).
