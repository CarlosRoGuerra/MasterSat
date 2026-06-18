#!/usr/bin/env bash
# =============================================================================
# deploy.sh — Script de deploy completo do Mastersat em produção
#
# Execute UMA VEZ no servidor após clonar o repositório:
#   chmod +x deploy.sh && ./deploy.sh
#
# Pré-requisitos:
#   - DNS já configurado: app.SEU_DOMINIO e api.SEU_DOMINIO apontando (proxy
#     Cloudflare "laranja" ligado) para o IP desta VPS.
#   - .env configurado a partir de .env.example, incluindo CLOUDFLARE_API_TOKEN
#     (token com permissão Zone:DNS:Edit para o domínio, criado em
#     https://dash.cloudflare.com/profile/api-tokens).
#
# O que este script faz:
#   1. Valida configurações de segurança do .env
#   2. Configura o domínio no nginx.conf
#   3. Sobe os serviços de aplicação (sem nginx/certbot ainda)
#   4. Emite o certificado SSL via certbot (DNS-01 / Cloudflare)
#   5. Sobe nginx + renovação automática do certificado
#   6. Configura cron de backup automático
# =============================================================================

set -euo pipefail
DOMAIN="${DOMAIN:-}"
EMAIL="${EMAIL:-}"

log()  { echo -e "\n\033[1;34m▶ $*\033[0m"; }
ok()   { echo -e "\033[1;32m✓ $*\033[0m"; }
fail() { echo -e "\033[1;31m✗ ERRO: $*\033[0m"; exit 1; }

# ── 1. Verificações iniciais ──────────────────────────────────────────────────
log "Verificando pré-requisitos..."

command -v docker >/dev/null || fail "Docker não instalado"
command -v git    >/dev/null || fail "Git não instalado"

[ -f ".env" ] || fail ".env não encontrado — copie .env.example e configure"

[ -z "$DOMAIN" ] && { read -p "Domínio raiz (ex: mastersat.com.br): " DOMAIN; }
[ -z "$EMAIL"  ] && { read -p "E-mail para Let's Encrypt: " EMAIL; }

[ -z "$DOMAIN" ] && fail "Domínio não informado"
[ -z "$EMAIL"  ] && fail "E-mail não informado"

# ── 2. Valida segurança do .env ───────────────────────────────────────────────
log "Validando configurações de segurança..."

SECRET=$(grep '^SECRET_KEY=' .env | cut -d= -f2)
[ ${#SECRET} -lt 32 ] && fail "SECRET_KEY muito curta ou padrão — gere com: python -c \"import secrets; print(secrets.token_hex(32))\""
[[ "$SECRET" == "change-me-super-secret" ]] && fail "SECRET_KEY ainda é o valor padrão"

DEBUG_TOKEN=$(grep '^DEBUG_RETURN_RESET_TOKEN=' .env | cut -d= -f2 || echo "false")
[[ "$DEBUG_TOKEN" == "true" ]] && fail "DEBUG_RETURN_RESET_TOKEN=true — defina como false no .env"

DB_URL=$(grep '^DATABASE_URL=' .env | cut -d= -f2-)
[[ "$DB_URL" == *"postgres:postgres@"* ]] && fail "DATABASE_URL ainda usa a senha padrão do Postgres — troque antes de produção"

MINIO_PASS=$(grep '^MINIO_ROOT_PASSWORD=' .env | cut -d= -f2- || echo "")
[[ "$MINIO_PASS" == "minioadmin" || -z "$MINIO_PASS" ]] && fail "MINIO_ROOT_PASSWORD ainda é o valor padrão — troque antes de produção"

CF_TOKEN=$(grep '^CLOUDFLARE_API_TOKEN=' .env | cut -d= -f2- || echo "")
[ -z "$CF_TOKEN" ] && fail "CLOUDFLARE_API_TOKEN não definido no .env (necessário para emitir o certificado via DNS-01)"

ok "Configurações de segurança OK"

# ── 3. Configura domínio no nginx.conf ────────────────────────────────────────
log "Configurando nginx para o domínio $DOMAIN (app.$DOMAIN / api.$DOMAIN)..."
sed -i "s/SEU_DOMINIO.COM.BR/$DOMAIN/g" nginx/nginx.conf
ok "nginx.conf atualizado"

# ── 4. Credenciais da Cloudflare para o certbot (DNS-01) ─────────────────────
log "Gerando credenciais do Cloudflare para o certbot..."
cat > nginx/cloudflare.ini <<EOF
dns_cloudflare_api_token = $CF_TOKEN
EOF
chmod 600 nginx/cloudflare.ini
ok "nginx/cloudflare.ini criado (chmod 600)"

# ── 5. Sobe serviços de aplicação (sem nginx/certbot ainda) ──────────────────
# DNS-01 não precisa da porta 80 aberta, então a aplicação pode subir já no
# modo de produção (sem portas publicadas para db/redis/minio/backend/frontend).
log "Subindo serviços de aplicação..."
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d db redis minio backend frontend
ok "Serviços de aplicação no ar"

# ── 6. Emite certificado SSL (DNS-01 via Cloudflare) ──────────────────────────
log "Emitindo certificado SSL para app.$DOMAIN e api.$DOMAIN..."
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm --entrypoint "" certbot \
  certbot certonly \
  --dns-cloudflare \
  --dns-cloudflare-credentials /etc/letsencrypt/cloudflare.ini \
  --dns-cloudflare-propagation-seconds 30 \
  --email "$EMAIL" \
  --agree-tos \
  --no-eff-email \
  --non-interactive \
  --keep-until-expiring \
  -d "app.$DOMAIN" \
  -d "api.$DOMAIN"
ok "Certificado SSL emitido"

# ── 7. Sobe nginx + renovação automática ──────────────────────────────────────
log "Subindo nginx (HTTPS) e o serviço de renovação do certificado..."
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
ok "Todos os serviços rodando"

# ── 8. Configura cron de backup ───────────────────────────────────────────────
log "Configurando backup automático..."
CRON_JOB="0 2 * * * cd $(pwd) && docker compose --profile backup run --rm pg_dump >> /var/log/mastersat-backup.log 2>&1"
(crontab -l 2>/dev/null | grep -v 'mastersat'; echo "$CRON_JOB") | crontab -
ok "Backup agendado para 02:00 diariamente"

# ── 9. Teste de saúde ─────────────────────────────────────────────────────────
log "Testando a aplicação..."
sleep 5
HTTP_CODE=$(curl -sk -o /dev/null -w "%{http_code}" "https://app.$DOMAIN/" || echo "000")
[ "$HTTP_CODE" = "200" ] && ok "Frontend OK (HTTPS $HTTP_CODE)" || echo "⚠ Frontend retornou $HTTP_CODE — verifique os logs"

API_CODE=$(curl -sk -o /dev/null -w "%{http_code}" "https://api.$DOMAIN/api/v1/auth/me" || echo "000")
[ "$API_CODE" = "401" ] && ok "API OK (retornou 401 sem token — correto)" || echo "⚠ API retornou $API_CODE"

# ── Conclusão ──────────────────────────────────────────────────────────────────
echo ""
echo "=================================================================="
echo "  ✅ Deploy concluído!"
echo ""
echo "  Frontend: https://app.$DOMAIN"
echo "  API:      https://api.$DOMAIN/api/v1"
echo ""
echo "  Logs:     docker compose logs -f"
echo "  Status:   docker compose ps"
echo ""
echo "  Próximo passo recomendado:"
echo "    sudo ./scripts/harden-vps.sh   (firewall, fail2ban, SSH, atualizações)"
echo "=================================================================="
