# Carnê do plano com vários veículos

Em Financeiro > Gerar carnê > **A partir do plano**, a lista de contratos virou
uma lista de veículos com marcação: dá para escolher um, alguns ou **Selecionar
todos os veículos ativos** do cliente.

- Com **um veículo**, nada muda: cada parcela vale o plano (ou o valor
  informado).
- Com **vários veículos**, cada mês vira **um boleto só com todas as placas**,
  no valor da soma dos planos. Exemplo: RMO0H18 + RDU2G08, 2 × R$ 64,99 =
  **R$ 129,98** por parcela, 12 parcelas. O valor da parcela fica somente
  leitura (soma dos planos).

Por dentro, cada veículo continua com a mensalidade do mês registrada no
próprio contrato (substituída pelo boleto do mês), igual ao boleto único do
fechamento. Assim o fechamento mensal não cobra de novo esses meses em nenhum
dos veículos, e o boleto/carnê mostra as placas:

- no canhoto do carnê, o campo **Placa** mostra todas as placas (antes mostrava
  o começo da descrição do serviço, que cortava a placa);
- nas instruções, "Referente a: carnê MM/AAAA, parcela N/12" e
  "Placas: RMO0H18, RDU2G08".

Regras:

- Todos os veículos precisam ter o **mesmo responsável financeiro**.
- Se algum mês já estiver cobrado em qualquer um dos veículos, o carnê inteiro
  é recusado, com a placa e os meses na mensagem. Meses presos só por
  cobranças canceladas podem ser liberados na confirmação, como antes.
- O carnê segue o mesmo fluxo de registro (Sicredi ou Ailos), acompanhamento e
  PDF de três parcelas por folha.
