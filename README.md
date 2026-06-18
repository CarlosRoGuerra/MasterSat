# Sistema de Rastreamento Veicular

Projeto full-stack com:
- Frontend: Next.js + Tailwind
- Backend: FastAPI + SQLAlchemy
- Banco: PostgreSQL
- Redis
- MinIO

## Credenciais iniciais
- Admin: `admin@rastreamento.local`
- Senha: `Admin@123`

## O que está fechado nesta versão
- home, logins e dashboards com visual revisado
- separação entre **portal do cliente** e **painel administrativo**
- cadastro e edição de **clientes PF/PJ** com CEP automático
- suporte a **múltiplos e-mails** para clientes PJ
- sincronização automática da conta de acesso do cliente a partir do cadastro administrativo
- cadastro e edição administrativa de **veículos** no padrão visual definido
- documentos do cliente e do veículo armazenados no **MinIO**
- visualização e download de documentos **via backend**
- análise administrativa de documentos com status e observações
- permissões ajustadas:
  - **admin / operacional**: leitura e edição de clientes, veículos, documentos e rastreadores
  - **financeiro**: leitura somente
  - **cliente**: acesso apenas ao próprio portal

## Sprint 2 entregue
- módulo administrativo de **rastreadores**
- cadastro, edição e exclusão lógica de rastreadores
- vínculo de rastreador com **cliente e veículo**
- validação para impedir mais de um rastreador ativo no mesmo veículo
- campos técnicos do rastreador:
  - IMEI / ID
  - número de série
  - marca / modelo
  - status do equipamento
  - chip / ICCID / operadora / status do chip
  - datas de aquisição, instalação e garantia
  - observações técnicas
- **histórico de vínculos** e eventos do rastreador
- exibição do rastreador atual dentro do **portal do cliente**

## Como subir
```bash
docker compose down -v
docker compose build --no-cache
docker compose up
```

## URLs
- Frontend: http://localhost:3000
- Backend: http://localhost:8000
- Swagger: http://localhost:8000/docs
- MinIO Console: http://localhost:9001

## Observações
- Como houve mudança de schema, use `docker compose down -v` antes de subir para recriar o banco local.
- Os arquivos são servidos pelo backend, então o portal não depende de abrir URL direta do MinIO no navegador.
- Em ambiente local, mantenha `MINIO_ENDPOINT=minio:9000` e `MINIO_PUBLIC_URL=http://localhost:9000` no `.env` / `.env.example`.
- O backend desta versão foi validado com `python -m compileall`.


## Integração Multiportal
- camada de integração com o Web Service **IntegracaoAdmService** da Multiportal
- rotas administrativas para:
  - consultar fabricantes externos
  - sincronizar cliente
  - sincronizar veículo
  - sincronizar equipamento
  - executar o fluxo completo cliente → veículo → equipamento → vínculos
- logs de integração persistidos em banco
- novos campos técnicos/comerciais do rastreador:
  - fabricante externo da Multiportal
  - firmware
  - IP e porta
  - local da instalação
  - tipo de chip / equipamento / comunicação
  - plano do serviço e valor da instalação
  - status e retorno da última sincronização
- nova tela administrativa **Integração** e botão de sincronização no módulo de rastreadores

## Variáveis de ambiente da integração
- `MULTIPORTAL_ENABLED`
- `MULTIPORTAL_WSDL_URL`
- `MULTIPORTAL_ID`
- `MULTIPORTAL_PASSWORD`
- `MULTIPORTAL_GROUP_CODES`
- `MULTIPORTAL_SEND_WELCOME_EMAIL`


## Integração Ailos (Cobrança API)
Integração direta com a **API REST de Cobrança Bancária da Ailos**
(autenticação OAuth2 via APIM/WSO2 + token de cooperado), que se soma ao
fluxo CNAB já existente (`boleto_ailos.py`/`cnab400.py`/`cnab240.py`,
expostos em `/api/v1/boletos/*`) — **esse fluxo continua funcionando
normalmente** e não foi alterado.

A integração é **singleton em nível de empresa**: a MasterSat é o único
"cooperado" (1 convênio, 1 conjunto de credenciais/tokens). O modelo
`Client` continua representando os clientes da MasterSat (= "pagadores" na
Ailos), sem nenhuma configuração Ailos por cliente. Esta entrega é
**backend only** — não há tela de admin no frontend; o endpoint `/connect`
retorna a URL de autorização como JSON para um admin abrir manualmente.

Capacidades:
- emissão de boletos via API (síncrono, em lote e carnê)
- consulta de boletos e de status de lote
- cadastro/consulta/listagem de pagadores
- solicitação, listagem e download (MinIO) de arquivos de retorno
- override automático dos dados oficiais (linha digitável/código de
  barras/nosso número) no PDF do boleto CNAB existente, quando o boleto
  também foi gerado via API Ailos

### Variáveis de ambiente
Ver `.env.example`, seção **"Integração Ailos (Cobrança Bancária API)"**:
`AILOS_ENV`, `AILOS_APIM_BASE_URL`, `AILOS_GATEWAY_BASE_URL`,
`AILOS_CLIENT_ID`, `AILOS_CLIENT_SECRET`, `AILOS_DEVELOPER_KEY`,
`AILOS_CALLBACK_URL`, `AILOS_TIMEOUT_SECONDS`, `AILOS_NUMERO_CONVENIO`,
`AILOS_DEFAULT_CARTEIRA`, `AILOS_DEFAULT_FORMA_EMISSAO`,
`AILOS_DEFAULT_INDICADOR_REGISTRO_NUCLEA`, `AILOS_TOKEN_ENCRYPTION_KEY`
(gerar com `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).

**Sem credenciais preenchidas, todas as rotas `/api/v1/ailos/*` (exceto
`/status`) retornam 400 explicativo**, e o fluxo CNAB existente continua
funcionando normalmente.

### Configuração inicial (sandbox)
1. Preencha as variáveis `AILOS_*` no `.env` com as credenciais de sandbox
   fornecidas pela Ailos no onboarding e suba o backend.
2. Como ADMIN, chame `POST /api/v1/ailos/connect` → retorna `{login_url,
   state}`.
3. Abra a `login_url` no navegador e autorize o cooperado. A Ailos chama
   `POST /api/v1/ailos/callback` automaticamente (rota pública).
4. Confirme `GET /api/v1/ailos/status` → `cooperado_status: "authorized"`.
5. Teste a emissão: `POST /api/v1/ailos/boletos {"billing_id": <id>}`.

### Endpoints (`/api/v1/ailos/*`, papéis ADMIN/FINANCIAL salvo indicação)
- `GET  /status` — status da integração (nunca expõe tokens)
- `POST /connect` (ADMIN) — inicia autorização do cooperado
- `POST /callback` (público) — recebido da Ailos
- `POST /boletos`, `POST /boletos/lote`, `POST /carne/lote`
- `GET  /boletos/{numero_boleto}`, `GET /lotes/{ticket}`
- `POST /pagadores`, `PUT /pagadores`, `GET /pagadores/{numero_inscricao}`, `GET /pagadores`
- `POST /retorno/solicitar`, `GET /retorno/listar`, `GET /retorno/baixar/{ticket}`

## Homologação Ailos
Após configurar o sandbox e autorizar o cooperado (`/connect` →
`/callback` → `GET /status` com `cooperado_status: "authorized"`), gere os
3 boletos de teste exigidos pela Ailos:

```bash
cd backend
python scripts/ailos_homologacao.py
```

O script cria/atualiza 3 registros de teste (PF, PJ e PF com endereço
mínimo, marcados `notes='[HOMOLOGACAO AILOS]'`), gera os boletos via API e
salva os PDFs em `backend/homologacao_pdfs/`. Envie os 3 PDFs para
**homologacaocobranca@ailos.coop.br** (suporte: **(047) 3231-4196**) para
aprovação do layout. Após aprovação, troque `AILOS_ENV=production` e use as
credenciais de produção.


## Rebuild obrigatório após atualizar dependências
Sempre rode `docker compose build --no-cache backend` quando houver mudança em `backend/requirements.txt`, especialmente para a integração Multiportal, que depende de `requests` e `zeep`.

## Deploy em produção e segurança
Ver [docs/deploy-producao.md](docs/deploy-producao.md) — DNS/Cloudflare, `deploy.sh`, hardening da VPS (`scripts/harden-vps.sh`) e checklist de resposta a incidente.
