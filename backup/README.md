# Backup Automático — Mastersat

## Estratégia

| Camada | O que | Quando | Retenção |
|---|---|---|---|
| PostgreSQL | `pg_dump` comprimido | Diário 02:00 | 30 dias |
| MinIO | sync via `mc mirror` | Semanal | 4 semanas |
| Nuvem | upload via rclone | Junto ao pg_dump | 90 dias |

---

## Configuração rápida (5 min)

### 1. Adicionar ao `.env`

```env
# Backup
BACKUP_KEEP_DAYS=30
ALERT_WEBHOOK=https://discord.com/api/webhooks/SEU_WEBHOOK  # opcional

# Nuvem (escolha uma opção abaixo)
RCLONE_REMOTE=b2:mastersat-backups
```

### 2. Configurar destino na nuvem

#### Opção A — Backblaze B2 (mais barato: $0,006/GB/mês)

```bash
# Instalar rclone no servidor
curl https://rclone.org/install.sh | sudo bash

# Configurar (siga o wizard)
rclone config
# → n (novo remote)
# → nome: b2
# → tipo: 2 (Backblaze B2)
# → Account ID e Application Key do painel Backblaze

# Testar
rclone lsd b2:
```

#### Opção B — Google Drive (gratuito até 15GB)

```bash
rclone config
# → tipo: drive (Google Drive)
# → siga autenticação OAuth
```

#### Opção C — AWS S3

```bash
rclone config
# → tipo: s3
# → provider: AWS
# → access_key_id + secret_access_key
```

---

## Executar backup manualmente

```bash
# Backup apenas do banco (modo mais simples)
docker compose --profile backup run --rm pg_dump

# Backup completo via script
docker exec mastersat-db-1 \
  sh -c 'PGPASSWORD=$POSTGRES_PASSWORD pg_dump -U postgres rastreamento | gzip' \
  > backup_$(date +%Y%m%d).sql.gz

# Verificar backups existentes
ls -lh backup/db/
```

---

## Restaurar o banco

```bash
# 1. Parar o backend
docker compose stop backend

# 2. Restaurar (mais recente automático)
chmod +x backup/restore.sh
docker compose exec db bash /restore.sh

# 3. Reiniciar
docker compose start backend
```

---

## Verificar se o backup está funcionando

```bash
# Ver logs do último backup
cat backup/logs/backup_*.log | tail -30

# Listar backups na nuvem
rclone ls b2:mastersat-backups/db/ | head -10

# Tamanho total na nuvem
rclone size b2:mastersat-backups/
```

---

## Alertas de falha

O script envia uma notificação via webhook quando o backup falha.

**Discord:** Servidor → Configurações do canal → Integrações → Webhooks → Copiar URL  
**Slack:** api.slack.com/apps → Incoming Webhooks → Ativar → Copiar URL

```env
ALERT_WEBHOOK=https://discord.com/api/webhooks/000000/xxxx
```

---

## Regra 3-2-1 (boas práticas)

✅ **3** cópias dos dados  
✅ **2** mídias diferentes (disco local + nuvem)  
✅ **1** cópia offsite (Backblaze/AWS/GDrive)

---

## Testar a restauração (IMPORTANTE)

> Um backup nunca testado não é um backup.

```bash
# Teste mensal recomendado:
# 1. Copiar backup para ambiente de teste
# 2. Subir docker-compose separado
# 3. Restaurar e verificar dados
# 4. Confirmar que o sistema funciona normalmente
```
