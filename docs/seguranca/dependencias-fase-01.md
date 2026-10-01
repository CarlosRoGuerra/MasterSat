# Atualização de dependências — Fase 01 (DEP-01 HTTP/PDF/JWT, FE-05)

Scanners executados na imagem final: `pip-audit` na imagem do `backend/Dockerfile`
e `npm audit` com o lockfile. JSONs em `docs/validacao/fase-01/`.

## Backend (`backend/requirements.txt`)

| Pacote | Antes | Depois | Onde o advisory é alcançável |
|---|---|---|---|
| fastapi / starlette | 0.115.0 / 0.38.6 | 0.142.2 / 1.7.0 | O FastAPI lê o corpo multipart **antes** de resolver as dependências de autenticação, então os uploads (documentos, certificado, contrato) são parseados até para requisição anônima. `request.url` é reconstruído nos redirects de barra final. StaticFiles e HTTPEndpoint não são usados |
| python-multipart | 0.0.9 | 0.0.32 | Mesmo caminho dos uploads (DoS por preâmbulo, cabeçalhos de parte e Content-Length negativo). Os advisories de `UPLOAD_DIR` não se aplicam: essa opção não é usada |
| pypdf | 6.14.2 | 6.19.0 | `services/contract_check.py` extrai texto do PDF de contrato enviado (usuário autenticado com perfil de edição): CPU e memória com fontes, XForms e outlines forjados |
| python-jose (+ ecdsa) | 3.3.0 | **removido**, substituído por PyJWT 2.15.1 | Na prática não era alcançável: HS256 fixo, sem JWE, sem chave assimétrica. A troca elimina o `ecdsa`, cujo advisory (Minerva) não tem correção |
| requests | 2.32.3 | 2.33.0 | Chamadas Ailos/SGR/webhook para URLs de configuração. O vazamento de `.netrc` exige URL controlada por atacante, o que não foi encontrado |
| pip (imagem) | 25.0.1 | 26.2.1 | Só atua no build |

Resultado do `pip-audit` na imagem: **0 achados** para HTTP/PDF/JWT e pip.
Continuam os achados de `cryptography 41.0.7`, `pyOpenSSL 23.3.0`,
`signxml 3.2.2`, `lxml 5.3.0` e `zeep 4.3.1`. Essa pilha de assinatura XMLDSig
está travada em conjunto (ver comentário em `requirements.txt`) e fica para a
**Fase 05**, que precisa validar a assinatura de DPS/RPS com certificado
sintético. Nada foi alterado nela aqui.

### Compatibilidade

- **JWT:** mesmo algoritmo (HS256) e mesmo formato. Tokens emitidos pelo
  python-jose continuam válidos. A decodificação está centralizada em
  `core/security.decode_token`. O PyJWT recusaria `iat` no futuro; essa
  checagem foi desligada (paridade com o jose) para que um ajuste de relógio do
  servidor não derrube sessões. Assinatura, algoritmo e `exp` continuam
  validados.
- **OpenAPI:** mesmas 190 operações e nenhum schema removido. O que muda é só a
  notação: upload passa de `format: binary` para `contentMediaType` (JSON
  Schema 2020-12), e `ValidationError` ganhou campos. O contrato HTTP não mudou.
  `backend/openapi.json` não foi regenerado nesta fase.
- **Startup:** o FastAPI 0.142 ainda executa `@app.on_event('startup')`, com
  aviso de depreciação. A migração para `lifespan` fica como pendência: se um
  FastAPI futuro remover `on_event`, as migrations e os workers param de subir.
  Verificado subindo a imagem real com PostgreSQL, Redis e MinIO descartáveis.
- **Suíte:** 2033 testes; só as 38 falhas triadas da Fase 00.
- **Avisos novos nos testes:** `InsecureKeyLengthWarning` (a chave de teste tem
  30 bytes; em produção `SECRET_KEY` < 32 já impede o boot) e
  `StarletteDeprecationWarning` sobre `httpx` no `TestClient` (ferramenta de
  teste).

### Correção operacional encontrada no boot

O `alembic/env.py` chamava `fileConfig()` com `disable_existing_loggers=True`
(o padrão). Como as migrations rodam no startup, **depois do boot a aplicação
não registrava mais nada via logging**: access log, "Application startup
complete", alertas críticos do keepalive Ailos e os avisos novos desta fase.
Corrigido com `disable_existing_loggers=False`.

## Frontend (`frontend/package.json`, `package-lock.json`)

| Pacote | Antes | Depois |
|---|---|---|
| next | 15.5.22 | 15.5.26 (mesma major; React 18.3.1 inalterado) |
| eslint-config-next | ^15.5.22 | ^15.5.26 |
| sharp (opcional do next) | 0.34.5 | 0.35.5 |
| postcss dentro do next | 8.4.31 (fixado pelo Next) | 8.5.28 via `overrides` |
| undici, browserslist, baseline-browser-mapping, postcss-selector-parser, brace-expansion, @redocly/openapi-core (js-yaml) | afetados | atualizados dentro das faixas (`npm update`) |

`npm audit`: **10 → 0** (1 crítico, 7 altos, 1 moderado e 1 baixo antes). Não
foi usado `npm audit fix --force`.

**Override `next → postcss "$postcss"`:** o Next 15 fixa `postcss 8.4.31`, e a
correção oficial só existe no Next 16 (major, fora do escopo). O postcss é
usado só no build, com o CSS do próprio projeto, então os advisories (XSS no
stringify, leitura de `.map` via `sourceMappingURL`) não são alcançáveis aqui.
Mesmo assim o override foi aplicado, e **o CSS de produção gerado com e sem o
override é byte-idêntico** (mesmo SHA-256). Remova o override quando migrar
para o Next 16.

**Alcance dos advisories do Next:**

- GHSA-p293-qw3h-jr36 (RCE com hospedagem em Windows): não se aplica ao deploy
  em `node:20-alpine`;
- GHSA-2xp9-vwfh-vxw4 (otimização de AVIF): o `next.config` não define
  `images.remotePatterns` e o `next/image` só carrega logos estáticos.

Os dois ficam corrigidos pela 15.5.26 de qualquer forma.

Validado em `node:20-alpine` (Node 20.20.2 / npm 10.8.2): `npm ci`, lint (0
erros, 41 avisos, igual à baseline), typecheck, 172 testes e build. A imagem de
produção (`target runner`) foi construída a partir do commit.
