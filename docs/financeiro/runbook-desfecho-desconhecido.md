# Runbook — boleto com desfecho desconhecido, baixa pendente e pendências de conciliação

Fase 03 (FIN-02, FIN-03, FIN-05, FIN-06). Para quem opera o financeiro e para
o suporte técnico. A regra de fundo: **enquanto não se sabe se existe um
boleto pagável no banco, a cobrança não muda e não é emitida de novo.**

## 1. Onde ver

- **Financeiro → Visão geral → "Pendências bancárias"**: lista tudo que
  precisa de ação e a saúde da conciliação (títulos acompanhados, consulta
  mais antiga, janela estimada, alerta de atraso).
- **Detalhe da cobrança → "Título no banco"**: estado, nosso número, baixa e
  pendência.
- API: `GET /api/v1/ailos/pendencias`, `GET /api/v1/ailos/conciliacao`.
- Log do backend: uma linha por rodada da conciliação
  (`Conciliação Ailos: N consultado(s), …, atraso máx. Xh, janela ~Yh`) e
  alerta ao administrador quando surgem pendências novas.

## 2. Estados do título

| Estado | O que significa | O que a cobrança aceita |
|---|---|---|
| sem título | nunca foi ao banco, ou o banco recusou de forma definitiva (4xx) | tudo |
| em registro | pedido enviado há menos de 30 min (individual ou lote) | nada; aguarde |
| **desfecho desconhecido** | o pedido pode ter chegado ao banco e não houve resposta conclusiva: timeout de leitura, erro 5xx, "já cadastrado" sem os dados, falha local depois do aceite, ou reserva sem resposta há mais de 30 min | **nada**: nem editar, receber, cancelar, excluir ou emitir de novo |
| registrado | boleto ativo no banco | receber (por fora), cancelar (com confirmação) |
| baixado | título registrado com baixa confirmada | receber, cancelar, liberar competência |
| remessa CNAB | incluído numa remessa CNAB reservada (canal desligado hoje) | receber, cancelar com confirmação |

Tabela completa por operação: `app/services/titulo_bancario.py` (`_PERMITIDO`).

## 3. Desfecho desconhecido

**Sintoma:** ao gerar boleto a tela mostra "A Ailos não confirmou o registro e o
boleto pode ter sido criado no banco…" (HTTP 502 `desfecho_desconhecido`), ou
uma tentativa de editar/cancelar responde 409
`boleto_ailos_desfecho_desconhecido`.

**Resolução automática.** A conciliação horária consulta a Ailos pelo número do
documento (= id da cobrança) e decide:

- encontrou o título → grava linha digitável/código de barras (registrado);
- a Ailos respondeu 400/404 **e** já passaram 10 min do envio → não
  registrado; a cobrança volta a aceitar emissão e edição (fica no histórico
  da cobrança, `field_name = registro_ailos`);
- ainda dentro dos 10 min → aguarda a próxima rodada;
- consulta falhou → continua bloqueada e o erro fica no título.

**Resolução manual (qualquer perfil financeiro):** botão **"Consultar desfecho
na Ailos"** no detalhe ou na lista de pendências
(`POST /ailos/boletos/{id}/consultar-desfecho`). Não reenvia nada.

**Retry de parcela de carnê/lote:** "Gerar boletos pendentes" e o retry
individual **consultam antes de reenviar**; só registram de novo se a Ailos
confirmou que o título não existe.

**Saída administrativa (último recurso):** se a consulta não resolve e alguém
conferiu no internet banking da Ailos que o título NÃO existe, o administrador
usa `POST /ailos/boletos/{id}/declarar-nao-registrado` com justificativa.
Fica em `billing_change_logs` com o usuário. **Nunca use** se houver dúvida:
declarar não registrado um título que existe permite emitir outro para a mesma
cobrança.

> Premissa a validar em homologação: a consulta de boleto da Ailos responde
> 400/404 para número de documento inexistente. Até lá, o prazo de 10 min e a
> saída administrativa cobrem a incerteza.

## 4. Baixa pendente

**Quando aparece:** a cobrança local deixou de valer, mas o boleto continua
pagável no banco — cobrança cancelada (inclusive em lote e por exclusão de
contrato), recebida por fora (Pix/dinheiro) ou removida no passado (inventário
da migration). O convênio **não tem baixa pela API**.

**O que fazer:**

1. Baixar o título no internet banking da Ailos (nosso número na tela).
2. Esperar a conciliação: quando a consulta devolver situação **3 (baixado)**
   ou **5 (liquidado)**, a baixa é confirmada sozinha.
3. Se precisar liberar o mês antes da próxima rodada, o **administrador**
   confirma: botão "Confirmar baixa no banco"
   (`POST /ailos/boletos/{id}/confirmar-baixa`, justificativa obrigatória,
   só para cobrança já cancelada/paga/removida).

**Efeitos enquanto pendente:** a competência da mensalidade não pode ser
liberada (409 `baixa_bancaria_pendente`); cancelar pedindo "liberar
competência" oferece cancelar **sem** liberar; PDF, e-mail, link público e
carnê não entregam o boleto; a conciliação continua consultando o título.

## 5. Pendências de conciliação

| Código | Significado | Tratamento |
|---|---|---|
| `pagamento_divergente` | o banco informa valor pago diferente do título; a cobrança **não** foi quitada | registrar o recebimento com o valor do banco e classificar a diferença (desconto, parcial, encargos, crédito); a pendência se encerra sozinha e a baixa é confirmada |
| `pago_em_cobranca_cancelada` | cliente pagou um boleto de cobrança cancelada | decidir com o cliente: estornar o cancelamento (reabrir e receber) ou devolver; depois "Encerrar pendência" com a justificativa |
| `pago_em_cobranca_removida` | idem, cobrança removida (legado) | idem |
| `possivel_pagamento_duplicado` | a cobrança foi recebida por fora e o boleto também foi pago | conferir extrato; devolver ou lançar crédito; "Encerrar pendência" |
| `baixado_com_cobranca_aberta` | alguém baixou o título no banco e a cobrança segue aberta aqui | cancelar a cobrança (encerra a pendência) ou lançar nova cobrança |

"Encerrar pendência" (`POST /ailos/boletos/{id}/resolver-pendencia`) grava a
justificativa no histórico da cobrança.

## 6. Conciliação atrasada

`alerta_atraso` liga quando algum título monitorado está há mais de 26 h sem
consulta. Causas prováveis, em ordem:

1. Sessão do cooperado Ailos caída (banner "Sessão Ailos desconectada").
2. Carteira maior que o orçamento: janela ≈ carteira ÷ 300 horas. Ajuste
   `AILOS_CONCILIACAO_ORCAMENTO` (e confirme o limite de requisições com a
   Ailos).
3. Erros individuais repetidos: veja `ultima_consulta_erro` na lista de
   pendências. Um título com erro vai para o fim da fila — não trava os outros.

## 7. Depois do deploy da Fase 03

A migration marca, sem chamar o banco:

- **baixa pendente** em todo título registrado cuja cobrança está cancelada,
  removida ou recebida por fora — a conciliação confirma sozinha os que já
  foram baixados/liquidados; o que sobrar é título realmente pagável;
- **desfecho desconhecido** nos `ERRO_REGISTRO` cuja última tentativa não teve
  resposta conclusiva — a conciliação consulta e resolve.

Antes: `python scripts/preflight_fase03.py` num snapshot (só leitura) para ver
quantos são. Depois: acompanhar `GET /ailos/conciliacao` até a janela
estabilizar.
