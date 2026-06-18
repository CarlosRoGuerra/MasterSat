# Deploy em produção e segurança — Mastersat

Guia para colocar (ou recolocar) o Mastersat no ar em `app.mastersat.com.br`
(frontend) e `api.mastersat.com.br` (backend), atrás do proxy da Cloudflare,
com TLS via Let's Encrypt (DNS-01) e hardening da VPS.

---

## 0. Resposta ao incidente recente (fazer ANTES do deploy)

A VPS já foi comprometida uma vez (porta exposta com credenciais padrão).
Antes de subir a versão corrigida, faça uma limpeza no servidor atual:

1. **Procure persistência deixada pelo atacante:**
   - `crontab -l` (e `sudo crontab -l -u root` e de qualquer outro usuário) —
     remova jobs desconhecidos.
   - `cat ~/.ssh/authorized_keys` e `sudo cat /root/.ssh/authorized_keys` —
     remova chaves que você não reconhece.
   - `docker ps -a` — procure containers que você não criou (ex.: mineradores).
   - `last -a` e `sudo tail -200 /var/log/auth.log` — procure logins
     suspeitos.
2. **Se encontrar qualquer sinal de acesso root pelo atacante** (chave SSH
   estranha, container desconhecido, binário em `/tmp` ou `/var/tmp`),
   o mais seguro é **recriar a VPS do zero** (nova instância) e restaurar
   só o banco de dados a partir de um backup confiável — não dá para
   confiar 100% num host que teve root comprometido.
3. **Troque TODAS as credenciais** que estavam no `.env` antigo, mesmo que
   nada pareça comprometido — elas já foram expostas:
   - `POSTGRES_PASSWORD` / `DATABASE_URL`
   - `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`
   - `SECRET_KEY` (gera novo: `python -c "import secrets; print(secrets.token_hex(32))"`)
     — trocar invalida todos os tokens JWT emitidos, forçando novo login
     (bom sinal pós-incidente).
4. **Troque a senha do admin padrão** (`admin@rastreamento.local` /
   `Admin@123`) — ela está no histórico do git, então é pública. Faça login
   e troque pela tela de usuários, ou via banco.
5. **Audite a tabela `users`** procurando contas admin que você não criou.

---

## 1. Arquitetura

```
Cliente → Cloudflare (proxy) → VPS:80/443 (nginx)
                                  ├─ app.mastersat.com.br → frontend:3000
                                  └─ api.mastersat.com.br → backend:8000
```

- Só as portas **80 e 443** ficam publicadas no host (`docker-compose.prod.yml`
  usa `ports: !reset []` para garantir que `db`, `redis`, `minio`, `backend`
  e `frontend` **não** fiquem acessíveis diretamente — confira sempre com:
  ```bash
  docker compose -f docker-compose.yml -f docker-compose.prod.yml config | grep -A2 published
  ```
  Só deve aparecer `published: "80"` e `published: "443"`.
- Certificado TLS único (SAN) cobrindo `app.mastersat.com.br` e
  `api.mastersat.com.br`, emitido via **DNS-01** (plugin
  `certbot/dns-cloudflare`) — funciona com o proxy da Cloudflare ligado e
  não precisa abrir a porta 80 para validação.
- `/docs`, `/redoc` e `/openapi.json` da API ficam bloqueados em produção.

---

## 2. Configurar DNS na Cloudflare

O DNS do `mastersat.com.br` hoje está em outro painel. Para usar o proxy da
Cloudflare (recomendado para WAF/DDoS), o domínio precisa ser **migrado**
para os nameservers da Cloudflare:

1. Crie uma conta gratuita em https://dash.cloudflare.com e clique em
   **Add a site** → digite `mastersat.com.br`.
2. A Cloudflare vai escanear e importar os registros DNS atuais
   automaticamente. **Confira com atenção** se todos os registros
   importantes vieram (principalmente **MX** e `TXT` de e-mail, se houver
   e-mail corporativo no domínio) — se faltar algum, adicione manualmente
   antes de prosseguir.
3. A Cloudflare vai mostrar **2 nameservers** (ex.: `ana.ns.cloudflare.com`,
   `bob.ns.cloudflare.com`). Vá no painel atual (Hostinger/GoDaddy/etc.) e
   troque os nameservers do domínio para esses dois.
4. Aguarde a propagação (a Cloudflare avisa por e-mail quando o domínio
   fica "Active" — geralmente minutos a poucas horas).
5. Com o domínio ativo na Cloudflare, crie os registros A:

   | Tipo | Nome | Conteúdo         | Proxy       |
   |------|------|------------------|-------------|
   | A    | app  | 69.197.174.43    | ✅ Proxied  |
   | A    | api  | 69.197.174.43    | ✅ Proxied  |

6. Em **SSL/TLS → Overview**, defina o modo como **Full (strict)** (o
   certificado válido da origem é emitido no passo de deploy, via DNS-01,
   então pode deixar em "Full (strict)" desde já).
7. Em **SSL/TLS → Edge Certificates**, ative **Always Use HTTPS**.
8. Opcional, mas recomendado (plano free):
   - **Security → Bots**: ative o "Bot Fight Mode".
   - **Security → WAF**: revise as "Managed rules" (já vêm com proteções
     básicas habilitadas no free).

### Token de API para o certbot (DNS-01)

1. Acesse https://dash.cloudflare.com/profile/api-tokens → **Create Token**.
2. Use o template **"Edit zone DNS"**.
3. Em **Zone Resources**, restrinja para **Specific zone → mastersat.com.br**
   (não use um token de conta inteira).
4. Copie o token gerado — ele vai para `CLOUDFLARE_API_TOKEN` no `.env`
   (ver próxima seção). Esse token só consegue editar registros DNS da
   zona `mastersat.com.br`, nada mais.

---

## 3. Configurar `.env` de produção

Na VPS, copie `.env.example` para `.env` e preencha:

```bash
cp .env.example .env
```

Itens **obrigatórios** a trocar (o `deploy.sh` valida vários destes e aborta
se estiverem com valor padrão):

- `SECRET_KEY` — `python -c "import secrets; print(secrets.token_hex(32))"`
- `POSTGRES_PASSWORD` e `DATABASE_URL` (mesma senha nos dois)
- `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`
- `CLOUDFLARE_API_TOKEN` — token criado no passo anterior
- `DEBUG_RETURN_RESET_TOKEN=false`
- URLs de produção (já vêm corretas no `.env.example`):
  ```
  NEXT_PUBLIC_API_URL=https://api.mastersat.com.br/api/v1
  BACKEND_PUBLIC_URL=https://api.mastersat.com.br
  FRONTEND_URL=https://app.mastersat.com.br
  ```

---

## 4. Deploy

```bash
git clone <repo> mastersat && cd mastersat
cp .env.example .env   # preencha conforme seção 3
chmod +x deploy.sh scripts/harden-vps.sh
DOMAIN=mastersat.com.br EMAIL=seu@email.com ./deploy.sh
```

O `deploy.sh`:
1. Valida as configurações de segurança do `.env`.
2. Substitui `SEU_DOMINIO.COM.BR` por `mastersat.com.br` em `nginx/nginx.conf`
   (gera `app.mastersat.com.br` / `api.mastersat.com.br`).
3. Gera `nginx/cloudflare.ini` (chmod 600, gitignored) a partir de
   `CLOUDFLARE_API_TOKEN`.
4. Sobe `db`, `redis`, `minio`, `backend`, `frontend` (sem portas publicadas).
5. Emite o certificado SSL via DNS-01 (Cloudflare).
6. Sobe `nginx` + `certbot` (renovação automática a cada 12h).
7. Agenda o backup diário do banco às 02:00.
8. Faz um teste de saúde em `https://app.mastersat.com.br/` e
   `https://api.mastersat.com.br/api/v1/auth/me`.

---

## 5. Hardening da VPS

Depois do primeiro deploy bem-sucedido (para não atrapalhar o acesso SSH
durante o setup):

```bash
sudo ./scripts/harden-vps.sh
```

Isso ativa:
- **UFW**: bloqueia tudo, exceto SSH, 80 e 443.
- **fail2ban**: bane IPs com 5 tentativas de SSH falhas em 10 min (1h de ban).
- **unattended-upgrades**: aplica patches de segurança do SO automaticamente.
- **SSH hardening**: desabilita login por senha (`PasswordAuthentication no`)
  e, se houver um usuário sudo com chave própria, desabilita login root
  (`PermitRootLogin no`).

⚠️ O script faz checagens de segurança (não desabilita nada se não houver
uma chave SSH configurada), mas **abra um novo terminal e confirme que ainda
consegue conectar** antes de fechar a sessão atual.

---

## 6. Checklist final

- [ ] DNS migrado para Cloudflare, `app` e `api` como A records "Proxied"
      apontando para `69.197.174.43`
- [ ] SSL/TLS da Cloudflare em **Full (strict)** + **Always Use HTTPS**
- [ ] `.env` com todas as senhas/`SECRET_KEY` trocadas (nenhum valor padrão)
- [ ] `docker compose ... config | grep published` mostra **só** 80 e 443
- [ ] `https://app.mastersat.com.br` carrega o frontend
- [ ] `https://api.mastersat.com.br/api/v1/auth/me` retorna `401` (sem token)
- [ ] `https://api.mastersat.com.br/docs` retorna **403/erro** (bloqueado)
- [ ] Senha do admin padrão (`admin@rastreamento.local`) trocada
- [ ] `sudo ./scripts/harden-vps.sh` executado e acesso SSH confirmado
      em uma nova sessão
- [ ] Backup diário (`crontab -l`) configurado e testado
      (`docker compose --profile backup run --rm pg_dump`)
