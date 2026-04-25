#!/usr/bin/env bash
# Backup diário do Postgres do NEXUS BETA.
#
# Como roda:
#   - Faz pg_dump no container 'db' via 'docker compose exec'
#   - Salva em $BACKUP_DIR como nexus-YYYYMMDD-HHMMSS.dump (formato custom, já comprimido)
#   - Apaga dumps com mais de $RETENTION_DAYS dias
#
# Variáveis lidas de .env: POSTGRES_USER, POSTGRES_DB
# Variáveis opcionais (override por ambiente):
#   BACKUP_DIR       (default /var/backups/nexus-postgres)
#   RETENTION_DAYS   (default 14)
#
# Cron (instalada em /etc/cron.d/nexus-pg-backup):
#   30 2 * * * root cd /root/NEXUS-BETA && ./scripts/pg-backup.sh >> /var/log/nexus-pg-backup.log 2>&1
#
# Restore manual:
#   docker compose exec -T db pg_restore -U $POSTGRES_USER -d $POSTGRES_DB --clean --if-exists \
#     < /var/backups/nexus-postgres/nexus-AAAAMMDD-HHMMSS.dump

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/nexus-postgres}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

cd "$PROJECT_DIR"

# Carrega POSTGRES_USER e POSTGRES_DB do .env
set -a
# shellcheck disable=SC1091
source ./.env
set +a

if [ -z "${POSTGRES_USER:-}" ] || [ -z "${POSTGRES_DB:-}" ]; then
  echo "[$(date -Iseconds)] ERRO: POSTGRES_USER ou POSTGRES_DB não definidos em .env" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

TS=$(date +%Y%m%d-%H%M%S)
OUT="$BACKUP_DIR/nexus-${TS}.dump"

echo "[$(date -Iseconds)] iniciando dump de $POSTGRES_DB para $OUT"
docker compose exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom > "$OUT"
chmod 600 "$OUT"

SIZE=$(du -h "$OUT" | cut -f1)
echo "[$(date -Iseconds)] dump concluído ($SIZE)"

# Rotação
DELETED=$(find "$BACKUP_DIR" -maxdepth 1 -name 'nexus-*.dump' -type f -mtime "+$RETENTION_DAYS" -print -delete | wc -l)
echo "[$(date -Iseconds)] rotação: $DELETED arquivo(s) com mais de $RETENTION_DAYS dias removido(s)"
