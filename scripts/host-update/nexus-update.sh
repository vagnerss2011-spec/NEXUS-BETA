#!/bin/bash
# /usr/local/bin/nexus-update.sh — disparado pelo systemd path unit quando o
# backend escreve /var/lib/nexus/update/request.json (volume bind-mounted do
# container backend pra `${REPO}/infra/update/`).
#
# Faz: lê request → escreve status=running → git fetch + checkout → docker
# compose up -d --build → escreve status=success|failed → remove request.
#
# Roda como root (systemd unit). NÃO usar variáveis do shell vindas do request
# sem validar — só lemos a tag e validamos com regex semver. Defesa simples
# porque o request file é escrito pelo backend, mas vale ser paranoico.
set -euo pipefail

# ──────────────────────── Configuração ────────────────────────

REPO_DIR="${REPO_DIR:-/root/NEXUS-BETA}"
UPDATE_DIR="${REPO_DIR}/infra/update"
REQUEST_FILE="${UPDATE_DIR}/request.json"
STATUS_FILE="${UPDATE_DIR}/status.json"
LOG_FILE="${UPDATE_DIR}/last-run.log"
HOST_READY_MARKER="${UPDATE_DIR}/host-ready"

mkdir -p "${UPDATE_DIR}"
# (Re)cria marker — confirma pro backend que o helper está vivo.
touch "${HOST_READY_MARKER}"

# ──────────────────────── Helpers ────────────────────────

write_status() {
  # write_status <state> [mensagem]
  # Escrita atômica: tmp + mv.
  local state="$1"; shift
  local mensagem="${1:-}"
  local log_id="${LOG_ID:-0}"
  local now
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  local tmp="${STATUS_FILE}.tmp"
  cat > "${tmp}" <<EOF
{
  "log_id": ${log_id},
  "state": "${state}",
  "versao_para": "${TARGET:-}",
  "mensagem": $(printf '%s' "${mensagem}" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))'),
  "updated_at": "${now}"
}
EOF
  mv "${tmp}" "${STATUS_FILE}"
}

fail() {
  local msg="$1"
  echo "[nexus-update] FAIL: ${msg}" | tee -a "${LOG_FILE}" >&2
  write_status "failed" "${msg}"
  exit 1
}

log() {
  echo "[nexus-update] $*" | tee -a "${LOG_FILE}"
}

# ──────────────────────── Validações ────────────────────────

if [ ! -f "${REQUEST_FILE}" ]; then
  # Pode acontecer se o path unit foi disparado por evento residual.
  exit 0
fi

if ! command -v jq >/dev/null 2>&1; then
  fail "jq não instalado no host — rode 'apt-get install -y jq' e tente de novo"
fi

LOG_ID="$(jq -r '.log_id // 0' "${REQUEST_FILE}")"
TARGET="$(jq -r '.versao_para // ""' "${REQUEST_FILE}")"
FROM="$(jq -r '.versao_de // ""' "${REQUEST_FILE}")"
USR="$(jq -r '.usuario_nome // "?"' "${REQUEST_FILE}")"

if [[ ! "${TARGET}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  fail "tag alvo inválida no request: '${TARGET}'"
fi

# ──────────────────────── Execução ────────────────────────

log "==== start ===="
log "log_id=${LOG_ID} ${FROM} -> ${TARGET} (por ${USR})"

write_status "running" "iniciando update para ${TARGET}"

cd "${REPO_DIR}" || fail "REPO_DIR não acessível: ${REPO_DIR}"

# Captura a saída pra incluir na mensagem em caso de falha.
{
  log "git fetch origin"
  git fetch origin --tags 2>&1
  log "git checkout ${TARGET}"
  git checkout "${TARGET}" 2>&1
  log "docker compose up -d --build backend frontend"
  docker compose up -d --build backend frontend 2>&1
} >> "${LOG_FILE}" 2>&1 || {
  # Pega as últimas 80 linhas do log pra reportar a falha
  tail_msg="$(tail -n 80 "${LOG_FILE}" 2>/dev/null || true)"
  fail "comando falhou: ${tail_msg}"
}

log "update concluído"
write_status "success" "atualizado para ${TARGET}"

# Remove o trigger pra não re-executar a próxima vez que o path unit acordar.
rm -f "${REQUEST_FILE}"
log "==== end ===="
