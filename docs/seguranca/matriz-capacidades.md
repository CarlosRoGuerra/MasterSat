# Matriz de capacidades

Fonte única no código: `backend/app/core/permissions.py`. A autorização é sempre
feita no backend; o frontend (`frontend/lib/route-roles.ts`) só esconde menus e
botões.

## Capacidades

| Capacidade | admin | operacional | financeiro | cliente | O que protege |
|---|---|---|---|---|---|
| `REGISTRY_READ` | sim | sim | sim | não | Cadastro e campo: clientes, veículos, rastreadores, OS, documentos |
| `FINANCIAL_READ` | sim | **não** | sim | não | Valores, títulos, pagamentos, contratos, montantes em atraso |
| `AUDIT_READ` | sim | não | não | não | Trilha de auditoria |

Uma capacidade nomeia o **dado**, não a tela. Todo canal que entrega o mesmo
dado (JSON, CSV, XLSX, PDF) consulta a mesma capacidade.

## Canais que mudaram nesta fase (SEC-01)

| Canal | Antes | Agora, sem `FINANCIAL_READ` |
|---|---|---|
| `GET /exports/billings` (CSV/XLSX) | 200 com os títulos | 403 |
| `GET /exports/billings-report` (CSV/XLSX/PDF) | 200 | 403 |
| `GET /exports/delinquents` (CSV/XLSX/PDF) | 200 | 403 |
| `GET /clients/{id}/timeline-pdf` | 200 com contratos e cobranças | 200 sem essas seções, com aviso de omissão |
| `GET /dashboard/` | 200 com receita e vencimentos | 200 com `finance: null` e `upcoming_billings: []` |
| `GET /delinquency/status` | 200 com quantidade e valor vencidos | 200 com `cobrancas_vencidas: null` e `valor_total_vencido: null` |

Continuam iguais para o operacional: exports de clientes, veículos e
rastreadores; timeline JSON (que já filtrava); contagem de clientes
inadimplentes (dado de status do cliente, usado para bloqueio e desinstalação).

Admin e financeiro recebem exatamente o que recebiam antes. As chaves `finance`
e `valor_total_vencido` continuam no payload, então consumidores que só leem
para esses perfis não mudam.

## Decisão registrada para a empresa

**Proposta aplicada:** o operacional não vê valores nem títulos em nenhum
canal. Ele continua vendo quantos clientes estão inadimplentes, e o PDF da linha
do tempo dele traz cadastro e OS.

**Como verificar:** `backend/tests/test_fase01_capacidades.py` monta a matriz
perfil × formato com um título sintético marcado e confirma que o marcador não
aparece em corpo, metadado nem arquivo para quem não tem a capacidade.

**Se a empresa quiser que o operacional veja algum indicador financeiro
agregado** (ex.: total vencido no dashboard), a mudança é criar uma capacidade
nova (ex.: `FINANCIAL_SUMMARY_READ`) e aplicá-la só a esse campo. Não amplie
`FINANCIAL_READ`, porque ela libera os títulos em todos os canais.

## Pendência relacionada (baseline, código R1)

Cinco testes antigos (`baseline_falhas_conhecidas.txt`, código R1) esperam que o
operacional liste cobranças, itens de cobrança, planos, produtos e extrato.
Para cobranças, itens e extrato, a matriz acima confirma o 403 atual. Planos e
produtos têm preço, mas não são títulos. Falta decidir se entram em
`FINANCIAL_READ`, e isso depende da empresa. Os testes não foram alterados
nesta fase.
