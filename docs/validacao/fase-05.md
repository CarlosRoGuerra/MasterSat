# Validação — Fase 05: NFS-e, concorrência e XMLDSig

Implementação iniciada em 30/09/2026 e concluída em 01/10/2026.
Baseline: `79f120724817613d5c61acb2c9486f33fe80ca32` (Fase 04).
Código final: `f85db354cd30a67a69123407a628273ec78e48aa`.
Suíte geral validada em `06c94c61d5999d855728cb80bfe74a527cb81231`;
a alteração posterior limita-se ao nome documentado do método SOAP de
consulta de RPS e sua regressão. A suíte fiscal integral foi repetida no SHA final.
Branch: `fase-05-nfse-concorrencia-xmldsig`, em `tmp/fase-05-worktree`.
O checkout original permaneceu na branch Fase 04 com as alterações locais
preservadas. Ele não foi utilizado para editar ou aplicar migrations.

## Achados e situação

Todos os achados ainda estavam presentes no HEAD inicial, verificados antes
das edições. Não foi revertida nenhuma mudança das Fases 01–04.

| ID | Evidência no HEAD inicial | Resultado | Situação |
|---|---|---|---|
| NFSE-01 | `recuperar_notas_orfas` marcava todo pending/processing como erro no boot | Lease/heartbeat/identidade persistidos, expiração seletiva, estado desconhecido, consulta DPS/RPS; timeout após aceite não repete POST | **Resolvido no código**; legado sem identidade requer reconciliação assistida |
| NFSE-02 | `criar_lote` reaproveitava o vencedor de UNIQUE e escrevia pending; providers não reservavam antes da rede | Reserva com releitura após conflito, atualização condicionada ao token, histórico de tentativas, trigger de monotonicidade; dois processos fizeram um único POST | **Resolvido** |
| NFSE-03 | Competência/discriminação salvas no lote não chegavam a `montar_dps` | Nacional transmite seleção histórica/exclusiva e guarda valores efetivos; provider municipal recebe os parâmetros e recusa competência histórica não suportada | **Parcial**: Nacional resolvido; extensão do leiaute municipal não homologada |
| NFSE-04 | `_material_certificado`/`_par_pem_mtls` usavam cache sem versão e cleanup só local | Cache por versão/ciphertext, leitura em cada uso, validade fora cache; PEM por chamada, renovação em dois processos e Fernet preservado | **Resolvido no código**; aceitação do certificado real depende da homologação |
| NFSE-05 | `_fechar_lote` concluía qualquer lote sem erro, inclusive processing | Conclusão exige todos autorizados; itens incertos/em processamento/ausentes não concluem | **Resolvido** |
| DEP-01 | SignXML 3.2.2, cryptography 41, pyOpenSSL 23 e lxml 5.3 | Atualização conjunta; sign/verify, mutação, XSD e mTLS sintético validados; scanner revisado | **Parcial**: homologação fiscal e advisory residual do Zeep pendentes |

Detalhes: [máquina de estados e runbook](../nfse/estado-e-operacao-fase-05.md)
e [matriz de assinatura/scanner](../seguranca/dependencias-fase-05.md).

## Isolamento e versões

PostgreSQL 16 em container `mastersat-f05-pg`, sem portas publicadas, rede
Docker `--internal mastersat-f05-test-net`. Executor com backend do worktree
montado somente leitura e sem `.env`, PFX ou credenciais da empresa.
Transporte fiscal simulado; mTLS real apenas em 127.0.0.1, com CA efêmera.
Frontend em outro container `--network none`, volume próprio de dependências.
Build e scanner usaram rede para baixar pacotes/advisories, sem importar a
aplicação ou chamar integrações. Nenhum deploy, pagamento ou emissão real.
Ao concluir, os três containers de teste, seus volumes descartáveis e a rede
foram removidos. As imagens locais de revisão e as evidências foram preservadas.

Python 3.12.14; pytest 8.3.3; SQLAlchemy 2.0.35; psycopg 3.2.1;
FastAPI 0.142.2; SignXML 5.1.0; cryptography 50.0.1; pyOpenSSL 26.4.0;
lxml 6.1.3. Versão completa do PostgreSQL em
[postgres-version.txt](fase-05/postgres-version.txt). Imagem runtime em
[runtime-image.txt](fase-05/runtime-image.txt).

## Comandos e resultados

O [executor](fase-05/validate.py) registra cwd, comandos, versões, SHA e
exit codes em [test-results.json](fase-05/test-results.json). Comandos pytest
foram executados em `/app`, equivalente ao diretório `backend/`:

```text
# Baseline 79f1207, imagem anterior, sem rede e .env sobreposto por arquivo vazio:
python -m pytest tests/test_nfse_nacional.py tests/test_nfse_fiscal.py tests/test_nfse_lote.py tests/test_nfse_certificado.py tests/test_nfse_danfse_endpoint.py -q -p no:cacheprovider

# SHA 06c94c6, incluindo todos os test_nfse*.py e multiprocesso PostgreSQL:
docker exec -e VALIDATED_SHA=06c94c61d5999d855728cb80bfe74a527cb81231 mastersat-f05-tests python /evidence/validate.py

# SHA final, repetição da suíte fiscal após correção do método SOAP:
docker exec -e VALIDATED_SHA=f85db354cd30a67a69123407a628273ec78e48aa mastersat-f05-tests python /evidence/validate.py --nfse-only

# Frontend, cwd /app (frontend):
npm run typecheck
npm test -- lib/nfse.test.ts
npx --no-install eslint lib/nfse.ts lib/nfse.test.ts app/notas-fiscais/page.tsx app/financeiro/page.tsx app/clientes/_components/nfse-modal.tsx

# Banco descartável, cwd backend:
python -m alembic upgrade head
python -m alembic check
python scripts/nfse_preflight.py

# Scanner isolado da imagem runtime:
/audit/bin/python -m pip_audit --path /usr/local/lib/python3.12/site-packages -f json
```

| Validação | Resultado |
|---|---|
| Cinco arquivos base, HEAD inicial | **127 passaram** ([saída](fase-05/baseline-tests.txt)) |
| Todos os testes NFS-e + cinco PostgreSQL Fase 05, SHA final | **192 passaram**, zero skips ([saída](fase-05/nfse-final-tests.txt), [comando/versões/SHA](fase-05/nfse-final-results.json)) |
| Suíte backend completa + comparação da baseline | **2.355 executados: 2.317 passaram, 38 falhas conhecidas, zero skips; BASELINE OK** ([resultado final](fase-05/baseline-gate.txt)) |
| Frontend TypeScript | Passou ([saída](fase-05/frontend-typecheck.txt)) |
| Ações fiscais no frontend | **7 passaram** ([saída](fase-05/frontend-tests.txt)) |
| Lint dos arquivos alterados | **0 erros, 4 warnings preexistentes** em Financeiro ([saída](fase-05/frontend-lint.txt)) |
| Alembic upgrade + check | **Sem drift** ([saída](fase-05/schema-check.txt)) |
| `pip check` runtime | **Sem dependências incompatíveis** ([saída](fase-05/pip-check.txt)) |
| pip-audit runtime | **1 advisory residual do Zeep, duplicado em duas entradas**; nenhum nos quatro pins atualizados ([JSON](fase-05/pip-audit.json)) |
| Homologação fiscal em ambiente restrito | **NÃO EXECUTADO**: exige autorização operacional e certificado real; testes sintéticos não substituem essa etapa |

A regressão antiga `test_criar_lote_races_on_nfse_nota_insert` esperava dois
sucessos, permitindo tomar a nota do vencedor. Foi atualizada para exigir
um sucesso, um erro de domínio e manutenção da propriedade da nota. Os
testes antigos de recuperação também passaram a exigir lease vencida e
desfecho desconhecido; erro reprocessável agora tem classificação explícita.
Nenhuma falha foi adicionada à lista de baseline para acomodar a fase.

## Cobertura crítica

- Dois processos `spawn`, engines/sessões independentes e POST suspenso por
  barreira: exatamente um envio; outro processo retorna processing; boot
  parcial não recupera lease ativa.
- Expiração e consulta com novo token: resposta tardia não altera autorização;
  heartbeat persiste em sessão independente.
- Dois lotes concorrentes, elegibilidade obsoleta e exceção posterior ao
  commit fiscal: nota autorizada e proprietário preservados.
- Timeout depois de aceite simulado, 409, 400 ambíguo, 5xx e corpo inválido:
  consulta, sem segundo POST. Ambiente/provedor persistidos vencem configuração
  atual na consulta e no DANFSE.
- Competência histórica/descrição exclusiva chegam à DPS; reentrada em lote
  preserva XML/token anteriores e não muda série para contornar rejeição.
- PFX válido/vencido/senha errada, validade após cache, renovação em dois
  processos; Fernet antigo e PEM de chamada em andamento preservados.
- XML real assinado/verificado com referência, namespace, C14N, UTF-8 e XSD;
  mutações de descrição/valor invalidam assinatura. Handshake mTLS sintético
  exige certificado e CA correta. Perfil municipal SHA1 não é liberado por bypass.

## Dados, rollback e entrega

A migration é aditiva e preservadora: campos de tentativa/lease/identidade,
payload efetivo e histórico JSON. Pending/processing/erro legados sem
classificação tornam-se desconhecidos, sem alterar documentos ou identificadores.
Não deduz ambiente/prestador de configuração atual. O preflight foi executado
somente no banco descartável; o volume de legado da empresa **não foi medido**.

Ensaio completo: código novo prepara/migra/recua carimbo; código `79f1207`
com imagem anterior lê e serializa as notas; guardas recusam regressão;
código novo reaplica migration. SHA-256 dos documentos permaneceu igual
nas três etapas: [rollback.json](fase-05/rollback.json),
[leitura antiga](fase-05/rollback-legacy.txt). Scripts revisáveis:
[preparação/verificação](fase-05/rollback.py) e
[consumidor antigo](fase-05/rollback_legacy.py).
O banco usado no ensaio foi removido ao final.

Commits separados: `9b77a62` testes; `2dd11de` migration/preflight;
`fd28e43` serviços/lease/certificado; `de35f1a` contratos/frontend;
`a011aa1` dependências/executores; `06c94c6` resposta municipal de duplicidade;
`f85db35` método SOAP documentado para consulta de RPS;
documentação/evidências no commit seguinte.

Riscos/limitações operacionais: identidade fiscal legada incompleta demanda
revisão assistida; Joinville histórico/SHA1 não homologados; lote antigo
reprocessado tem histórico em JSON, sem reconstrução dos itens na UI;
Zeep residual; nenhum teste de aceitação do fisco foi executado. As propostas
e os efeitos das decisões D5.1–D5.5 estão explícitos no runbook.
