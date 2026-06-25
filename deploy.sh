#!/usr/bin/env bash
# =============================================================================
# deploy.sh — Deploy de produção do Mastersat (HTTPS via Let's Encrypt HTTP-01)
#
# Use quando o DNS de app.SEU_DOMINIO e api.SEU_DOMINIO já aponta (registro A)
# DIRETO para o IP desta VPS — SEM Cloudflare. A validação do certificado é
# feita pela porta 80 (webroot), então a porta 80 precisa estar aberta na
# internet (UFW/firewall do provedor liberando 80 e 443).
#
# Para deploy ATRÁS da Cloudflare (DNS-01, com WAF/proxy), ver docs/deploy-producao.md.
#
# Execute UMA VEZ no servidor após clonar o repositório e configurar o .env:
#   chmod +x deploy.sh && DOMAIN=mastersat.com.br EMAIL=voce@email.com ./deploy.sh
#
# O que este script faz:
#   1. Valida configurações de segurança do .env
#   2. Configura o domínio no nginx.conf
#   3. Builda e sobe os serviços de aplicação
#   4. Cria um certificado TEMPORÁRIO para o nginx conseguir subir
#   5. Sobe o nginx (porta 80/443)
#   6. Emite o certificado REAL via Let's Encrypt (HTTP-01 / webroot)
#   7. Recarrega o nginx e sobe a renovação automática
#   8. Configura cron de backup e faz um teste de saúde
# =============================================================================

set -euo pipefail
DOMAIN="${DOMAIN:-}"
EMAIL="${EMAIL:-}"
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

log()  { echo -e "\n\033[1;34m▶ $*\033[0m"; }
ok()   { echo -e "\033[1;32m✓ $*\033[0m"; }
fail() { echo -e "\033[1;31m✗ ERRO: $*\033[0m"; exit 1; }

# ── 1. Verificações iniciais ──────────────────────────────────────────────────
log "Verificando pré-requisitos..."
command -v docker >/dev/null || fail "Docker não instalado"
[ -f ".env" ] || fail ".env não encontrado — copie .env.example e configure"

[ -z "$DOMAIN" ] && { read -p "Domínio raiz (ex: mastersat.com.br): " DOMAIN; }
[ -z "$EMAIL"  ] && { read -p "E-mail para Let's Encrypt: " EMAIL; }
[ -z "$DOMAIN" ] && fail "Domínio não informado"
[ -z "$EMAIL"  ] && fail "E-mail não informado"

# ── 2. Valida segurança do .env ───────────────────────────────────────────────
log "Validando configurações de segurança..."

SECRET=$(grep '^SECRET_KEY=' .env | cut -d= -f2-)
[ "${#SECRET}" -lt 32 ] && fail "SECRET_KEY muito curta — gere com: python -c \"import secrets; print(secrets.token_hex(32))\""
[[ "$SECRET" == "change-me-super-secret" ]] && fail "SECRET_KEY ainda é o valor padrão"

DEBUG_TOKEN=$(grep '^DEBUG_RETURN_RESET_TOKEN=' .env | cut -d= -f2- || echo "false")
[[ "$DEBUG_TOKEN" == "true" ]] && fail "DEBUG_RETURN_RESET_TOKEN=true — defina como false no .env"

DB_URL=$(grep '^DATABASE_URL=' .env | cut -d= -f2-)
[[ "$DB_URL" == *"postgres:postgres@"* ]] && fail "DATABASE_URL ainda usa a senha padrão do Postgres — troque antes de produção"

MINIO_PASS=$(grep '^MINIO_ROOT_PASSWORD=' .env | cut -d= -f2- || echo "")
[[ "$MINIO_PASS" == "minioadmin" || -z "$MINIO_PASS" ]] && fail "MINIO_ROOT_PASSWORD ainda é o valor padrão — troque antes de produção"

ok "Configurações de segurança OK"

# ── 3. Configura domínio no nginx.conf (idempotente) ──────────────────────────
log "Configurando nginx para o domínio $DOMAIN (app.$DOMAIN / api.$DOMAIN)..."
sed -i "s/SEU_DOMINIO.COM.BR/$DOMAIN/g" nginx/nginx.conf
ok "nginx.conf atualizado"

# ── 4. Builda e sobe serviços de aplicação ────────────────────────────────────
log "Buildando e subindo serviços de aplicação..."
$COMPOSE up -d --build db redis minio backend frontend
ok "Serviços de aplicação no ar"

CERT_DIR="/etc/letsencrypt/live/app.$DOMAIN"

# ── 5. Certificado TEMPORÁRIO (para o nginx conseguir subir com bloco 443) ────
log "Criando certificado temporário (auto-assinado) para o nginx iniciar..."
$COMPOSE run --rm --entrypoint sh certbot -c "\
  mkdir -p '$CERT_DIR' && \
  openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
    -keyout '$CERT_DIR/privkey.pem' \
    -out '$CERT_DIR/fullchain.pem' \
    -subj '/CN=localhost' 2>/dev/null"
ok "Certificado temporário criado"

# ── 6. Sobe o nginx (já consegue iniciar com o cert temporário) ───────────────
log "Subindo nginx (portas 80/443)..."
$COMPOSE up -d nginx
ok "nginx no ar"

# ── 7. Emite o certificado REAL via HTTP-01 (webroot) ─────────────────────────
log "Removendo o cert temporário e emitindo o real (Let's Encrypt HTTP-01)..."
$COMPOSE run --rm --entrypoint sh certbot -c "\
  rm -rf '/etc/letsencrypt/live/app.$DOMAIN' \
         '/etc/letsencrypt/archive/app.$DOMAIN' \
         '/etc/letsencrypt/renewal/app.$DOMAIN.conf'"
$COMPOSE run --rm --entrypoint "" certbot \
  certbot certonly --webroot -w /var/www/certbot \
  --email "$EMAIL" \
  --agree-tos \
  --no-eff-email \
  --non-interactive \
  -d "app.$DOMAIN" \
  -d "api.$DOMAIN"
ok "Certificado SSL real emitido"

# ── 8. Recarrega o nginx com o cert real e sobe a renovação automática ────────
log "Recarregando nginx com o certificado real..."
$COMPOSE exec nginx nginx -s reload
ok "nginx recarregado"

log "Subindo o serviço de renovação automática do certificado..."
$COMPOSE up -d certbot
ok "Renovação automática ativa (verifica a cada 12h)"

# ── 9. Configura cron de backup ───────────────────────────────────────────────
log "Configurando backup automático..."
CRON_JOB="0 2 * * * cd $(pwd) && docker compose --profile backup run --rm pg_dump >> /var/log/mastersat-backup.log 2>&1"
(crontab -l 2>/dev/null | grep -v 'mastersat'; echo "$CRON_JOB") | crontab -
ok "Backup agendado para 02:00 diariamente"

# ── 10. Teste de saúde ────────────────────────────────────────────────────────
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
echo "  Logs:     $COMPOSE logs -f"
echo "  Status:   $COMPOSE ps"
echo ""
echo "  Próximo passo recomendado:"
echo "    sudo ./scripts/harden-vps.sh   (firewall, fail2ban, SSH, atualizações)"
echo "=================================================================="
