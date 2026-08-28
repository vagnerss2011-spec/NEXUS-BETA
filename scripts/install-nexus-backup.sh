#!/usr/bin/env bash
# install-nexus-backup.sh — bootstrap do NEXUS BACKUP em Debian 13 (Trixie).
#
# Modos de execução:
#   bash install-nexus-backup.sh                  # auto: roda tudo idempotente
#   bash install-nexus-backup.sh -i               # interativo: confirma cada passo
#   bash install-nexus-backup.sh --interactive    # idem
#
# Variáveis de ambiente (opcionais):
#   NEXUS_EXTRA_CIDRS=cidr1,cidr2,...             # faixas adicionais autorizadas a
#                                                 # pedir hora ao chrony (NTP) e a
#                                                 # passar pelo UFW na 123/udp.
#                                                 # Default: nenhuma (só RFC1918+RFC6598).
#   REPO_URL=https://github.com/user/repo.git     # override do repo (default já é
#                                                 # o HTTPS público oficial). Use
#                                                 # git@github.com:... só pra fork privado
#                                                 # (aí o passo 3 cria a deploy key).
#
# Pré-requisitos (FAZER MANUALMENTE ANTES — ver docs/INSTALL.md):
#   1. SSH do host movido pra porta 2288 (libera 22 pro SFTP push do app).
#      → este script NÃO toca em sshd_config — risco alto de lockout.
#   2. Console (Proxmox/IPMI) acessível como fallback se SSH der ruim.
#   3. IP estático configurado, DNS público apontando pro IP (resolvendo).
#   4. Portas liberadas no firewall de borda (80, 443, 21, 22, 69/udp, 2288,
#      30000-30099 tcp+udp, 123/udp).
#   5. (Só fork privado) gerar deploy key SSH e cadastrar em
#      github.com/<owner>/<repo>/settings/keys ANTES do passo 3 (clone).
#      → o repo oficial é PÚBLICO: clone anônimo, NADA disso é necessário.
#      → se você rodar com REPO_URL=git@..., o script gera a key e te mostra a
#        public key pra colar.
#
# Antes dos passos, faz pré-flight de conectividade (DNS, github.com,
# api.ipify.org pra detectar IP público). Falha cedo se algo crítico
# estiver fora.
#
# O que o script faz (em 9 passos, idempotente — pode rodar várias vezes):
#   [1] Sistema base       — locale pt_BR.UTF-8, timezone, apt deps
#   [2] Docker Engine      — repo oficial + daemon.json (bip 10.17/24, pool 10.18/16)
#                            → passo 2 cria a chain DOCKER-USER, dep do fail2ban
#   [3] Clone do repo      — checkout na tag mais recente (default SSH/deploy key)
#   [4] Diretórios persist — infra/ftp-logs/ftp-auth.log e infra/state/
#                            → passo 4 cria o log file que o jail nexus-ftp tail-a
#   [5] Chrony NTP server  — allow RFC1918+RFC6598 + NEXUS_EXTRA_CIDRS + ratelimit
#   [6] Fail2ban           — action docker-allports + filter+jail nexus-ftp
#                            → roda DEPOIS dos passos 2 e 4 pra ter chain + log file
#   [7] UFW firewall       — regras (2288 SSH, 80/443, 21/22/69/30000-30099, 123/udp)
#   [8] Gera .env          — secrets gerados (SECRET_KEY, ENCRYPTION_KEY,
#                            DB_EXPORT_KEY, POSTGRES_PASSWORD) + auto FTP_MASQUERADE
#                            via api.ipify.org. Faz backup do .env antigo se existir.
#   [9] Cron pg-backup     — OPCIONAL: agenda pg_dump diário 03:30 (retém 14 dias)
#
# O que ainda é manual (depois deste script — ver docs/INSTALL.md):
#   - Editar .env: DOMAIN, CERTBOT_EMAIL (e revisar FTP_MASQUERADE_ADDRESS)
#   - ./init-letsencrypt.sh (pega certificado real)
#   - docker compose up -d
#   - Criar primeiro usuário admin (via Python no container — ver INSTALL.md §8)

set -euo pipefail

# ───────────────────────── flags ─────────────────────────
INTERACTIVE=0
AUTO_YES_REMAINING=0   # quando user pick "a" (all), pula prompts seguintes

for arg in "$@"; do
  case "$arg" in
    -i|--interactive) INTERACTIVE=1 ;;
    -h|--help)
      grep -E '^# ' "$0" | sed 's/^# //; s/^#//'
      exit 0 ;;
    *) printf 'Argumento desconhecido: %s (use --help)\n' "$arg" >&2; exit 1 ;;
  esac
done

# ───────────────────────── helpers de output ─────────────────────────
C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'
C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'
C_RED=$'\033[31m'; C_BLUE=$'\033[34m'; C_GREY=$'\033[90m'

ok()    { printf '  %s✓%s %s\n' "$C_GREEN" "$C_RESET" "$1"; }
warn()  { printf '  %s!%s %s\n' "$C_YELLOW" "$C_RESET" "$1"; }
fail()  { printf '  %s✗%s %s\n' "$C_RED" "$C_RESET" "$1" >&2; exit 1; }
skip()  { printf '  %s·%s %s (já feito)\n' "$C_GREY" "$C_RESET" "$1"; }
note()  { printf '  %s%s%s\n' "$C_GREY" "$1" "$C_RESET"; }

STEP_NUM=0
STEP_TOTAL=9

# IP público detectado no pré-flight — preenchido em FTP_MASQUERADE_ADDRESS
# se o usuário não definir manualmente. Vazio se a detecção falhar.
DETECTED_PUBLIC_IP=""

# confirm_step "Título do passo" "descrição multilinha do que faz"
# Retorna 0 (executar) ou 1 (pular). No modo auto sempre retorna 0.
confirm_step() {
  STEP_NUM=$((STEP_NUM + 1))
  local title="$1"
  local desc="${2:-}"

  printf '\n%s┌──────────────────────────────────────────────────────────────────────%s\n' "$C_BOLD$C_BLUE" "$C_RESET"
  printf '%s│ Passo %d/%d — %s%s\n' "$C_BOLD$C_BLUE" "$STEP_NUM" "$STEP_TOTAL" "$title" "$C_RESET"
  printf '%s└──────────────────────────────────────────────────────────────────────%s\n' "$C_BOLD$C_BLUE" "$C_RESET"
  if [ -n "$desc" ]; then
    printf '%s%s%s\n' "$C_GREY" "$desc" "$C_RESET"
  fi

  # Modo auto, ou usuário já picou "all" — segue
  [ "$INTERACTIVE" = "0" ] && return 0
  [ "$AUTO_YES_REMAINING" = "1" ] && return 0

  while true; do
    printf '\n%sExecutar? [s]im / [n]ão / [a]ll restantes / [q]uit:%s ' "$C_YELLOW" "$C_RESET"
    read -r ans </dev/tty
    case "${ans,,}" in
      s|sim|y|yes) return 0 ;;
      n|nao|não|no|'') warn "passo $STEP_NUM pulado"; return 1 ;;
      a|all|todos) AUTO_YES_REMAINING=1; ok "executando este e todos os próximos sem confirmar"; return 0 ;;
      q|quit|sair|exit) printf '%ssaindo no passo %d.%s\n' "$C_YELLOW" "$STEP_NUM" "$C_RESET"; exit 0 ;;
      *) printf '%sresposta inválida — use s/n/a/q.%s\n' "$C_RED" "$C_RESET" ;;
    esac
  done
}

# ───────────────────────── pré-flight ─────────────────────────
[ "$(id -u)" = "0" ] || fail "Rodar como root."

print_header() {
  printf '\n'
  printf '%s======================================================================%s\n' "$C_BOLD$C_BLUE" "$C_RESET"
  printf '%s NEXUS BACKUP — Bootstrap %s%s\n' "$C_BOLD$C_BLUE" "$([ "$INTERACTIVE" = "1" ] && echo "interativo" || echo "auto")" "$C_RESET"
  printf '%s======================================================================%s\n' "$C_BOLD$C_BLUE" "$C_RESET"
  if [ "$INTERACTIVE" = "1" ]; then
    note "Cada passo vai mostrar o que faz e perguntar antes de executar."
    note "Opções: [s]im executa  [n]ão pula  [a]ll executa todos sem perguntar  [q]uit sai"
  else
    note "Modo automático: todos os 9 passos serão executados em sequência."
    note "(Use --interactive pra confirmar cada passo.)"
  fi
}
print_header

# Sanity checks
if ! grep -q 'VERSION_CODENAME=trixie' /etc/os-release 2>/dev/null; then
  warn "Distro detectada NÃO é Debian 13 (Trixie)."
  printf '  Continuar mesmo assim? [s/N]: '
  read -r ans </dev/tty
  [ "${ans:-n}" = "s" ] || exit 1
fi

if ! ss -tlnp 2>/dev/null | grep -qE ':2288\b'; then
  warn "SSH não está escutando em 2288. Se você ainda está conectado pela 22,"
  warn "este script NÃO vai mover o sshd — você precisa fazer isso manualmente"
  warn "antes (ver docs/INSTALL.md §3)."
  printf '  Continuar mesmo assim? [s/N]: '
  read -r ans </dev/tty
  [ "${ans:-n}" = "s" ] || exit 1
fi

# ───────────────────────── ensure curl + ca-certificates ─────────────────────────
# O pré-flight a seguir usa `curl`, e Debian 13 minimal NÃO traz curl por default.
# Sem isso, o pré-flight de HTTPS quebra antes de chegar no passo 1 (que instalaria curl
# via apt). Esse bloco precede o pré-flight pra garantir a ferramenta básica.
if ! command -v curl >/dev/null 2>&1; then
  printf '\n%sInstalando curl + ca-certificates (necessário pro pré-flight)…%s\n' "$C_BOLD" "$C_RESET"
  apt-get update -qq
  apt-get install -y -qq curl ca-certificates >/dev/null
  ok "curl instalado"
fi

# ───────────────────────── pré-flight de conectividade ─────────────────────────
# Por que: melhor descobrir agora que algo crucial está fora do ar do que falhar
# tarde no passo 3 (clone do repo). Cada check tem timeout curto pra não travar.
printf '\n%sPré-flight: testando conectividade…%s\n' "$C_BOLD" "$C_RESET"

# 1) DNS resolve algo público
if getent ahosts github.com >/dev/null 2>&1; then
  ok "DNS resolve github.com"
else
  fail "DNS não consegue resolver github.com. Confira /etc/resolv.conf — precisa ter
  ao menos uma linha 'nameserver <IP>'. Se está vazio, corrige rápido com:
      echo 'nameserver 1.1.1.1' > /etc/resolv.conf
      echo 'nameserver 8.8.8.8' >> /etc/resolv.conf
  E rode o script de novo. Pra solução permanente, configure DNS no seu network
  manager (systemd-networkd, NetworkManager, /etc/network/interfaces, etc.)."
fi

# 2) HTTPS pra GitHub funciona (clone vai usar)
if curl -fsSL --max-time 10 -o /dev/null https://github.com 2>/dev/null; then
  ok "HTTPS pra github.com OK"
else
  fail "Não consegui curl https://github.com — confira proxy/firewall de borda"
fi

# 3) HTTPS pra api.ipify.org (vai detectar IP público pra FTP_MASQUERADE_ADDRESS)
if DETECTED_PUBLIC_IP="$(curl -fsSL --max-time 10 https://api.ipify.org 2>/dev/null)" \
   && [[ "$DETECTED_PUBLIC_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  ok "IP público detectado: $DETECTED_PUBLIC_IP"
else
  DETECTED_PUBLIC_IP=""
  warn "Não consegui detectar IP público (api.ipify.org) — vou deixar FTP_MASQUERADE_ADDRESS vazio pra você preencher"
fi

# REPO_URL: default HTTPS anônimo (repo é público desde v2.5.1 — clone sem
# credencial). Override pra SSH (git@...) se você clonar de um fork privado;
# nesse caso o passo 3 ainda gera/usa a deploy key automaticamente.
REPO_URL="${REPO_URL:-https://github.com/vagnerss2011-spec/NEXUS-BETA.git}"
REPO_DIR='/root/NEXUS-BETA'

# ═════════════════════════ [1] Sistema base ═════════════════════════
if confirm_step "Sistema base (locale, timezone, apt deps)" \
"  Instala locale pt_BR.UTF-8, define timezone America/Sao_Paulo e
  instala pacotes do host: git, ufw, fail2ban, chrony, ca-certificates,
  curl, gnupg, jq, openssl.
  Por que: o backend (Python no container) lê TZ via tzdata; sem
  America/Sao_Paulo o scheduler roda em UTC silenciosamente."; then

  if ! locale -a 2>/dev/null | grep -qi 'pt_BR.utf8'; then
    apt-get install -y locales >/dev/null
    sed -i 's/^# *\(pt_BR.UTF-8\)/\1/' /etc/locale.gen
    locale-gen pt_BR.UTF-8 >/dev/null
    ok "locale pt_BR.UTF-8 gerado"
  else
    skip "locale pt_BR.UTF-8"
  fi

  if [ "$(timedatectl show -p Timezone --value)" != "America/Sao_Paulo" ]; then
    timedatectl set-timezone America/Sao_Paulo
    ok "timezone definida pra America/Sao_Paulo"
  else
    skip "timezone"
  fi

  APT_DEPS=(git ufw fail2ban chrony ca-certificates curl gnupg jq openssl)
  MISSING=()
  for p in "${APT_DEPS[@]}"; do
    dpkg -s "$p" >/dev/null 2>&1 || MISSING+=("$p")
  done
  if [ ${#MISSING[@]} -gt 0 ]; then
    apt-get update -qq
    apt-get install -y "${MISSING[@]}" >/dev/null
    ok "instalado: ${MISSING[*]}"
  else
    skip "pacotes base"
  fi
fi

# ═════════════════════════ [2] Docker Engine ═════════════════════════
# Antes do fail2ban porque a action docker-allports precisa que a chain
# DOCKER-USER já exista (Docker cria automaticamente no startup).
if confirm_step "Docker Engine (repo oficial) + daemon.json" \
"  Instala Docker Engine + Compose plugin pelo repo oficial Docker
  (não o do Debian — versão muito antiga). Em seguida escreve
  /etc/docker/daemon.json com:
    - bip = 10.17.0.1/24            (docker0 default bridge)
    - default-address-pools = 10.18.0.0/16 size 24  (compose networks)
    - IPv6 habilitado (necessário pra SSH em links BGP IPv6)
  Por que essas faixas: defaults 172.17/172.18 já bateram com IPs de
  cliente real (172.17.89.2 era uma OLT em produção).
  Se já houver daemon.json existente, ele será salvo em .bak.<TS>."; then

  if ! command -v docker >/dev/null 2>&1; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/debian/gpg \
      -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc

    CODENAME="$(. /etc/os-release && echo "${VERSION_CODENAME}")"
    cat > /etc/apt/sources.list.d/docker.list <<EOF
deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $CODENAME stable
EOF
    apt-get update -qq
    apt-get install -y docker-ce docker-ce-cli containerd.io \
      docker-buildx-plugin docker-compose-plugin >/dev/null
    ok "Docker instalado"
  else
    skip "Docker"
  fi

  DAEMON_JSON='/etc/docker/daemon.json'
  EXPECTED_BIP='10.17.0.1/24'
  NEEDS_RESTART=0
  if ! [ -f "$DAEMON_JSON" ] || ! grep -q "\"$EXPECTED_BIP\"" "$DAEMON_JSON"; then
    if [ -f "$DAEMON_JSON" ]; then
      cp "$DAEMON_JSON" "${DAEMON_JSON}.bak.$(date +%Y%m%d-%H%M%S)"
      warn "daemon.json existente salvo em ${DAEMON_JSON}.bak.*"
    fi
    cat > "$DAEMON_JSON" <<'EOF'
{
  "ipv6": true,
  "ip6tables": true,
  "experimental": true,
  "fixed-cidr-v6": "fd00:dead:beef::/48",
  "bip": "10.17.0.1/24",
  "default-address-pools": [
    {"base": "10.18.0.0/16", "size": 24}
  ]
}
EOF
    NEEDS_RESTART=1
    ok "daemon.json escrito (bip 10.17/24, pool 10.18/16, IPv6)"
  else
    skip "daemon.json"
  fi

  systemctl enable --now docker >/dev/null 2>&1
  if [ "$NEEDS_RESTART" = "1" ]; then
    systemctl restart docker
    ok "Docker reiniciado pra aplicar daemon.json"
  fi
fi

# ═════════════════════════ [3] Clone do repo ═════════════════════════
# Antes do fail2ban porque o jail nexus-ftp aponta pra um arquivo que mora
# dentro do repo clonado (passo 4 cria o file em si).
if confirm_step "Clone do repo NEXUS BACKUP em $REPO_DIR" \
"  Clona o repo do GitHub em $REPO_DIR e faz checkout na tag mais
  recente (v* mais alta por sort de versão).
  Default: REPO_URL=$REPO_URL  (HTTPS público — clone anônimo, sem credencial).
  Se o repo já existir, só atualiza pra última tag.
  Branch mainline é 'backup'; tags estáveis são vMAJOR.MINOR.PATCH.
  Fork PRIVADO via SSH (REPO_URL=git@...): vou gerar a deploy key
  automaticamente se não existir e te mostrar a public key pra cadastrar."; then

  # Setup deploy key SSH só se REPO_URL for SSH (fork privado; não é o default)
  if [[ "$REPO_URL" =~ ^git@ ]]; then
    DEPLOY_KEY=/root/.ssh/nexus_deploy_key
    if [ ! -f "$DEPLOY_KEY" ]; then
      mkdir -p /root/.ssh
      chmod 700 /root/.ssh
      ssh-keygen -t ed25519 -N "" -C "deploy-key@$(hostname)" -f "$DEPLOY_KEY" >/dev/null 2>&1
      ok "deploy key criada em $DEPLOY_KEY"
      # ssh config aponta a chave pro github.com
      if ! grep -q "IdentityFile $DEPLOY_KEY" /root/.ssh/config 2>/dev/null; then
        cat >> /root/.ssh/config <<EOF

Host github.com
    HostName github.com
    User git
    IdentityFile $DEPLOY_KEY
    IdentitiesOnly yes
EOF
        chmod 600 /root/.ssh/config
        ok "ssh config atualizado pra rotear github.com via deploy key"
      fi
    fi

    # Testa autenticação no GitHub
    SSH_OUT=$(ssh -o StrictHostKeyChecking=accept-new -T git@github.com 2>&1 || true)
    if echo "$SSH_OUT" | grep -q "successfully authenticated"; then
      ok "deploy key autenticou no GitHub"
    else
      warn "deploy key NÃO está autorizada no repo. Cole esta public key em:"
      warn "  https://github.com/<owner>/<repo>/settings/keys → Add deploy key (read-only)"
      printf '\n%s%s%s\n\n' "$C_BOLD" "$(cat "$DEPLOY_KEY.pub")" "$C_RESET"
      printf '  Pressione Enter quando terminar de colar (ou Ctrl-C pra abortar): '
      read -r _ </dev/tty
    fi
  fi

  if [ ! -d "$REPO_DIR/.git" ]; then
    git clone "$REPO_URL" "$REPO_DIR"
    ok "repo clonado em $REPO_DIR"
  else
    skip "repo (já existe em $REPO_DIR)"
  fi

  cd "$REPO_DIR"
  git fetch --tags origin >/dev/null 2>&1
  LATEST_TAG="$(git tag -l 'v*' --sort=-v:refname | head -1)"
  if [ -n "$LATEST_TAG" ]; then
    CURRENT_TAG="$(git describe --tags --exact-match HEAD 2>/dev/null || echo 'none')"
    if [ "$CURRENT_TAG" != "$LATEST_TAG" ]; then
      git checkout "$LATEST_TAG"
      ok "checkout $LATEST_TAG"
    else
      skip "já em $LATEST_TAG"
    fi
  fi
else
  cd "$REPO_DIR" 2>/dev/null || warn "REPO_DIR $REPO_DIR não existe — passos 4 e 8 vão falhar"
fi

# ═════════════════════════ [4] Diretórios persistentes ═════════════════════════
# Antes do fail2ban: cria infra/ftp-logs/ftp-auth.log que o jail nexus-ftp
# tail-a no startup (sem o file, fail2ban 1.1.0 falha o startup).
if confirm_step "Diretórios persistentes (infra/ftp-logs e infra/state)" \
"  Cria:
    $REPO_DIR/infra/ftp-logs/ftp-auth.log  (log de auth FTP/SFTP que o
        fail2ban tail-a — se não existir, fail2ban 1.1.0 dá erro fatal
        no startup ao tentar abrir o file)
    $REPO_DIR/infra/state/                 (host key SFTP gerada no 1o
        start; se perder, equipamentos acusam fingerprint diferente)"; then

  mkdir -p "$REPO_DIR/infra/ftp-logs" "$REPO_DIR/infra/state"
  touch "$REPO_DIR/infra/ftp-logs/ftp-auth.log"
  chmod 644 "$REPO_DIR/infra/ftp-logs/ftp-auth.log"
  ok "infra/ftp-logs e infra/state criados"
fi

# ═════════════════════════ [5] Chrony NTP ═════════════════════════
if confirm_step "Chrony NTP server (allow RFC1918+RFC6598 + ratelimit)" \
"  Adiciona ao /etc/chrony/chrony.conf:
    - allow 10/8, 172.16/12, 192.168/16, 100.64/10 (CGNAT)
    - allow para cada CIDR em NEXUS_EXTRA_CIDRS (env var, opcional)
    - ratelimit interval 1 burst 16 leak 2  (anti-abuso)
  NEXUS_EXTRA_CIDRS atual: '${NEXUS_EXTRA_CIDRS:-(vazio)}'
  Pra adicionar faixas customizadas, rode com:
    NEXUS_EXTRA_CIDRS=200.150.30.0/24,45.7.68.0/22 bash $0
  Não remove pool/server existentes — só anexa as adições."; then

  CHRONY_CONF='/etc/chrony/chrony.conf'

  # Remove o bloco antigo do NEXUS BACKUP se existir (idempotente)
  if grep -q '# === NEXUS BACKUP — adições do install-nexus-backup' "$CHRONY_CONF" 2>/dev/null; then
    cp "$CHRONY_CONF" "${CHRONY_CONF}.bak.$(date +%Y%m%d-%H%M%S)"
    sed -i '/# === NEXUS BACKUP — adições/,/^ratelimit interval 1 burst 16 leak 2/d' "$CHRONY_CONF"
    note "bloco anterior do NEXUS BACKUP removido (será reescrito)"
  fi

  cat >> "$CHRONY_CONF" <<'EOF'

# === NEXUS BACKUP — adições do install-nexus-backup.sh ===
# RFC1918 (redes privadas)
allow 10.0.0.0/8
allow 172.16.0.0/12
allow 192.168.0.0/16
# RFC6598 (CGNAT)
allow 100.64.0.0/10
EOF
  # Faixas adicionais via env var
  if [ -n "${NEXUS_EXTRA_CIDRS:-}" ]; then
    echo "# Faixas adicionais desta instalação (NEXUS_EXTRA_CIDRS)" >> "$CHRONY_CONF"
    IFS=',' read -ra CIDRS <<< "$NEXUS_EXTRA_CIDRS"
    for cidr in "${CIDRS[@]}"; do
      cidr="${cidr// /}"  # trim spaces
      [ -z "$cidr" ] && continue
      # Valida o formato a.b.c.d/nn — CIDR malformado aqui também quebraria o UFW no passo 7.
      if [[ "$cidr" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]{1,2}$ ]]; then
        echo "allow $cidr" >> "$CHRONY_CONF"
      else
        warn "NEXUS_EXTRA_CIDRS: CIDR inválido ignorado (chrony): '$cidr'"
      fi
    done
  fi
  cat >> "$CHRONY_CONF" <<'EOF'
# Anti-abuso: até 16 reqs em rajada por IP, 1/s sustentado
ratelimit interval 1 burst 16 leak 2
EOF
  # NTP não é crítico pro stack subir — se o conf ficar inválido não aborta o install (set -e).
  systemctl restart chrony || warn "chrony não reiniciou — revise $CHRONY_CONF"
  ok "chrony.conf atualizado + restart"
  note "  $(grep -cE '^allow' "$CHRONY_CONF") faixas allow ativas"
fi

# ═════════════════════════ [6] Fail2ban ═════════════════════════
if confirm_step "Fail2ban (DOCKER-USER chain pro container FTP)" \
"  Cria 3 arquivos:
    /etc/fail2ban/action.d/docker-allports.conf  (action que insere DROP
        em DOCKER-USER chain — única jeito de banir tráfego que vai pro
        container Docker; banaction=ufw NÃO funciona pra esses ports.
        IMPORTANTE: action declara explicitamente
            iptables = /usr/sbin/iptables
        no bloco [Init] — em Debian 13 Trixie o iptables-common.conf não
        vem mais com o pacote fail2ban 1.1.0 e <iptables> ficaria literal.)
    /etc/fail2ban/filter.d/nexus-ftp.conf        (regex que casa
        '[WARNING] AUTH_FAIL ip=...' do log do backend.)
    /etc/fail2ban/jail.local                     (jails: sshd na 2288 +
        nexus-ftp tail-ando /root/NEXUS-BETA/infra/ftp-logs/ftp-auth.log)
  jail nexus-ftp: maxretry=3, findtime=10m, bantime=24h."; then

  # Restart do fail2ban é feito UMA vez no fim, se QUALQUER um dos 3 arquivos mudar.
  F2B_CHANGED=0

  F2B_ACTION='/etc/fail2ban/action.d/docker-allports.conf'
  # Atualiza se action não existe OU se falta o bloco [Init] (versão antiga)
  if [ ! -f "$F2B_ACTION" ] || ! grep -q '^\[Init\]' "$F2B_ACTION"; then
    cat > "$F2B_ACTION" <<'EOF'
# Action pra serviços PUBLICADOS via Docker (FTP/SFTP/TFTP push).
# Tráfego destinado a container Docker passa pelo FORWARD via chain DOCKER,
# NUNCA tocando INPUT — banaction=ufw NÃO funciona. Esta action insere a
# chain f2b-<name> dentro de DOCKER-USER (chamada antes do roteamento pro
# container). É o padrão recomendado pelo próprio Docker:
#   https://docs.docker.com/network/packet-filtering-firewalls/#docker-on-iptables

[Definition]
actionstart = <iptables> -N f2b-<name> 2>/dev/null || true
              <iptables> -A f2b-<name> -j RETURN
              <iptables> -I DOCKER-USER -j f2b-<name>
actionstop  = <iptables> -D DOCKER-USER -j f2b-<name>
              <iptables> -F f2b-<name>
              <iptables> -X f2b-<name>
actioncheck = <iptables> -n -L DOCKER-USER | grep -q 'f2b-<name>'
actionban   = <iptables> -I f2b-<name> 1 -s <ip> -j DROP
actionunban = <iptables> -D f2b-<name> -s <ip> -j DROP

[Init]
# Define o path do binário explicitamente — em Debian 13 Trixie não há mais
# iptables-common.conf no pacote fail2ban 1.1.0. /usr/sbin/iptables é symlink
# pra iptables-nft via update-alternatives.
iptables = /usr/sbin/iptables
EOF
    ok "action docker-allports criada/atualizada (com [Init])"
    F2B_CHANGED=1
  else
    skip "action docker-allports (já tem [Init])"
  fi

  F2B_FILTER='/etc/fail2ban/filter.d/nexus-ftp.conf'
  if [ ! -f "$F2B_FILTER" ]; then
    cat > "$F2B_FILTER" <<'EOF'
# Filter pro FTP/SFTP server do NEXUS BACKUP.
# fail2ban remove o timestamp antes de aplicar a regex, então a linha vista
# começa direto em [WARNING] / [ERROR].
[Definition]
failregex = ^\s*\[(WARNING|ERROR)\] AUTH_FAIL ip=<HOST> .*$
ignoreregex =
EOF
    ok "filter nexus-ftp criado"
    F2B_CHANGED=1
  else
    skip "filter nexus-ftp"
  fi

  F2B_JAIL='/etc/fail2ban/jail.local'
  if [ ! -f "$F2B_JAIL" ] || ! grep -q '\[nexus-ftp\]' "$F2B_JAIL"; then
    cat > "$F2B_JAIL" <<EOF
[DEFAULT]
bantime  = 1h
findtime = 10m
maxretry = 5
ignoreip = 127.0.0.1/8 ::1 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16
backend  = systemd

[sshd]
enabled  = true
port     = 2288

[nexus-ftp]
enabled  = true
filter   = nexus-ftp
logpath  = $REPO_DIR/infra/ftp-logs/ftp-auth.log
backend  = polling
# Apertado vs default — esse log atrai brute-force pesado.
maxretry = 3
findtime = 10m
bantime  = 24h
banaction = docker-allports
EOF
    ok "jail.local escrito"
    F2B_CHANGED=1
  else
    skip "jail.local (já tem [nexus-ftp])"
  fi

  # Restart UMA vez se QUALQUER dos 3 arquivos (action/filter/jail) mudou — NÃO só o jail.
  # Num re-run/upgrade que reescreve só a action (ex.: pra adicionar o [Init]), o fix
  # ficaria no disco mas dormente (action quebrada segue em memória). Mesmo padrão do
  # NEEDS_RESTART do passo 2 (Docker).
  if [ "${F2B_CHANGED:-0}" = "1" ]; then
    systemctl enable --now fail2ban >/dev/null 2>&1
    systemctl restart fail2ban
    sleep 2
    if systemctl is-active --quiet fail2ban; then
      ok "fail2ban recarregado (active)"
    else
      warn "fail2ban falhou ao subir — checa: journalctl -u fail2ban --no-pager -n 20"
    fi
  else
    skip "fail2ban (nenhum arquivo mudou — sem restart)"
  fi
fi

# ═════════════════════════ [7] UFW ═════════════════════════
if confirm_step "UFW firewall (regras + enable)" \
"  Política: default deny incoming / allow outgoing.
  Allows: 2288/tcp (SSH host), 80/443 (web), 21/22/69 (push FTP/SFTP/TFTP),
  30000-30099 tcp+udp (PASV + TFTP efêmera), 123/udp (NTP) só pra
  RFC1918+RFC6598 + NEXUS_EXTRA_CIDRS.
  Importante: portas Docker-publicadas (21/22/69/30000-30099) bypassam UFW
  na prática — quem protege é fail2ban via DOCKER-USER. Estes allows
  servem como documentação + cobertura caso o host bind diretamente."; then

  ufw_add() {
    local rule_check="$1" rule_add="$2" comment="${3:-}"
    if ufw status | grep -qE "$rule_check"; then
      skip "ufw: $rule_add"
      return 0
    fi
    # Captura stdout+stderr pra detectar "ERROR: Invalid syntax" (que UFW emite
    # quando o comment tem caracteres rejeitados — apóstrofe, colchete, etc.).
    # Antes deste fix (pre-v2.1.6), o script ignorava o erro e seguia em frente,
    # deixando regras NÃO criadas no firewall — falha silenciosa.
    local out
    if [ -n "$comment" ]; then
      out="$(ufw allow "$rule_add" comment "$comment" 2>&1)"
    else
      out="$(ufw allow "$rule_add" 2>&1)"
    fi
    if echo "$out" | grep -qi 'ERROR\|invalid'; then
      fail "ufw allow '$rule_add' falhou: $out"
    fi
    ok "ufw: $rule_add ${comment:+($comment)}"
  }

  ufw --force default deny incoming >/dev/null
  ufw --force default allow outgoing >/dev/null

  # IMPORTANTE: comments do UFW são parseados em modo strict em versões modernas
  # (Debian 13 / UFW 0.36.2+). Caracteres `'` (apóstrofe), `[`, `]` e às vezes
  # `(` `)` quebram com "ERROR: Invalid syntax" — não chega nem a registrar a
  # regra. Mantemos os comments aqui apenas com alfanuméricos + espaço + hífen
  # + barra (que UFW aceita sempre). Detectado em prod 2026-05-15.
  ufw_add '2288/tcp' '2288/tcp' 'SSH host - fail2ban protege sshd jail'
  ufw_add '80/tcp'   '80/tcp'   'HTTP - Lets Encrypt e redirect'
  ufw_add '443/tcp'  '443/tcp'  'HTTPS painel'
  ufw_add '21/tcp'   '21/tcp'   'FTP control - backup push'
  ufw_add '22/tcp'   '22/tcp'   'SFTP push - porta padrao container'
  ufw_add '69/udp'   '69/udp'   'TFTP push'
  ufw_add '30000:30099/tcp' '30000:30099/tcp' 'FTP passive'
  ufw_add '30000:30099/udp' '30000:30099/udp' 'TFTP passive UDP'

  for cidr in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10; do
    if ! ufw status | grep -E '123/udp' | grep -q "$cidr"; then
      # Sem parênteses no comment — UFW Trixie rejeita
      ufw allow from "$cidr" to any port 123 proto udp comment "NTP server $cidr RFC1918 RFC6598" >/dev/null
      ok "ufw: NTP allow $cidr"
    fi
  done

  if [ -n "${NEXUS_EXTRA_CIDRS:-}" ]; then
    IFS=',' read -ra CIDRS <<< "$NEXUS_EXTRA_CIDRS"
    for cidr in "${CIDRS[@]}"; do
      cidr="${cidr// /}"  # trim spaces
      [ -z "$cidr" ] && continue
      # Valida ANTES de tocar no UFW: 'ufw allow from <CIDR_ruim>' sai !=0 e, sob set -e,
      # abortaria o passo 7 com o firewall ainda DESABILITADO (antes do enable) + sem .env.
      if ! [[ "$cidr" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]{1,2}$ ]]; then
        warn "ufw: CIDR inválido em NEXUS_EXTRA_CIDRS, ignorado: '$cidr'"
        continue
      fi
      if ! ufw status | grep -E '123/udp' | grep -q "$cidr"; then
        if ufw allow from "$cidr" to any port 123 proto udp comment "NTP server $cidr extra" >/dev/null 2>&1; then
          ok "ufw: NTP allow $cidr (extra)"
        else
          warn "ufw: falhou ao adicionar NTP allow '$cidr' — ignorado"
        fi
      fi
    done
  fi

  if ! ufw status | grep -q 'Status: active'; then
    # Em Debian 13 Trixie com nftables backend, o ufw service precisa estar
    # habilitado no systemd ANTES do `ufw enable` — senão o enable retorna OK
    # mas o serviço fica `inactive (dead)` após reboot. Sequência defensiva:
    systemctl enable ufw >/dev/null 2>&1 || true
    # `ufw --force enable` (NÃO `yes | ufw enable`): sob `set -euo pipefail`, quando o
    # ufw fecha o stdin após ler a confirmação, o `yes` leva SIGPIPE e o pipeline retorna
    # 141 → o script abortava logo após ativar o UFW, pulando os passos 8 (.env) e 9 (cron).
    # `--force` já dispensa o prompt, sem precisar do pipe.
    ufw --force enable >/dev/null
    sleep 1
    # Pós-check explícito — `ufw enable` pode reportar sucesso mas o serviço
    # acabar morto. Valida o estado real.
    if ufw status | grep -q 'Status: active' && systemctl is-active --quiet ufw; then
      ok "ufw enable + service active"
    else
      warn "UFW reportou enable mas não ficou active. Tente manualmente:"
      warn "  systemctl enable --now ufw && ufw --force enable"
      warn "Se persistir, verifique se nftables/iptables backend está OK:"
      warn "  update-alternatives --display iptables  (espera-se iptables-nft)"
    fi
  else
    skip "ufw já ativo"
  fi
fi

# ═════════════════════════ [8] .env ═════════════════════════
if confirm_step "Gera .env com secrets aleatórios + placeholders" \
"  Cria $REPO_DIR/.env com:
    - SECRET_KEY (openssl rand -hex 64)             ← gerado
    - ENCRYPTION_KEY (Fernet base64 url-safe)       ← gerado
    - DB_EXPORT_KEY (Fernet base64 url-safe)        ← gerado (v2.0.0 .nxbak)
    - POSTGRES_PASSWORD (32 chars alfanuméricos)    ← gerado
    - FTP_MASQUERADE_ADDRESS                        ← auto IP público${DETECTED_PUBLIC_IP:+ ($DETECTED_PUBLIC_IP)}
    - DOMAIN, CERTBOT_EMAIL                         ← VOCÊ EDITA
    - GITHUB_TOKEN/GITHUB_REPO                      ← opcional (banner update)
  chmod 600 .env. Se já existir, faz backup em .env.bak.<ts> e NÃO
  sobrescreve (preserva secrets antigas — perder ENCRYPTION_KEY ou
  DB_EXPORT_KEY = dados criptografados viram lixo)."; then

  cd "$REPO_DIR" 2>/dev/null || fail "REPO_DIR $REPO_DIR não existe"

  if [ -f .env ]; then
    # NÃO sobrescreve .env existente (perder ENCRYPTION_KEY/DB_EXPORT_KEY = dados viram
    # lixo). Mantém UM backup rolante chmod 600 — NÃO timestamp a cada re-run, senão
    # acumularia N cópias dos secrets em texto puro (e cp herda 644, não 600).
    cp .env .env.bak && chmod 600 .env.bak
    skip ".env (já existe — backup em .env.bak [600], NÃO sobrescrevo)"
  else
    SECRET_KEY="$(openssl rand -hex 64)"
    # Fernet expects 32 bytes URL-safe base64 — equivalent to Fernet.generate_key()
    ENCRYPTION_KEY="$(openssl rand -base64 32 | tr '+/' '-_')"
    DB_EXPORT_KEY="$(openssl rand -base64 32 | tr '+/' '-_')"
    POSTGRES_PASSWORD="$(openssl rand -base64 32 | tr -d '/+=' | cut -c1-32)"

    # FTP_MASQUERADE_ADDRESS: auto-preenche com IP público detectado no pré-flight.
    # Se a detecção falhou (DETECTED_PUBLIC_IP vazio), deixa vazio pra editar manual.
    # Cenários onde o auto pode estar errado e precisa correção manual:
    #   - VM atrás de NAT com VPN: clientes acessam pelo IP da VPN, não pelo público
    #   - Múltiplos IPs públicos no servidor: pode pegar o "errado"
    if [ -n "$DETECTED_PUBLIC_IP" ]; then
      FTP_MASQ_VALUE="$DETECTED_PUBLIC_IP"
      FTP_MASQ_NOTE="# Auto-detectado via api.ipify.org no install. Mude se clientes acessam por outro IP (VPN/NAT)."
    else
      FTP_MASQ_VALUE=""
      FTP_MASQ_NOTE="# [!] Detecção automática falhou — preencher manualmente com o IP que os clientes usam."
    fi

    cat > .env <<EOF
# Gerado por install-nexus-backup.sh em $(date -Iseconds)
# Edite os campos marcados com [!] ANTES de rodar init-letsencrypt.sh.

# === Domínio público + Let's Encrypt ===
# [!] DNS deste DOMAIN deve resolver pra IP público deste servidor antes do certbot
DOMAIN=backup.exemplo.com.br
CERTBOT_EMAIL=admin@exemplo.com.br

# === Banco de dados (gerado, NÃO trocar depois — perde acesso ao banco) ===
POSTGRES_DB=dbnexus
POSTGRES_USER=nexus
POSTGRES_PASSWORD=$POSTGRES_PASSWORD

# === Backend (gerados — guarde-os offline) ===
SECRET_KEY=$SECRET_KEY
# IMPORTANTE: se trocar ENCRYPTION_KEY depois, todas as senhas SSH salvas
# ficam ilegíveis. Backup desta chave + .env num cofre.
# Formato Fernet = 32 bytes em base64 url-safe.
ENCRYPTION_KEY=$ENCRYPTION_KEY
# Chave Fernet DEDICADA pro .nxbak (snapshot diário criptografado do banco,
# feature v2.0.0). Separada da ENCRYPTION_KEY de propósito — pode rotacionar
# uma sem invalidar a outra. Guarde em local SEPARADO do servidor — sem ela
# os arquivos .nxbak são irrecuperáveis, nem pela ferramenta externa de leitura.
DB_EXPORT_KEY=$DB_EXPORT_KEY
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=480
BACKUP_RETENTION_DAYS=7
TZ=America/Sao_Paulo

# === FTP PASV masquerade ===
# IP que o pyftpdlib anuncia em respostas PASV. Sem isso, container anuncia
# 10.18.x.x e clientes externos ficam com transferência travada.
$FTP_MASQ_NOTE
FTP_MASQUERADE_ADDRESS=$FTP_MASQ_VALUE

# === Checagem de versão (banner de update no painel) ===
# Opcional — vazio = banner desabilitado. Crie um PAT em
# https://github.com/settings/tokens (scope "repo" read) e cole aqui.
GITHUB_TOKEN=
GITHUB_REPO=vagnerss2011-spec/NEXUS-BETA
EOF
    chmod 600 .env
    ok ".env criado em $REPO_DIR/.env (chmod 600)"
    if [ -n "$DETECTED_PUBLIC_IP" ]; then
      warn "EDITE: DOMAIN e CERTBOT_EMAIL antes de seguir (FTP_MASQUERADE_ADDRESS já auto-preenchido como $DETECTED_PUBLIC_IP — revise se está certo pra seus clientes)"
    else
      warn "EDITE: DOMAIN, CERTBOT_EMAIL e FTP_MASQUERADE_ADDRESS antes de seguir"
    fi

    # Validação rápida do DOMAIN — só checa se o placeholder ainda está lá.
    # Regex de FQDN é difícil de acertar bem; aqui só evita o erro mais comum
    # (esquecer de editar e ficar com backup.exemplo.com.br).
    note "Lembre: o certbot vai falhar se o DOMAIN não estiver editado e resolvendo via DNS."
  fi
fi

# ═════════════════════════ [9] cron pg-backup.sh (opcional) ═════════════════════════
if confirm_step "Cron diário do pg-backup.sh (OPCIONAL)" \
"  Instala /etc/cron.d/nexus-pg-backup pra rodar pg_dump todo dia
  às 03:30, retendo 14 dias em /var/backups/nexus-postgres/.
  Por que separado do .nxbak (v2.0.0): o .nxbak protege os
  backups COLETADOS (config dos devices); o pg-backup.sh protege
  os METADADOS do app (users, devices, log_scheduler, OperacaoMassaLog
  etc.) — são fluxos complementares.

  Esta é OPCIONAL: o app funciona sem. Se a VM tiver snapshot do
  hipervisor cobrindo o disco do banco, dá pra pular este passo."; then

  CRON_FILE='/etc/cron.d/nexus-pg-backup'
  if [ -f "$CRON_FILE" ]; then
    skip "cron pg-backup (já instalada em $CRON_FILE)"
  else
    # Cron exige fim de arquivo com newline + permissão 644 + dono root
    cat > "$CRON_FILE" <<EOF
# NEXUS BACKUP — dump diário do Postgres às 03:30 (após scheduler 02:00).
# Retém 14 dias por default. Log em /var/log/nexus-pg-backup.log.
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
30 3 * * * root cd $REPO_DIR && ./scripts/pg-backup.sh >> /var/log/nexus-pg-backup.log 2>&1
EOF
    chmod 644 "$CRON_FILE"
    touch /var/log/nexus-pg-backup.log
    chmod 644 /var/log/nexus-pg-backup.log
    ok "cron instalada em $CRON_FILE (próxima execução: amanhã 03:30)"
    note "  Restore: docker compose exec -T db pg_restore -U \$POSTGRES_USER -d \$POSTGRES_DB --clean --if-exists < <dump_file>"
  fi
fi

# ═════════════════════════ done ═════════════════════════
APP_VERSION="$(grep -oE '[0-9]+\.[0-9]+\.[0-9]+' "$REPO_DIR/frontend/package.json" 2>/dev/null | head -1 || echo '?')"

printf '\n%s======================================================================%s\n' "$C_BOLD$C_GREEN" "$C_RESET"
printf '%s Bootstrap concluído (versão alvo: v%s)%s\n' "$C_BOLD$C_GREEN" "$APP_VERSION" "$C_RESET"
printf '%s======================================================================%s\n\n' "$C_BOLD$C_GREEN" "$C_RESET"

cat <<EOF
Próximos passos manuais (ver docs/INSTALL.md §5 em diante):

  1. Editar .env e preencher os 3 campos com [!]:
     nano $REPO_DIR/.env

  2. Confirmar que DNS resolve antes do certbot:
     dig +short \$(grep ^DOMAIN= $REPO_DIR/.env | cut -d= -f2)

  3. Tirar certificado Let's Encrypt (testar STAGING primeiro):
     cd $REPO_DIR
     STAGING=1 ./init-letsencrypt.sh   # validação
     ./init-letsencrypt.sh             # real

  4. Subir o stack:
     docker compose up -d
     docker compose ps                  # tudo Up + db Healthy

  5. Criar primeiro admin (ver docs/INSTALL.md §8 — Python no container)

  6. Validar:
     curl -sk https://\$DOMAIN/ | grep -oE 'v[0-9.]+'   # deve mostrar v$APP_VERSION

EOF

if [ "$INTERACTIVE" = "0" ]; then
  note "Dica: pra revisar passo-a-passo numa próxima execução, rode:"
  note "  bash $0 --interactive"
fi
