# Carnê Sicredi no fechamento

Ao selecionar **Carnê Sicredi** no fechamento, a tela oferece a quantidade de
parcelas, inicialmente 12. A prévia mostra cada vencimento e parcela separadamente.
Serviços de novembro/2026 iniciam os vencimentos em dezembro/2026; um carnê de
12 parcelas segue até novembro/2027, dentro da vigência do contrato.

A mesma quantidade acompanha a simulação, os relatórios PDF/Excel e a geração,
por meio do parâmetro opcional `carne_installments` (2 a 60). A API exige o filtro
`forma_cobranca=carne_sicredi` para essa operação. Sem esse parâmetro, o fechamento
mensal permanece com o comportamento anterior.

Meses já ocupados por mensalidades ou carnês são mostrados como gerados e não
recebem uma segunda cobrança. A competência é revalidada sob trava do contrato.
Pró-rata inicial e serviços iniciais entram somente na primeira parcela; o
pró-rata final de uma desinstalação não cria parcelas futuras.

Para boleto único, contratos do mesmo pagador são consolidados separadamente em
cada mês. Taxas e serviços deste fechamento acompanham o primeiro mês, sem somar
todo o carnê em um boleto único. O lote guarda todos os títulos efetivos e a
conferência abre com Sicredi selecionado. Preparar o fechamento não registra
boletos no banco: a emissão continua sendo uma ação explícita da equipe.

A conferência do lote oferece **Emitir carnê pelo Sicredi (12 boletos)** por
responsável. Essa ação usa o mesmo acompanhamento de carnê do Financeiro e da
Ailos: registra cada parcela separadamente, mostra o progresso dos 12 títulos
e disponibiliza o carnê com três parcelas pagáveis por folha (quatro folhas
para 12 parcelas). Não soma os 12 meses em um título. A seleção de linhas para
envio por e-mail não reduz a quantidade de parcelas do carnê.

Para títulos que já foram emitidos individualmente pelo fechamento, **Montar
carnê** reaproveita os registros e os nossos números existentes, sem novo
registro bancário. O lote fica disponível para acompanhar e baixar novamente
no fechamento e na lista de carnês do Financeiro. Abrir a conferência e baixar
o PDF não emitem nem cancelam boletos.

No parcelamento pelo plano, o primeiro vencimento automático de um contrato
mensal novo respeita o mês seguinte ao início do serviço. Um primeiro
vencimento explicitamente informado continua sendo respeitado. A troca de
cliente no gerador de carnê limpa o vencimento digitado para o cliente anterior.

Verificação: 183 testes de backend e 24 de interface aprovados; TypeScript
aprovado. Um teste antigo de PDF que exige duas páginas foi excluído dessa
execução: a falha de expectativa (PDF atual em uma página) já foi reproduzida
no código anterior e não foi alterada nesta correção.
