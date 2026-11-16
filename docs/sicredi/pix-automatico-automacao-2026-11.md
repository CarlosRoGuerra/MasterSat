# Pix Automático pelo cadastro — relatório de entrega (16/11/2026)

Branch `feat/pix-automatico-cadastro` (base `main` 7b59f8d). **Não está na `main` nem na VPS.** Nenhuma chamada real ao Sicredi, nenhum e-mail real e nenhum dado de produção foi usado.

## 1. Diagnóstico do funcionamento original

| Etapa | Situação encontrada |
|---|---|
| Cadastro | Pix Automático já era uma forma de cobrança. O formulário só dizia "após salvar, abra os detalhes do cliente". |
| Autorização (Jornada 2) | `criar_recorrencia` já tinha reserva durável antes do banco, recuperação de timeout pelo vínculo e uma autorização ativa por cliente (índice único). Só era acionada pelo botão nos detalhes do cliente. |
| Convite ao cliente | Inexistente. O QR só aparecia na tela; o envio dependia de copiar e colar. |
| Fechamento | **Já enfileirava** as cobranças Pix (`enfileirar_fechamento`) na mesma transação, sem rede. Cobranças geradas antes da autorização existir ficavam paradas até o botão "Preparar cobranças do fechamento". |
| Worker | Consultava autorizações, enviava agendamentos na janela de 2 a 10 dias e conciliava com dados autenticados. Falhas iam só para o log. |
| Financeiro | Pix aparecia só no detalhe de cada cobrança. Não havia visão consolidada nem alertas. |
| Webhooks | Rotas `/webhooks/<segredo>/{pix,rec,cobr}` corretas pela especificação do Banco Central (sufixos e chaves `pix`/`recs`/`cobsr` conferidos no `openapi.yaml` 2.9.0). O nginx gravava a URL com o segredo no log de acesso. |
| Publicação | `deploy.sh` não subia o `backend-worker`. |
| Permissões | Cliente: admin e operacional. API Pix: admin e financeiro (há teste garantindo). Só o admin tem as duas. |

## 2. Fluxos

Antes:

```
Cadastro (Pix) ─► salvar ─► abrir detalhes ─► "Gerar autorização" ─► copiar QR manualmente
Fechamento ─► fila só se já havia autorização ─► senão: botão "Preparar cobranças"
Worker ─► consulta/agenda/concilia ─► falhas só no log
```

Proposto (implementado):

```
Cadastro (Pix + quadro Sicredi) ─► salvar cliente (commit)
   └─► admin: POST autorização (reserva durável → Sicredi) ─► continuação na mesma tela
         ├─ QR/Copia e Cola + convite por e-mail registrado ("autorização Pix gerada")
         └─ falha/indisponível/desconhecido ("autorização Pix pendente", motivo, cadastro preservado)
   └─► operacional: "pendente — autorização é do Financeiro" ─► painel Pix do Financeiro
Worker ─► consulta autorização ─► APROVADA ─► associa cobranças já geradas pelo fechamento
       ─► envia convites pedidos cujo QR chegou depois ─► agenda na janela (só com envio automático liberado)
       ─► concilia com dados autenticados ─► 3 rodadas com falha = alerta ao admin
Financeiro ─► botão "Pix Automático" (contador) ─► situações e próxima ação por cliente/cobrança
```

O que continua dependendo de pessoas: liberar escrita/produção/envio automático; o fechamento mensal (conferências e NFS-e inalteradas); gerar autorização quando o cadastro foi feito pelo operacional; contatar clientes; decidir cobrança por outro meio; cancelar autorização ou agendamento.

## 3 e 4. Arquivos alterados e justificativa

| Arquivo | Por quê |
|---|---|
| `backend/app/services/sicredi_pix.py` | Pedido de convite na reserva da autorização; `enfileirar_pendentes` (mesma regra do botão); associação automática no worker só para autorizações aprovadas; savepoint na reserva para corrida com o worker não desfazer o fechamento. |
| `backend/app/services/sicredi_pix_convite.py` (novo) | Convite por e-mail com reserva prévia, registro de tentativas e resultado incerto sem reenvio automático. |
| `backend/app/services/sicredi_pix_painel.py` (novo) | Situações derivadas (sem estado novo) e indicadores do Financeiro. |
| `backend/app/api/v1/endpoints/sicredi_pix.py` | Convite opcional na autorização, `POST /clientes/{id}/convite`, `GET /painel`, SMTP no `/status`. |
| `backend/app/models/sicredi_pix.py`, `alembic/versions/e6b2d4f8a1c3_sicredi_pix_convite.py` | 7 colunas de convite, anuláveis (contador com padrão 0). Aditiva e reversível. |
| `backend/app/schemas/sicredi_pix.py` | Campos novos de saída e entrada `enviar_convite` (padrão `false`, comportamento anterior preservado). |
| `backend/app/main.py` | Alerta ao admin após 3 rodadas seguidas com falha no worker Pix. |
| `frontend/app/clientes/_components/client-form-pix-block.tsx` (novo), `client-form-modal.tsx`, `page.tsx` | Quadro Pix no cadastro e continuação depois de salvar; proteção contra duplo clique. Outras formas de cobrança sem mudança visual. |
| `frontend/app/clientes/_components/client-pix-cadastro.tsx` (novo) | Orquestração pós-salvamento que nunca lança erro e o modal de continuação. |
| `frontend/app/clientes/_components/client-pix-automatico.tsx` | Convite (status, reenvio, WhatsApp manual), rótulos de situação e avisos de QR pendente/resultado desconhecido. |
| `frontend/app/financeiro/_components/pix-automatico-painel.tsx` (novo), `financeiro/page.tsx` | Botão com contador e painel ao lado de "Pendências bancárias". |
| `nginx/nginx.conf` | Callbacks Pix sem `access_log` e com `error_log` crítico. |
| `deploy.sh` | Sobe `backend-worker`. |
| `docs/sicredi/pix-automatico.md` | Guia atualizado. |

## 5. Reutilizado sem alteração

`criar_recorrencia` (reserva, idempotência, recuperação), `consultar_recorrencia`, `enfileirar_fechamento` (regra de ciclo e conflito), `processar_cobranca` (janela, política bancária, valor mínimo, `NAO_PERMITE`), `_aplicar_recebimento` (conciliação estrita), webhooks como aviso, `titulo_bancario` (bloqueio de boleto concorrente), `enviar_email`/limites SMTP, `_alerta_admin`, o fechamento (`execute_closure`) e as permissões.

## 6. Adicionado

Quadro Pix no cadastro; continuação com QR; convite por e-mail registrado; reenvio sem nova autorização; associação automática pós-aprovação; painel e contador no Financeiro; alerta de falhas do worker; endurecimento do nginx; worker no `deploy.sh`.

## 7. Testes realizados (resultados reais)

Comparação feita com uma cópia limpa da `main` (7b59f8d) nas mesmas condições.

| Execução | `main` | Branch | Observação |
|---|---|---|---|
| Backend completo, PostgreSQL 16 isolado (Docker, rede interna) | 45 falhas, 3250 aprovados | 45 falhas, 3297 aprovados | Mesmas 45 falhas (lista idêntica). São a linha de base registrada na auditoria de 15/11/2026: esquemas de rastreador/contrato, relatórios, planos, produtos, NFS-e, homologação Sicredi, migration de lotes com revisão fixa e 2 de concorrência Pix sem agência/conta configuradas. |
| Backend completo, SQLite (Windows) | 39 falhas, 3166 aprovados | 39 falhas, 3208 aprovados | Mesmas 39 falhas. Os testes de migration não importam o `alembic` no Python do Windows (a pasta `backend/alembic` sombreia o pacote); rodam no Docker. |
| Pix + migration + concorrência em PostgreSQL | — | 104 aprovados, 2 falhas da linha de base | As 2 falham igualmente na `main`; com agência/conta de teste no ambiente, os 3 testes de concorrência passam na branch. |
| Frontend (Vitest) | — | 458 aprovados de 458 | Inclui 6 testes de páginas do Financeiro que quebraram com a primeira versão do botão (corrigido). |
| Typecheck (`tsc --noEmit`) e lint dos arquivos alterados | — | sem erros | 3 avisos antigos em `financeiro/page.tsx`. |
| `next build` | — | compilado, 24 páginas geradas | Aviso conhecido de symlink do `standalone` no Windows. |
| `nginx -t` com a configuração nova | — | aprovado | Certificado descartável e hosts fictícios. |

Testes novos (51 no total após a revisão): `test_sicredi_pix_automacao.py` (42: cadastro novo/existente/editado/duplicado, Sicredi indisponível com recuperação, integração desligada, operacional, responsável diferente, QR indisponível, aprovação/recusa/expiração/cancelamento, reenvio, falha incerta e limite do SMTP, envio interrompido, vários contratos e veículos com um responsável, fechamento duas vezes, cobrança anterior à autorização, corrida na reserva, 15 situações do painel, pagamento confirmado e conciliação repetida, worker em leitura), `test_sicredi_pix_automacao_postgres.py` (3: convite disputado por duas sessões, dois workers associando, fechamento × worker no mesmo ciclo) e `test_sicredi_pix_convite_migration.py` (2: SQLite e PostgreSQL com dados existentes). No frontend, 20 novos testes de componentes.

Não houve homologação bancária: tudo acima usa transporte Sicredi e SMTP substituídos.

## 8. Falhas encontradas e corrigidas durante o trabalho

- Contador "autorizações pendentes" podia contar um agendamento com resultado desconhecido: passou a ser calculado pela autorização de cada pagador.
- A associação automática repetia toda rodada pagadores cujas cobranças vencem antes da autorização (podia deixar outros para trás com o limite): filtro adicionado.
- Botão do painel quebrava telas do Financeiro quando a resposta vinha fora do formato (backend antigo): o componente passou a validar a resposta.
- Editar um cliente que já era Pix com autorização encerrada pediria nova autorização e e-mail sem o operador perceber: agora exige marcar "Solicitar uma nova autorização".
- Segredo do webhook no log de acesso do nginx: location dedicado.

### Revisão independente (agente separado, somente leitura, sobre os commits da branch)

| # | Gravidade | Achado | Desfecho |
|---|---|---|---|
| 1 | Alta | Edição comum de cliente que já era Pix pedia autorização e e-mail por padrão; com a situação ainda carregando, recriava autorização cancelada/recusada | Corrigido: pedido automático só quando Pix acabou de ser escolhido; caso contrário exige marcar a caixa, que fica bloqueada até a situação carregar |
| 2 | Média | Cobrança em conflito de ciclo aparecia como "associação automática" | Corrigido: situação "Outra cobrança já ocupa este ciclo" com a cobrança ocupante |
| 3 | Média (operação) | `deploy.sh` passa a subir o worker, que roda todos os jobs; convites e associação rodam com o canal habilitado | Mantido e registrado como decisão de implantação (seção 12) |
| 4 | Baixa | Cobrança paga por outro caminho com agendamento Pix ainda ativo ficava oculta como "paga" | Corrigido: "Paga fora do Pix com agendamento ativo", visível por padrão |
| 5 | Baixa | Worker parado deixava cobrança vencida como "aguardando confirmação" sem alerta | Corrigido: "Consulta ao banco atrasada" (falha de integração, nunca recusa) dois dias após o vencimento |
| 6 | Baixa | Limite do SMTP contava como falha da fila e podia disparar alerta falso | Corrigido |
| 7 | Baixa | Tentativa bloqueada pelo limite do SMTP incrementava o contador | Corrigido |

O revisor confirmou como corretos: proteção contra autorização/agendamento duplicados, baixa só com dados autenticados, fechamento inalterado, permissões, conteúdo do convite sem CPF/CNPJ, idRec ou txid, migration aditiva e logs do webhook.

Depois das correções: backend Pix/migrations/concorrência/fechamento em PostgreSQL 255 aprovados, 0 falhas; suíte local com as mesmas 39 falhas da `main`; frontend 459 de 459.

## 9. Riscos pendentes

- **Homologação bancária ponta a ponta ainda não feita** (escopos de escrita, QR oficial, aprovação/recusa pelo pagador, agendamento, liquidação, callbacks). Os testes usam transporte controlado.
- **Operacional não gera autorização** (decisão de permissão preservada). O fluxo completo numa tela só existe para o admin; o operacional depende do Financeiro pelo painel. Mudar isso é decisão do negócio.
- Cobrança cujo agendamento foi cancelado (por exemplo, autorização recusada) não pode ser reagendada numa nova autorização (`billing_id` único na fila — regra anterior). Aparece no painel como "Agendamento cancelado — cobrar por outro meio".
- Duas cobranças Pix no mesmo ciclo continuam marcando a primeira como "revisão necessária" (regra anterior); agora isso também pode acontecer pela associação automática.
- Convite por WhatsApp é só abertura de conversa; não há integração de envio automático no projeto.
- Com `SICREDI_PIX_ENVIO_AUTOMATICO=false`, a fila fica associada mas não é enviada; o painel sinaliza "prazo terminando". O worker ainda envia **e-mails de convite** pedidos no cadastro (não é escrita bancária).
- Se o proxy de produção for o Caddy, ele não registra acesso por padrão; manter assim antes de cadastrar webhooks.
- Achados abertos da auditoria de 15/11 (NO-GO) continuam valendo e não foram tratados aqui.

## 10. Variáveis de ambiente

Nenhuma variável nova. Continuam: `SICREDI_PIX_ENABLED`, `SICREDI_PIX_AMBIENTE`, `SICREDI_PIX_CLIENT_ID`, `SICREDI_PIX_CLIENT_SECRET`, `SICREDI_PIX_CERT_PATH`, `SICREDI_PIX_KEY_PATH`, `SICREDI_PIX_CA_PATH`, `SICREDI_PIX_CHAVE`, `SICREDI_PIX_AGENCIA`, `SICREDI_PIX_CONTA`, `SICREDI_PIX_ESCRITA_HABILITADA`, `SICREDI_PIX_PRODUCAO_CONFIRMADA`, `SICREDI_PIX_ENVIO_AUTOMATICO` (só no compose do worker), `SICREDI_PIX_WEBHOOK_TOKEN`, `SICREDI_PIX_WORKER_INTERVAL_SECONDS`, `RUN_BACKGROUND_JOBS`, `ALERT_WEBHOOK` (opcional, alerta do worker). O convite usa o SMTP já configurado no painel.

| Controle | Onde vale | Efeito |
|---|---|---|
| `SICREDI_PIX_ENABLED` | API e worker | Liga o canal e a thread do worker. |
| `SICREDI_PIX_ESCRITA_HABILITADA` | API (`.env`) | Libera criar/cancelar autorização e ações manuais. |
| `SICREDI_PIX_PRODUCAO_CONFIRMADA` | API (`.env`) | Obrigatória em produção, além da anterior. |
| `SICREDI_PIX_ENVIO_AUTOMATICO` | só worker (compose) | Sobrescreve escrita e confirmação do worker: `false` = worker só consulta, associa localmente e envia convites. |

## 11. Homologação Sicredi (HML)

1. Ambiente separado da produção, `.env` com `SICREDI_PIX_AMBIENTE=homologacao`, credenciais/certificados de HML, `SICREDI_PIX_ENABLED=true`, `SICREDI_PIX_ESCRITA_HABILITADA=true`, agência/conta de HML e SMTP de teste (ex.: Mailpit).
2. `alembic upgrade head` (revisão `e6b2d4f8a1c3`) e `POST /sicredi-pix/testar-conexao` (escopos de leitura e escrita).
3. Cadastrar cliente de teste com Pix e e-mail de teste, como admin: conferir "autorização Pix gerada", QR/Copia e Cola e o e-mail recebido.
4. Aprovar no ambiente do pagador de HML; aguardar o worker (`RUN_BACKGROUND_JOBS=true`) mudar para "Autorizada".
5. Repetir com recusa e com expiração; conferir o painel.
6. Gerar cobrança de teste com vencimento entre 2 e 10 dias, liberar `SICREDI_PIX_ENVIO_AUTOMATICO=true` **só em HML** e acompanhar `ATIVA` → liquidação → baixa.
7. Simular débito não realizado e conferir "Débito não realizado" e o contador.
8. Registrar webhooks em HML (`POST /webhooks/configurar`) com URL pública HTTPS e conferir que o log do proxy não mostra o segredo.
9. Cancelar agendamento antes do envio e depois do envio (até 22h da véspera).

## 12. Implantação e reversão

Implantação (somente com aprovação explícita). Atenção: o `deploy.sh` agora sobe o `backend-worker`, que executa todos os jobs de fundo (conciliações Ailos/Sicredi, fila de e-mails, Pix). Em produção ele já é previsto pelo `docker-compose.prod.yml` e era subido à mão; confirme na VPS antes de usar o script. backup `pg_dump -Fc` e cópia privada do `.env`; merge na `main`; na VPS `git pull --ff-only`; `up -d --build backend backend-worker frontend` (migration aditiva roda no boot); recarregar o nginx (`nginx -t && nginx -s reload`) para o novo location; conferir revisão `e6b2d4f8a1c3`, `app/login` 200 e `api /auth/me` 401; manter `SICREDI_PIX_ESCRITA_HABILITADA`, `SICREDI_PIX_PRODUCAO_CONFIRMADA` e `SICREDI_PIX_ENVIO_AUTOMATICO` em `false`.

Reversão: voltar às imagens anteriores. As colunas novas são ignoradas pelo código antigo; não é preciso downgrade. Se preferir remover as colunas, `alembic downgrade d2a7c9e41b60` com a imagem nova (apaga só o histórico de convites).

## 13. Checklist para produção

- [ ] Revisão do código e da decisão de permissões (operacional x Financeiro).
- [ ] Homologação HML completa (seção 11), com evidências.
- [ ] SMTP configurado e testado para o remetente de cobrança.
- [ ] Backup verificado antes do deploy.
- [ ] Worker confirmado em execução na VPS (`docker compose ps backend-worker`).
- [ ] nginx recarregado e log de acesso sem o caminho dos callbacks.
- [ ] Liberação explícita, em etapas: escrita e confirmação de produção (API); só depois `SICREDI_PIX_ENVIO_AUTOMATICO`.
- [ ] Primeiro cliente real acompanhado do convite à liquidação.
