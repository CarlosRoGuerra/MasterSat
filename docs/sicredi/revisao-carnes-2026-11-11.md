# Revisão dos carnês Sicredi em relação à Ailos

Referência: commit `4033287`, de 21/10/2026, anterior à integração Sicredi.
Também foi conferido `00a2b14`, de 06/10/2026, que cria as parcelas do plano
individualmente, somando um mês ao vencimento de cada parcela.
Fluxo Sicredi revisado: `e8f1d18` e `fcd7245`, com a correção de ordem desta revisão.

## Comportamento comparado

| Aspecto | Ailos em outubro | Sicredi após esta correção |
| --- | --- | --- |
| Carnê de 12 mensalidades | 12 cobranças e 12 boletos, um por mês | 12 cobranças e 12 boletos, um por mês |
| Numeração | Ordem enviada ao endpoint de carnê | Ordem de vencimento; ID apenas desempata vencimentos iguais |
| Acompanhamento | Parcelas individualizadas, status e download | Mesmo fluxo visual, com status por parcela |
| PDF completo | Ficha pagável por parcela, 3 por página | Mesmo renderizador, 12 parcelas em 4 páginas, dados Sicredi |
| Registro bancário | Envio ao endpoint de carnê em lote da Ailos | Fila durável que registra cada boleto na API Sicredi |
| Fechamento | Simulação mensal | Opção explícita de carnê que prepara os meses seguintes; o fechamento mensal continua mensal |

Não são integrações bancárias idênticas. O resultado do carnê e o modelo de
impressão são equivalentes; transporte, autenticação e tratamento de retornos
continuam específicos de cada banco. A referência histórica não demonstra que
o fechamento mensal antigo criava automaticamente 12 parcelas: a criação de
parcelas a partir do plano já existia no fluxo próprio de carnê.

## Erro confirmado e corrigido

`criar_lote` usava `sorted(set(billing_ids))` tanto para evitar repetição quanto
para a ordem das parcelas. No fechamento de um contrato com serviço adicional
apenas no primeiro mês, o boleto consolidado desse mês é criado por último.
Ele recebe o maior ID e acabava impresso como parcela 12/12.

A ordenação por vencimento agora é compartilhada pela criação, processamento,
retomada de parcelas pendentes, acompanhamento, listagem e PDF dos carnês
Sicredi. Também se aplica na leitura dos carnês antigos, sem alterar seu cadastro
nem exigir novo registro bancário. A chave de idempotência e a aquisição dos
locks continuam usando IDs canônicos, preservando a reutilização do mesmo lote
quando a seleção é enviada em outra ordem. Lotes avulsos não mudam de ordem.

## Validação

Os testes reproduzem um fechamento de novembro com 12 vencimentos de dezembro
de 2026 a novembro de 2027 e um serviço de R$ 10,00 no primeiro mês. Confirmam:

- Dezembro é a parcela 1/12, por R$ 74,99; as demais são de R$ 64,99.
- Acompanhamento, listagem e PDF seguem a mesma sequência.
- O PDF tem 4 páginas com 3 parcelas por página.
- Um lote antigo gravado por ID também apresenta a sequência correta.
- Repetir a seleção reutiliza lote e números; retomar envia apenas pendentes.
- Consultar e baixar o PDF não chama o banco.

Os testes usam banco de dados isolado e respostas bancárias simuladas. Nenhum
boleto real deve ser emitido ou cancelado para validar esta correção.
