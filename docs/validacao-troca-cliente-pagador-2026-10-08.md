# Validação da troca de cliente e pagador — 08/10/2026

Base anterior às alterações: `a1b4befe2cdafe29577d70ba7c629f7d9a788516`.

## Resultado

| Verificação | Resultado |
| --- | --- |
| Backend em container isolado, sem rede | 564 testes passaram; 6 testes legados deselecionados após reprodução da falha na base anterior |
| Python / FastAPI / Pydantic / SQLAlchemy | 3.12.14 / 0.142.2 / 2.9.2 / 2.0.35, versões da imagem local do backend |
| Frontend: novo menu, assistente e cliente HTTP | 22 testes passaram |
| TypeScript (`tsc --noEmit --incremental false`) | Sem erros |
| ESLint nos arquivos da interface envolvidos | Sem erros; 14 avisos existentes nos containers das páginas |
| Preservação dos arquivos já alterados pelo usuário | 13 arquivos conferidos por SHA-256, sem alterações desta tarefa |

O container recebeu uma cópia da base anterior com apenas os arquivos Python desta tarefa sobrepostos. As dependências e o código ficaram em mounts de leitura; o banco usado foi SQLite em memória. Nenhuma conexão com a VPS, banco de produção, Ailos ou Multiportal participou dos testes.

## Casos cobertos

- Venda de uma placa entre duas do mesmo cliente: apenas o veículo escolhido muda, seu rastreador continua instalado e seu contrato mantém plano, início e vencimento.
- Prévia de fechamento: a placa transferida passa ao novo responsável; a outra permanece no cliente anterior.
- Troca só do interveniente, inclusive pelo financeiro, preservando o proprietário do veículo.
- Reparo do caso em que o veículo já mudou de cliente, mas o rastreador e o contrato ainda apontam para o anterior.
- Edição comum do veículo usando a mesma troca atômica.
- Múltiplos rastreadores e contratos ativos na mesma placa.
- Cobranças pagas, pendentes e canceladas preservadas; snapshot legado do pagador congelado antes da troca.
- Consulta de cobrança paga pela API mantendo o pagador anterior; interveniente legado removido mantém o fallback anterior para o cliente da cobrança.
- Serviços ativos acompanham o contrato nas parcelas futuras; a parcela já gerada permanece no cliente anterior.
- IDs removidos/inexistentes, tela desatualizada, autorização por papel e contrato com rastreador de outra placa.
- Busca de rastreador por placa completa, parcial, minúscula, com espaço ou hífen; placa de veículo removido não aparece.
- Menu sem consultas externas, pagador atual carregado localmente, escolha obrigatória quando os contratos têm pagadores diferentes e recusa do backend exibida na interface.

## Falhas preexistentes confirmadas

Os seis testes abaixo falham da mesma forma em uma cópia de `a1b4bef`, sem as alterações desta tarefa. Esperam emissão automática na vinculação/criação ou a antiga rota de geração, comportamentos ausentes nessa base. Foram deselecionados na execução final em Python 3.12; os demais testes das respectivas classes e módulos foram executados.

- `test_trackers_api.py::TestLinkVeiculo::test_link_with_plan_generates_billings`
- `test_trackers_api.py::TestLinkVeiculo::test_link_sem_prorata_gera_exato`
- `test_contracts_api.py::TestCreateContrato::test_generates_billings_by_default`
- `test_contracts_api.py::TestCreateContrato::test_billing_cycles_out_of_range_422`
- `test_contracts_api.py::TestGenerateBillings::test_generates_billings`
- `test_contracts_api.py::TestGenerateBillings::test_default_12_months`

O schema da nova operação foi extraído com as versões da imagem do backend. Apenas o novo endpoint, seu corpo e sua operação foram acrescentados aos tipos do frontend, preservando os tipos existentes das outras funcionalidades.
