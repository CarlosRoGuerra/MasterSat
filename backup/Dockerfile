# Imagem do job de backup/restore. Mesma versão major do PostgreSQL do
# serviço db: pg_dump/pg_restore precisam ser >= à versão do servidor.
FROM postgres:16

# rclone: cópia dos objetos do MinIO (S3) e envio externo com conferência.
# gnupg: cifragem opcional do dump. curl: alertas por webhook.
RUN apt-get update \
 && apt-get install -y --no-install-recommends rclone gnupg curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY lib.sh backup.sh restore.sh /opt/mastersat-backup/
RUN chmod 0555 /opt/mastersat-backup/*.sh

WORKDIR /opt/mastersat-backup
ENTRYPOINT []
CMD ["/opt/mastersat-backup/backup.sh"]
