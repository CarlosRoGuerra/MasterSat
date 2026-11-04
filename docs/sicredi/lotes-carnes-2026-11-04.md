# Lotes e carnês Sicredi - 04/11/2026

A escolha de banco foi estendida à emissão dos boletos selecionados no
Financeiro e aos carnês de cobranças existentes ou do plano, incluindo a
central do cliente e o cadastro de veículo. Ailos continua como padrão.
Sicredi aparece quando sua configuração está disponível. O cadastro e os
filtros aceitam a forma de cobrança Carnê Sicredi.

Os carnês Sicredi reservam todas as parcelas em uma fila persistente antes
de retornar ao navegador. As reservas impedem manutenção financeira e
emissão em outro canal. O processamento usa o mesmo registro individual,
salva o resultado de cada parcela e permite retomar apenas as faltantes.
Registros sem resposta são consultados antes de permitir outro envio.
Uma trava PostgreSQL por lote impede processadores simultâneos; as travas
das cobranças não ficam retidas durante chamadas HTTP.

O acompanhamento preserva banco e identidade de cada operação; respostas
atrasadas de outro carnê são descartadas. Falhas parciais oferecem ações
manuais e download parcial explícito. Parcelas pagas que já estavam
registradas continuam confirmadas; o PDF disponibiliza as parcelas abertas
sem exigir que o operador registre ou imprima novamente uma parcela paga.

O PDF mantém o modelo compacto MasterSat, três parcelas por A4, com
identificação Sicredi, I2of5 de 103 x 13 mm e o Pix oficial de cada parcela
híbrida. Nenhum download ou GET de acompanhamento emite títulos no Sicredi.

## Validação local

- Regressão bancária, financeira, CNAB e PDFs: 515 testes aprovados;
  dez cenários condicionais ignorados nesse comando.
- Concorrência real em schemas temporários do PostgreSQL local: dois
  testes aprovados (criação concorrente e dois processadores do mesmo lote).
- Após a revisão de parcelas pagas: 21 testes do módulo de lotes aprovados.
- Frontend: 66 testes focados aprovados; após a revisão de mensagens de
  confirmação, os oito testes do acompanhamento passaram. Typecheck e
  ESLint dos arquivos alterados passaram, com três avisos anteriores no
  Financeiro.
- Build de produção Linux, incluindo imagem standalone: aprovado no Docker
  local, com Next.js 15.5.26 do lockfile.
- Prévia sintética com três parcelas em uma folha e doze em quatro folhas:
  revisão visual e leitura dos quinze códigos I2of5 e quinze QR Pix.
  Esses arquivos demonstram a impressão, sem registro ou validade financeira.
- Migrations aplicadas no PostgreSQL local até a única revisão final
  `a6e2d9c51b74`.

A validação usa respostas bancárias falsas nos testes. Nenhum título foi
emitido na API bancária, nenhuma chave foi alterada e a VPS não foi
modificada. O ambiente local continua em sandbox, com tarefas automáticas
desativadas. A validação da impressão não substitui a homologação pelo banco.

## Atualização posterior

Aplicar as migrations com o processo normal de inicialização/Alembic e
reconstruir backend, worker e frontend. As revisões novas são
`f4c8b2a63d10` (lotes e reservas) e `a6e2d9c51b74` (forma Carnê Sicredi),
após `e3b9d5a71c42`. Não é necessário recadastrar cobranças ou boletos já
registrados. A ativação de produção usa as configurações documentadas em
[homologacao.md](homologacao.md).
