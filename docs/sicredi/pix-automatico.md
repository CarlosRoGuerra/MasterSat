# Pix Automático Sicredi no MasterSat

Pix Automático é uma opção de **Forma de cobrança**, junto de boleto mensal, carnês e cartão de crédito. A seleção é uma preferência do cadastro; a autorização do pagador é obtida separadamente no aplicativo do banco.

## Uso pela equipe

1. Em Clientes, editar o cadastro e selecionar **Pix Automático** em Forma de cobrança.
2. Salvar e abrir os detalhes do cliente. O bloco **Pix Automático · Sicredi** apresenta a autorização e os agendamentos. O mesmo acompanhamento aparece nos detalhes da cobrança no Financeiro.
3. Quando o ambiente estiver liberado para escrita, informar o primeiro vencimento e gerar a autorização. A Jornada 2 fornece um QR Code e um código Copia e Cola oficiais, usados pelo pagador no aplicativo do banco.
4. Consultar a autorização até o banco informar `APROVADA`. O QR não é um boleto nem uma confirmação de pagamento.
5. Fazer o fechamento pelo fluxo habitual. Os valores do responsável são consolidados em uma cobrança por ciclo. Não há taxa de emissão de boleto nessa modalidade.
6. O worker aguarda autorização e a janela de 2 a 10 dias antes do vencimento para enviar a instrução. A baixa só ocorre com liquidação identificada, valor compatível e horário informado pelo Sicredi.

O fechamento, a conferência de documentos e os filtros passam a reconhecer a nova forma. O envio por e-mail do fechamento exige agendamento confirmado e NFS-e quando obrigatória; informa Pix Automático e não exige nem anexa boleto. Nenhuma mensagem foi enviada no desenvolvimento.

Cobranças antigas não são convertidas pela mudança da preferência. A ação **Preparar cobranças do fechamento** considera apenas obrigações já identificadas como Pix Automático pelo novo fechamento. Se o primeiro vencimento não couber na autorização, ou se houver outra cobrança do mesmo ciclo, a equipe precisa revisar o lançamento.

## Regras de segurança financeira

- Uma autorização ativa por cliente e ambiente; um agendamento por cobrança e ciclo da recorrência.
- O ciclo mensal usa a data inicial como âncora, inclusive nos meses curtos. Não usa simplesmente a competência do serviço.
- O Pix usa a data acordada, inclusive em fins de semana (`ajusteDiaUtil=false`), mantendo a liquidação compatível com a janela de envio. Não se presume o calendário útil dos boletos.
- Há reserva persistente antes do envio. Um timeout não gera novo `txid` nem reenvio automático. A recuperação consulta o banco com os identificadores conservados.
- Uma fila Pix reservada ou enviada impede emissão por Ailos, Sicredi boleto e CNAB. A política bancária comum também protege edição, cancelamento e recebimento manual enquanto houver agendamento ativo.
- Cancelar um agendamento antes do envio é local. Depois do envio, exige instrução e consulta de confirmação bancária, observando até 22h da véspera do vencimento.
- Mudar o pagador ou dia de vencimento, remover o cliente ou trocar sua forma de cobrança exige encerrar a autorização ativa e resolver instruções ainda em andamento.
- O status `CONCLUIDA` isolado não dá baixa. São conferidos `idRec`, `txid`, `endToEndId`, valor e horário de liquidação. Valor diferente, devolução, pagamento repetido e cobrança cancelada/removida ficam para conferência.
- Uma liquidação já conciliada não é reaplicada. Estorno local posterior gera pendência, sem uma nova baixa silenciosa.
- A política inicial de retentativa pós-vencimento é `NAO_PERMITE`. Não há tentativa extra de débito criada pelo MasterSat.

As operações de autorização, agendamento e conciliação usam o responsável financeiro da obrigação, preservando o cliente atendido e os contratos originais.

## Configuração

A API Pix tem credenciais, endpoints e mTLS próprios. As credenciais de boleto Sicredi não são usadas nesse canal. Configure segredos somente no arquivo externo usado por `MASTERSAT_ENV_FILE` ou no cofre do ambiente. Os arquivos privados devem ser montados em volume somente para leitura, sem entrar no Git ou na imagem Docker.

```dotenv
SICREDI_PIX_ENABLED=true
SICREDI_PIX_AMBIENTE=homologacao
SICREDI_PIX_CLIENT_ID=<credencial-do-ambiente>
SICREDI_PIX_CLIENT_SECRET=<segredo-do-ambiente>
SICREDI_PIX_CERT_PATH=/app/certs/sicredi-pix/14228344000167.cer
SICREDI_PIX_KEY_PATH=/app/certs/sicredi-pix/MasterSatCSR-sem-senha.key
SICREDI_PIX_CA_PATH=/app/certs/sicredi-pix/CadeiaCompletaSicredi.cer
SICREDI_PIX_CHAVE=14228344000167
SICREDI_PIX_AGENCIA=2602
SICREDI_PIX_CONTA=208865
SICREDI_PIX_ESCRITA_HABILITADA=false
SICREDI_PIX_PRODUCAO_CONFIRMADA=false
SICREDI_PIX_ENVIO_AUTOMATICO=false
SICREDI_PIX_WEBHOOK_TOKEN=<segredo-alfanumerico-com-ao-menos-32-caracteres>
SICREDI_PIX_WORKER_INTERVAL_SECONDS=300
```

O número da conta foi normalizado a partir de `20886-5`. Conferir o formato aceito nos testes de agendamento do Sicredi antes da liberação operacional. As credenciais de HML são obtidas pelo fluxo específico das páginas 15-16 do guia; não há fallback entre ambientes.

Produção usa `https://api-pix.sicredi.com.br`. HML usa `https://api-pix-h.sicredi.com.br`. O cliente preserva validação TLS, apresenta certificado/chave/cadeia e usa OAuth `client_credentials` com Basic e formulário. O token é reutilizado conforme sua expiração e nunca aparece nas respostas da aplicação ou no resultado dos testes.

`SICREDI_PIX_ESCRITA_HABILITADA=true` libera mutações no ambiente configurado. Em produção também é obrigatório `SICREDI_PIX_PRODUCAO_CONFIRMADA=true`. Selecionar Pix no cadastro, obter token ou ligar o canal não ativa esses dois controles. A ativação exige avaliação e autorização operacional próprias.

Os escopos de leitura utilizados são `rec.read payloadlocationrec.read cobr.read pix.read webhookrec.read webhookcobr.read webhook.read`. As operações de escrita precisam também de `rec.write payloadlocationrec.write cobr.write webhookrec.write webhookcobr.write webhook.write`, cuja liberação bancária ainda deve ser validada.

O worker é iniciado apenas com `RUN_BACKGROUND_JOBS=true` e `SICREDI_PIX_ENABLED=true`, usando advisory lock próprio. No modo de leitura ele acompanha o que já foi enviado, sem consumir a fila de novas instruções.

Na composição de produção, o worker tem um controle adicional: `SICREDI_PIX_ENVIO_AUTOMATICO`, desativado por padrão. Com os dois controles de escrita da API habilitados e este controle em `false`, a equipe pode criar/cancelar autorizações pelas telas e usar os endpoints administrativos manuais, mas o worker só consulta o banco. Preparar cobranças do fechamento grava a fila local; não envia o débito nesse modo. O endpoint administrativo `POST /api/v1/sicredi-pix/processar-fila` continua sendo uma ação manual explícita. Não é executado durante a habilitação.

Somente uma liberação posterior de `SICREDI_PIX_ENVIO_AUTOMATICO=true` permite que o worker envie novas instruções, observando autorização e vencimento. Esse controle não desliga os jobs dos outros canais nem a conciliação por consulta. A liberação manual solicitada em 05/11/2026 preserva o envio automático desligado e não cadastra webhooks ou recursos financeiros.

## Webhooks

Uma ação administrativa explícita em `POST /api/v1/sicredi-pix/webhooks/configurar` registra as três famílias de callback e exige liberação de escrita. A URL pública deve ter HTTPS e porta 443. Antes de registrar, verificar DNS, certificado público, proxy e acesso externo do Sicredi.

O callback usa um segredo opaco na URL. O backend redige esse trecho nos logs de acesso e não o grava na auditoria. O proxy também deve omitir ou redigir o caminho em seus logs (por exemplo, `access_log off` no location específico de `/api/v1/sicredi-pix/webhooks/` do nginx). O segredo fica no cofre, e não em documentação ou frontend. A autenticação mTLS de entrada oferecida pelo Sicredi pode reforçar a configuração do proxy.

Os callbacks apenas sinalizam registros locais para nova consulta autenticada. Valores, status e declarações de pagamento recebidos no corpo do webhook não dão baixa diretamente. Isso também permite lidar com eventos repetidos ou fora de ordem. As consultas periódicas continuam recuperando alterações quando não houver notificação.

Não foram registrados webhooks no Sicredi durante esta implementação.

## Validação realizada e limite atual

- Autenticação pelo novo cliente HTTP no container local, usando as credenciais de produção fornecidas: token obtido sem criar recursos bancários.
- Migração `b8e4c6d2a907 -> c1f6a8d3e902` validada em banco PostgreSQL descartável e aplicada no banco local. As preferências e cobranças existentes foram preservadas.
- Testes de autorização, fila, datas, proteção entre canais, conciliação, estorno, recibos idempotentes, permissões, e-mails preparados e bloqueio de escrita.
- Testes com sessões PostgreSQL concorrentes para criar uma autorização, enviar uma única instrução e cancelar após envio concorrente, sempre com transporte bancário substituído.
- Testes de interface para escolher Pix na mesma lista de formas de cobrança, exibir QR, bloquear mutações de produção e evitar reenvio automático.

A regressão selecionada identificou duas falhas já reproduzidas no commit anterior `4cb3b55`: `TestListBillings.test_operational_can_list` espera permissão de operacional que a API já recusava, e `TestBoletoUnico.test_boleto_unico_inclui_mensalidade_prorata_taxa_e_servico` ainda espera duas páginas depois do ajuste anterior para uma página. Não foram alterados esses contratos ou o PDF nesta implementação.

O build isolado do frontend compilou e gerou as páginas. A cópia do standalone no Windows apresentou aviso de permissão para symlink; por isso o build foi repetido em diretório temporário Linux dentro do container frontend, concluindo também o empacotamento sem esse erro. Nenhum desses builds interferiu no diretório do servidor de desenvolvimento. Foram utilizadas as dependências já instaladas, incluindo Next 15.5.22; o build com instalação limpa das versões do lockfile deve integrar a preparação da publicação.

Resultados finais: 47 testes específicos de serviço/transporte Pix aprovados; 375 testes aprovados na seleção de regressão do backend, com as duas falhas anteriores identificadas e separadas; cinco testes de migração/concorrência aprovados com SQLite e PostgreSQL descartável; 28 testes selecionados de interface aprovados, além da checagem de tipos. A verificação dos arquivos novos/alterados não encontrou as credenciais ou o segredo de webhook.

No ambiente local foram mantidos `SICREDI_PIX_ESCRITA_HABILITADA=false`, `SICREDI_PIX_PRODUCAO_CONFIRMADA=false` e `RUN_BACKGROUND_JOBS=false`. Nenhuma autorização, cobrança ou agendamento real foi criado. Nenhum cliente foi habilitado automaticamente e a VPS não foi alterada.

Ainda falta validar ponta a ponta no HML: escopos de escrita, criação do QR oficial, aprovação/recusa pelo pagador, registro e cancelamento de instruções, liquidação e entrega dos callbacks. Os testes com transporte controlado não substituem essa homologação bancária. A integração permanece bloqueada para novas operações reais até essa liberação.

## Publicação na VPS

A publicação autorizada em 05/11/2026 mantém os controles de escrita e confirmação de produção em `false`. A opção aparece no cadastro, mas o deploy não cria autorizações, não agenda débitos e não emite boletos existentes. Os jobs dos canais anteriores preservam sua configuração.

Antes da migração, guardar uma cópia privada do `.env`, um dump PostgreSQL verificável e a identificação das imagens anteriores. Fazer build das imagens com as dependências do lockfile, aplicar `alembic upgrade head` e recriar backend, worker e frontend. Conferir a revisão `c1f6a8d3e902`, a saúde HTTP e os controles efetivos nos containers.

O certificado, a cadeia e a chave correspondentes ficam fora do repositório, em um subdiretório do volume de certificados já usado por backend e worker, com leitura restrita ao usuário da aplicação. Configurar os caminhos `/certs/sicredi-pix/…` no `.env` privado da VPS. Não cadastrar os webhooks nem liberar escrita bancária como efeito do deploy. O retorno à imagem anterior não exige remover as tabelas novas; preservar o histórico e evitar downgrade destrutivo.
