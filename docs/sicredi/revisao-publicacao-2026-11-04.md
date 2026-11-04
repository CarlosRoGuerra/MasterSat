# Revisão da integração Sicredi — 04/11/2026

Dois revisores independentes avaliaram o código antes da publicação: um para
emissão, autorização, conciliação, baixa e migração; outro para Financeiro,
PDF e distribuição. Ambos aprovaram a versão corrigida.

Correções encontradas na revisão:

- Nova tentativa não sobrepõe reserva ativa em nenhum dos bancos. A exceção
  legada para retomada de parcela Ailos continua restrita ao próprio canal.
- Consulta atrasada confere a geração da reserva e aplica o resultado sob
  trava da cobrança, preservando confirmações e novas reservas. Confirmação
  e falha do POST participam da mesma exclusão.
- Resposta inválida não comprova ausência do título; somente o 404 do GET
  individual confirma que ele não foi localizado.
- Consultas de registros incertos têm checkpoint e rodízio, inclusive quando
  o banco está indisponível, para que os demais registros sejam atendidos.
- A paleta de ações identifica o banco, e os botões Sicredi respeitam o
  processamento em andamento.

Verificação final: 602 testes backend aprovados e 6 testes PostgreSQL
ignorados por dependerem de ambiente específico; 6 testes frontend e
typecheck aprovados. A build isolada do frontend concluiu compilação e
geração de 24 páginas. A cópia standalone isolada em Windows apresentou
aviso de permissão na junction de node_modules. A migração Sicredi já foi
aplicada e verificada no PostgreSQL do Docker local.

Os PDFs de demonstração, incluindo 56 placas em uma página e anexo, foram
inspecionados visualmente. A leitura de QR Pix e I2of5 e as dimensões do código
de barras foram conferidas na preparação do layout.

Publicar o código não ativa o banco na VPS. Credenciais ficam no arquivo de
segredos do servidor; produção exige ambiente, convênio e confirmação
explícitos. O ambiente local permanece em sandbox. Esta revisão não emite
títulos, não altera produção e não substitui a homologação bancária.
