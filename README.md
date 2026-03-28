# Sistema de Rastreamento Veicular

Projeto base full-stack com:
- Frontend: Next.js + Tailwind
- Backend: FastAPI + SQLAlchemy
- Banco: PostgreSQL
- Redis
- MinIO

## Credenciais iniciais
- Admin: `admin@rastreamento.local`
- Senha: `Admin@123`

## Novidades desta versão
- integração real com MinIO no backend
- criação automática do bucket no startup
- upload/listagem/remoção de documentos do veículo
- portal do cliente com cadastro de veículos próprio
- upload de documentos do cliente e do veículo para validação
- edição de perfil do cliente
- suporte a múltiplos e-mails para cliente do tipo PJ
- CRUD administrativo de veículos com filtros
- tela administrativa de clientes exibindo e-mails extras para PJ

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

## Observação
Como houve mudança de schema, use `docker compose down -v` antes de subir para recriar o banco local.


## Ajuste do MinIO no navegador

As URLs assinadas dos arquivos usam o host definido em `MINIO_PUBLIC_URL`.
Em ambiente local, mantenha `MINIO_PUBLIC_URL=http://localhost:9000`.
Em produção, troque para o domínio público do MinIO/proxy.


## Ajustes desta entrega
- portal do cliente mantido como consulta de veículos + gestão de perfil/documentos do cliente
- cadastro de veículos restrito ao administrativo
- filtros server-side em clientes
- criação/sincronização automática do usuário cliente quando o administrativo informa e-mail no cadastro
- abertura e download de documentos pelo backend
- proteção de rota no frontend para separar admin e cliente
- navegação administrativa focada apenas nos módulos já fechados neste sprint
