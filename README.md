# Sistema de Gestão para Rastreamento Veicular

Starter full-stack com a stack recomendada:
- Frontend: Next.js 15 + Tailwind + componentes base estilo shadcn/ui
- Backend: FastAPI + SQLAlchemy + Pydantic
- Banco: PostgreSQL
- Infra: Docker Compose + Redis + MinIO

## Módulos incluídos nesta versão
- Autenticação com JWT e refresh token
- Home com acesso separado para **Login ADM**, **Login Cliente** e **Cadastro do Cliente**
- Cadastro completo do cliente com validação de CPF/CNPJ, telefone, CEP, UF e força de senha
- Recuperação de senha com geração de token e redefinição de senha
- Dashboard administrativo inicial
- Dashboard do cliente com dados cadastrais, veículos vinculados e cobranças recentes
- CRUD base de usuários, clientes, veículos, rastreadores, ordens de serviço, planos, contratos e cobranças
- Estrutura preparada para upload de documentos e geração de PDF
- Seed com usuário administrador inicial

## Usuário inicial
- E-mail: `admin@rastreamento.local`
- Senha: `Admin@123`

## Fluxo do cliente
1. Acesse `http://localhost:3000`
2. Clique em **Cadastrar Cliente**
3. Conclua o cadastro
4. O sistema já autentica o cliente e redireciona para `/cliente/dashboard`

## Recuperação de senha
No ambiente de desenvolvimento, a API retorna o token de redefinição na resposta para facilitar os testes.
Em produção, defina `DEBUG_RETURN_RESET_TOKEN=false` e conecte esse fluxo a um serviço de e-mail.

## Subindo o projeto
Como esta versão adiciona novas tabelas e campos, rode com limpeza de volume para recriar o banco:

```bash
docker compose down -v
docker compose build --no-cache
docker compose up
```

## URLs
- Frontend: http://localhost:3000
- Backend: http://localhost:8000
- Docs da API: http://localhost:8000/docs
- MinIO Console: http://localhost:9001

## Correção de compatibilidade do bcrypt
Este projeto usa `passlib==1.7.4`, que pode falhar com versões novas do `bcrypt`.
O backend foi ajustado para fixar `bcrypt==4.0.1`, que é compatível.

## Observação
O login inicial usa `admin@rastreamento.local`. Para permitir esse endereço no login, o backend aceita e-mail como string simples no endpoint de autenticação.
