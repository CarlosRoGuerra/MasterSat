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


## Rebuild obrigatório após atualizar dependências
Sempre rode `docker compose build --no-cache backend` quando houver mudança em `backend/requirements.txt`, especialmente para a integração Multiportal, que depende de `requests` e `zeep`.
