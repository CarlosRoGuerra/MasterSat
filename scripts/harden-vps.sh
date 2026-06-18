#!/usr/bin/env bash
# =============================================================================
# harden-vps.sh — Hardening de segurança da VPS do Mastersat
#
# Execute como root (ou via sudo) UMA VEZ no servidor:
#   sudo ./scripts/harden-vps.sh
#
# O que este script faz:
#   1. Ativa atualizações automáticas de segurança (unattended-upgrades)
#   2. Configura o firewall UFW (libera apenas SSH, 80 e 443)
#   3. Instala e configura o fail2ban (bloqueia brute-force no SSH)
#   4. Hardening do SSH: desabilita login por senha e (quando seguro)
#      o login root — com checagens para não bloquear seu próprio acesso
#
# IMPORTANTE sobre Docker + UFW:
#   Containers com "ports:" publicadas (ex.: "8000:8000") ficam acessíveis
#   mesmo com o UFW bloqueando a porta — o Docker manipula o iptables
#   diretamente (chain DOCKER-USER), à frente das regras do UFW. Por isso
#   o docker-compose.prod.yml deste projeto usa "ports: !reset []" para
#   garantir que NENHUM serviço além do nginx (80/443) publique portas no
#   host. Sempre confira após qualquer mudança no compose:
#     docker compose -f docker-compose.yml -f docker-compose.prod.yml config | grep -A2 published
#   Se precisar publicar uma porta extra para debug, prefira
#   "127.0.0.1:PORTA:PORTA" (acessível só via túnel SSH) em vez de "0.0.0.0".
# =============================================================================

set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

log()  { echo -e "\n\033[1;34m▶ $*\033[0m"; }
ok()   { echo -e "\033[1;32m✓ $*\033[0m"; }
warn() { echo -e "\033[1;33m⚠ $*\033[0m"; }
fail() { echo -e "\033[1;31m✗ ERRO: $*\033[0m"; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "Execute como root (sudo ./scripts/harden-vps.sh)"

# ── 1. Atualizações automáticas de segurança ──────────────────────────────────
log "Instalando dependências (ufw, fail2ban, unattended-upgrades)..."
apt-get update -qq
apt-get install -y -qq ufw fail2ban unattended-upgrades

log "Ativando atualizações automáticas de segurança..."
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
ok "unattended-upgrades ativo (config padrão já cobre updates de segurança)"

# ── 2. Firewall (UFW) ──────────────────────────────────────────────────────────
log "Configurando UFW..."
SSH_PORT=$(grep -E '^\s*Port\s+[0-9]+' /etc/ssh/sshd_config 2>/dev/null | awk '{print $2}' | head -1) || true
SSH_PORT="${SSH_PORT:-22}"

ufw default deny incoming
ufw default allow outgoing
ufw allow "$SSH_PORT"/tcp comment 'SSH'
ufw allow 80/tcp  comment 'HTTP (redirect HTTPS)'
ufw allow 443/tcp comment 'HTTPS'
ufw --force enable
ok "UFW ativo — liberado apenas SSH ($SSH_PORT/tcp), 80/tcp e 443/tcp"

# ── 3. fail2ban ────────────────────────────────────────────────────────────────
log "Configurando fail2ban (proteção contra brute-force no SSH)..."
cat > /etc/fail2ban/jail.local <<EOF
[sshd]
enabled  = true
port     = $SSH_PORT
backend  = systemd
maxretry = 5
findtime = 10m
bantime  = 1h
EOF
systemctl enable --now fail2ban >/dev/null
systemctl restart fail2ban
ok "fail2ban ativo (jail sshd: 5 tentativas / 10min => ban de 1h)"

# ── 4. Hardening do SSH ─────────────────────────────────────────────────────────
log "Aplicando hardening do SSH..."

CURRENT_USER="${SUDO_USER:-$(logname 2>/dev/null || echo root)}"
USER_HOME=$(getent passwd "$CURRENT_USER" | cut -d: -f6)
AUTHORIZED_KEYS="$USER_HOME/.ssh/authorized_keys"

if [ ! -s "$AUTHORIZED_KEYS" ]; then
  warn "Nenhuma chave SSH encontrada em $AUTHORIZED_KEYS para o usuário '$CURRENT_USER'."
  warn "Pulando o hardening do SSH para não bloquear seu próprio acesso."
  warn "Configure uma chave SSH (ssh-copy-id) e rode este script novamente."
else
  cp /etc/ssh/sshd_config "/etc/ssh/sshd_config.bak.$(date +%Y%m%d%H%M%S)"

  # Desabilita login por senha — seguro mesmo que o único acesso seja o
  # usuário root, desde que ele tenha uma chave em authorized_keys (checado acima).
  sed -i \
    -e 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' \
    -e 's/^#\?ChallengeResponseAuthentication.*/ChallengeResponseAuthentication no/' \
    -e 's/^#\?KbdInteractiveAuthentication.*/KbdInteractiveAuthentication no/' \
    /etc/ssh/sshd_config

  if [ "$CURRENT_USER" != "root" ] && id -nG "$CURRENT_USER" | grep -qw sudo; then
    # Existe um usuário sudo com chave própria: pode desabilitar root totalmente.
    sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
    ROOT_LOGIN_MSG="desabilitado (PermitRootLogin no) — use '$CURRENT_USER' + sudo"
  else
    # Único acesso é root: mantém login root só por chave (nunca por senha).
    sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
    ROOT_LOGIN_MSG="restrito a chave (PermitRootLogin prohibit-password) — nenhum outro usuário sudo encontrado"
  fi

  if sshd -t; then
    systemctl reload ssh 2>/dev/null || systemctl reload sshd
    ok "SSH endurecido: PasswordAuthentication no, login root $ROOT_LOGIN_MSG"
    ok "Backup do sshd_config original salvo em /etc/ssh/sshd_config.bak.*"
  else
    cp "/etc/ssh/sshd_config.bak."* /etc/ssh/sshd_config
    fail "sshd_config inválido após edição — backup restaurado, revise manualmente"
  fi
fi

# ── Conclusão ────────────────────────────────────────────────────────────────
echo ""
echo "=================================================================="
echo "  ✅ Hardening concluído!"
echo ""
ufw status verbose | sed 's/^/  /'
echo ""
echo "  fail2ban:  $(systemctl is-active fail2ban)"
echo ""
echo "  ⚠ ANTES DE FECHAR ESTA SESSÃO: abra um NOVO terminal e confirme que"
echo "  ainda consegue conectar via SSH (chave, sem senha)."
echo "=================================================================="
