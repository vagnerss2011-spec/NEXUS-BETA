#!/bin/bash
# /usr/local/bin/nexus-update.sh — disparado pelo systemd path unit quando o
# backend escreve /var/lib/nexus/update/request.json (volume bind-mounted do
# container backend pra `${REPO}/infra/update/`).
#
# Faz: lê request → AUTO-REINSTALA-SE com versão atualizada do repo → escreve
# status=running → salva commit atual pra rollback → git fetch + checkout →
# docker compose up -d --build → health check do backend → escreve
# status=success ou (em caso de saúde ruim) faz rollback automático e marca
# status=rolled_back.
#
# Roda como root (systemd unit). Validações em duas camadas (backend escreve
# tag + script valida regex semver) protegem contra request file forjado.
set -euo pipefail

# ──────────────────────── Configuração ────────────────────────

REPO_DIR="${REPO_DIR:-/root/NEXUS-BETA}"
UPDATE_DIR="${REPO_DIR}/infra/update"
REQUEST_FILE="${UPDATE_DIR}/request.json"
STATUS_FILE="${UPDATE_DIR}/status.json"
LOG_FILE="${UPDATE_DIR}/last-run.log"
HOST_READY_MARKER="${UPDATE_DIR}/host-ready"

# Tempos de health check (v2.5.0). start_period do healthcheck Docker já dá 30s
# antes da 1ª probe; aqui esperamos um pouco mais e damos N tentativas.
WARMUP_SECONDS="${NEXUS_UPDATE_WARMUP:-20}"
HEALTH_RETRIES="${NEXUS_UPDATE_RETRIES:-12}"
HEALTH_INTERVAL_SECONDS="${NEXUS_UPDATE_INTERVAL:-10}"
BACKEND_CONTAINER="${BACKEND_CONTAINER:-nexus-beta-backend-1}"

mkdir -p "${UPDATE_DIR}"
# (Re)cria marker — confirma pro backend que o helper está vivo.
touch "${HOST_READY_MARKER}"

# ──────────────────────── Auto-update do próprio script ────────────────────────
# Antes de qualquer coisa, garantir que a versão mais recente do script (que
# veio com o git pull anterior) sobrescreve a versão instalada. Sem isso,
# melhorias no nexus-update.sh nunca chegariam pelo painel — o script velho
# se preservaria. A cópia roda só se o conteúdo for diferente, pra economizar
# IO. Se já estamos rodando o atualizado, isso é no-op.
SELF_SRC="${REPO_DIR}/scripts/host-update/nexus-update.sh"
SELF_DST="/usr/local/bin/nexus-update.sh"
if [ -f "${SELF_SRC}" ] && ! cmp -s "${SELF_SRC}" "${SELF_DST}"; then
  install -m 0755 "${SELF_SRC}" "${SELF_DST}" || true
fi

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

log() {
  echo "[nexus-update] $*" | tee -a "${LOG_FILE}"
}

tail_log() {
  tail -n 80 "${LOG_FILE}" 2>/dev/null || true
}

fail() {
  local msg="$1"
  echo "[nexus-update] FAIL: ${msg}" | tee -a "${LOG_FILE}" >&2
  write_status "failed" "${msg}"
  exit 1
}

# Health check do backend usando o healthcheck nativo do Docker. start_period
# protege contra falsas negativas durante boot. Espera healthy via polling.
check_backend_healthy() {
  local i status
  for ((i=1; i<=HEALTH_RETRIES; i++)); do
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${BACKEND_CONTAINER}" 2>/dev/null || echo missing)"
    log "health check ${i}/${HEALTH_RETRIES}: ${status}"
    if [ "${status}" = "healthy" ]; then
      return 0
    fi
    if [ "${status}" = "missing" ]; then
      # Container nem existe — fatal.
      return 2
    fi
    sleep "${HEALTH_INTERVAL_SECONDS}"
  done
  # Esgotou tentativas — pode estar starting ou unhealthy.
  return 1
}

# ──────────────────────── Pre-flight ────────────────────────

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

# Salva o commit/ref atual ANTES de mexer — necessário pro rollback (v2.5.0).
# Preferência: tag exata se HEAD bater com uma; senão o SHA, que git checkout
# aceita normalmente. PREV_REF é o que volta em caso de falha de saúde.
PREV_REF="$(git describe --tags --exact-match HEAD 2>/dev/null || git rev-parse HEAD)"
log "ref atual (rollback target): ${PREV_REF}"

# Captura a saída pra incluir na mensagem em caso de falha.
{
  log "git fetch origin"
  git fetch origin --tags 2>&1
  log "git checkout ${TARGET}"
  git checkout "${TARGET}" 2>&1
  log "docker compose up -d --build backend frontend"
  docker compose up -d --build backend frontend 2>&1
} >> "${LOG_FILE}" 2>&1 || {
  fail "comando falhou: $(tail_log)"
}

log "warmup de ${WARMUP_SECONDS}s antes do health check"
sleep "${WARMUP_SECONDS}"

# ──────────────────────── Health check + rollback ────────────────────────

log "checando saúde do backend (${HEALTH_RETRIES} tentativas, intervalo ${HEALTH_INTERVAL_SECONDS}s)"
if check_backend_healthy; then
  log "backend healthy — update OK"
  write_status "success" "atualizado para ${TARGET}"
  rm -f "${REQUEST_FILE}"
  log "==== end ===="
  exit 0
fi

# Saúde NÃO voltou — tenta rollback automático.
log "backend NÃO ficou healthy após ${HEALTH_RETRIES} tentativas — iniciando rollback pra ${PREV_REF}"
write_status "running" "saúde não voltou; revertendo para ${PREV_REF}…"

{
  log "git checkout ${PREV_REF}"
  git checkout "${PREV_REF}" 2>&1
  log "docker compose up -d --build backend frontend (rollback)"
  docker compose up -d --build backend frontend 2>&1
} >> "${LOG_FILE}" 2>&1 || {
  # Rollback falhou — situação ruim, mas o painel verá failed + tail do log.
  fail "rollback FALHOU após health check ruim. Saída: $(tail_log)"
}

# Verifica saúde novamente após rollback
sleep "${WARMUP_SECONDS}"
if check_backend_healthy; then
  log "rollback OK — backend healthy novamente em ${PREV_REF}"
  write_status "rolled_back" "Saúde do backend não voltou após atualizar para ${TARGET}. Sistema revertido automaticamente para ${PREV_REF}. Veja last-run.log pra detalhes."
  rm -f "${REQUEST_FILE}"
  log "==== end (rolled back) ===="
  exit 0
fi

# Rollback "rodou" mas a saúde ainda não voltou — o estado do servidor é
# ambíguo. Marca failed e pede intervenção manual.
fail "rollback executou mas backend ainda não está healthy. Intervenção manual necessária. Tail: $(tail_log)"
