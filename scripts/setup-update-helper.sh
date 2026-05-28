#!/bin/bash
# setup-update-helper.sh — instala o helper de update no HOST (Fase 2 — v2.4.0).
#
# Idempotente: roda quantas vezes quiser. Instala:
#   - /usr/local/bin/nexus-update.sh           (script de update)
#   - /etc/systemd/system/nexus-update.path    (path unit — watcher do request.json)
#   - /etc/systemd/system/nexus-update.service (one-shot que roda o script)
#
# Pré-requisitos: bash, systemd, jq, docker compose v2, git. Roda como root.
#
# Uso:
#   cd /root/NEXUS-BETA
#   bash scripts/setup-update-helper.sh
#
# Pra desinstalar: ver fim do arquivo.

set -euo pipefail

REPO_DIR="${REPO_DIR:-/root/NEXUS-BETA}"
SRC_DIR="${REPO_DIR}/scripts/host-update"
UPDATE_DIR="${REPO_DIR}/infra/update"

# ──────────────────────── Sanity checks ────────────────────────

if [ "${EUID}" -ne 0 ]; then
  echo "[!] Rode como root (use sudo). EUID=${EUID}" >&2
  exit 1
fi

for cmd in systemctl jq docker git; do
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    echo "[!] '${cmd}' não está no PATH. Instale antes de continuar."
    if [ "${cmd}" = "jq" ]; then
      echo "    apt-get install -y jq"
    fi
    exit 1
  fi
done

if ! docker compose version >/dev/null 2>&1; then
  echo "[!] 'docker compose' v2 não disponível. Verifique a instalação do Docker."
  exit 1
fi

if [ ! -d "${SRC_DIR}" ]; then
  echo "[!] Diretório do helper não encontrado: ${SRC_DIR}"
  echo "    Você precisa estar na v2.4.0+ do repo (git pull origin backup primeiro)."
  exit 1
fi

# ──────────────────────── Instalação ────────────────────────

echo "[*] Instalando nexus-update.sh em /usr/local/bin/"
install -m 0755 "${SRC_DIR}/nexus-update.sh" /usr/local/bin/nexus-update.sh

echo "[*] Instalando units do systemd em /etc/systemd/system/"
install -m 0644 "${SRC_DIR}/nexus-update.path"    /etc/systemd/system/nexus-update.path
install -m 0644 "${SRC_DIR}/nexus-update.service" /etc/systemd/system/nexus-update.service

echo "[*] Garantindo diretório do volume de trigger: ${UPDATE_DIR}"
mkdir -p "${UPDATE_DIR}"
# O backend (container) precisa enxergar este dir via bind mount. O
# docker-compose.yml já tem o mapeamento ./infra/update:/var/lib/nexus/update.
# Cria o marker pro backend saber que o helper está pronto.
touch "${UPDATE_DIR}/host-ready"

echo "[*] systemctl daemon-reload"
systemctl daemon-reload

echo "[*] habilitando e iniciando nexus-update.path"
systemctl enable --now nexus-update.path

# Validação pós-instalação
sleep 1
if systemctl is-active --quiet nexus-update.path; then
  echo "[OK] nexus-update.path ativo — watching ${UPDATE_DIR}/request.json"
else
  echo "[!!] nexus-update.path NÃO está ativo. Verifique com:"
  echo "     systemctl status nexus-update.path"
  exit 1
fi

cat <<'EOF'

[OK] Helper de update instalado com sucesso.

A partir de agora o painel pode disparar updates via botão "Atualizar pelo painel"
(Configurações → Atualização do sistema). Cada clique:
  1. Backend valida (admin master, versão alvo, scheduler livre, nada em andamento)
  2. Backend escreve infra/update/request.json
  3. systemd path unit detecta e dispara o script no host
  4. Script faz git fetch + checkout + docker compose up --build
  5. Status reflete no painel via polling

Pra ver o que aconteceu na última execução:
    tail -f /root/NEXUS-BETA/infra/update/last-run.log
    journalctl -u nexus-update.service -n 200 --no-pager

Pra desinstalar:
    systemctl disable --now nexus-update.path
    rm -f /etc/systemd/system/nexus-update.path /etc/systemd/system/nexus-update.service
    rm -f /usr/local/bin/nexus-update.sh
    systemctl daemon-reload
    rm -f /root/NEXUS-BETA/infra/update/host-ready

EOF
