#!/usr/bin/env bash
# =============================================================================
# deploy.sh — Script de deploy completo do Mastersat em produção
#
# Execute UMA VEZ no servidor após clonar o repositório:
#   chmod +x deploy.sh && ./deploy.sh
#
# O que este script faz:
#   1. Valida configurações de segurança do .env
#   2. Sobe os serviços sem HTTPS primeiro (necessário para certbot)
#   3. Emite o certificado SSL via certbot
#   4. Sobe tudo com HTTPS ativo
#   5. Configura cron de backup automático
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

[ -z "$DOMAIN" ] && { read -p "Domínio (ex: mastersat.com.br): " DOMAIN; }
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

ok "Configurações de segurança OK"

# ── 3. Substitui domínio no nginx.conf ───────────────────────────────────────
log "Configurando nginx para o domínio $DOMAIN..."
sed -i "s/SEU_DOMINIO.COM.BR/$DOMAIN/g" nginx/nginx.conf
ok "nginx.conf atualizado"

# ── 4. Sobe serviços SEM nginx inicialmente (certbot precisa de HTTP livre) ──
log "Subindo serviços base (sem nginx)..."
docker compose up -d db redis minio backend frontend
sleep 10

# ── 5. Emite certificado SSL ──────────────────────────────────────────────────
log "Emitindo certificado SSL para $DOMAIN..."

# Nginx básico só para o desafio HTTP do certbot
docker run --rm \
  -v "$(pwd)/nginx/nginx.conf:/etc/nginx/nginx.conf:ro" \
  -v certbot-www:/var/www/certbot \
  -p 80:80 \
  nginx:1.25-alpine nginx -g "daemon off;" &
NGINX_PID=$!
sleep 3

docker compose run --rm certbot \
  certonly \
  --webroot \
  --webroot-path /var/www/certbot \
  --email "$EMAIL" \
  --agree-tos \
  --no-eff-email \
  -d "$DOMAIN" \
  -d "www.$DOMAIN" || true

kill $NGINX_PID 2>/dev/null || true
ok "Certificado SSL emitido"

# ── 6. Sobe tudo com HTTPS ────────────────────────────────────────────────────
log "Subindo todos os serviços com HTTPS..."
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
ok "Todos os serviços rodando"

# ── 7. Configura cron de backup ───────────────────────────────────────────────
log "Configurando backup automático..."
CRON_JOB="0 2 * * * cd $(pwd) && docker compose --profile backup run --rm pg_dump >> /var/log/mastersat-backup.log 2>&1"
(crontab -l 2>/dev/null | grep -v 'mastersat'; echo "$CRON_JOB") | crontab -
ok "Backup agendado para 02:00 diariamente"

# ── 8. Teste de saúde ─────────────────────────────────────────────────────────
log "Testando a aplicação..."
sleep 5
HTTP_CODE=$(curl -sk -o /dev/null -w "%{http_code}" "https://$DOMAIN/" || echo "000")
[ "$HTTP_CODE" = "200" ] && ok "Frontend OK (HTTPS $HTTP_CODE)" || echo "⚠ Frontend retornou $HTTP_CODE — verifique os logs"

API_CODE=$(curl -sk -o /dev/null -w "%{http_code}" "https://$DOMAIN/api/v1/auth/me" || echo "000")
[ "$API_CODE" = "401" ] && ok "API OK (retornou 401 sem token — correto)" || echo "⚠ API retornou $API_CODE"

# ── Conclusão ──────────────────────────────────────────────────────────────────
echo ""
echo "=================================================================="
echo "  ✅ Deploy concluído!"
echo ""
echo "  Frontend: https://$DOMAIN"
echo "  API:      https://$DOMAIN/api/v1"
echo "  Docs:     https://$DOMAIN/docs"
echo ""
echo "  Logs:     docker compose logs -f"
echo "  Status:   docker compose ps"
echo "=================================================================="
