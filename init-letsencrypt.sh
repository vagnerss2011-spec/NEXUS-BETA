#!/usr/bin/env bash
# Bootstrap do certificado Let's Encrypt para o frontend nginx.
# Uso:
#   1. Preencha .env na raiz com DOMAIN e CERTBOT_EMAIL.
#   2. Aponte o DNS do DOMAIN para o IP público deste servidor.
#   3. Libere as portas 80 e 443 no firewall / router.
#   4. ./init-letsencrypt.sh
#
# Em modo staging (recomendado no primeiro teste, evita rate-limit do LE):
#   STAGING=1 ./init-letsencrypt.sh

set -euo pipefail

if [ ! -f .env ]; then
  echo "[erro] arquivo .env não encontrado na raiz — copie de .env.example e edite."
  exit 1
fi
# shellcheck disable=SC1091
source .env
: "${DOMAIN:?DOMAIN não definido em .env}"
: "${CERTBOT_EMAIL:?CERTBOT_EMAIL não definido em .env}"

# DOMAIN entra em paths e argumentos do Docker/openssl. Além de dar um erro
# mais claro para placeholders, esta validação impede barras e metacaracteres.
if [[ ! "$DOMAIN" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$ ]]; then
  echo "[erro] DOMAIN inválido: '$DOMAIN'" >&2
  exit 1
fi

for required_command in curl sha256sum docker openssl; do
  if ! command -v "$required_command" >/dev/null 2>&1; then
    echo "[erro] comando obrigatório não encontrado: $required_command" >&2
    exit 1
  fi
done
if ! docker compose version >/dev/null 2>&1; then
  echo "[erro] Docker Compose v2 não está disponível (comando: docker compose)." >&2
  exit 1
fi

DATA_PATH="./infra/certbot"
CONF_PATH="$DATA_PATH/conf"
RSA_KEY_SIZE=4096
# Os arquivos TLS eram baixados de um path interno da branch `master` do
# Certbot. O projeto migrou para `main` e reorganizou o pacote em 2026, então
# aquele URL passou a responder 404 e bloqueou toda instalação nova.
#
# Fixamos o commit da release Certbot v5.7.0 para outra reorganização da branch
# principal (ou uma tag movida por engano) não quebrar o bootstrap. Para testar
# outra revisão, informe também os 2 checksums esperados via ambiente.
CERTBOT_TLS_RELEASE="v5.7.0"
CERTBOT_TLS_REF="${CERTBOT_TLS_REF:-e75e7378cd024d74d18a22c66d5aea32abd474c7}"
CERTBOT_TLS_BASE="https://raw.githubusercontent.com/certbot/certbot/${CERTBOT_TLS_REF}/certbot/src/certbot"
OPTIONS_SSL_URL="${OPTIONS_SSL_URL:-${CERTBOT_TLS_BASE}/_internal/plugins/nginx/tls_configs/options-ssl-nginx.conf}"
SSL_DHPARAMS_URL="${SSL_DHPARAMS_URL:-${CERTBOT_TLS_BASE}/ssl-dhparams.pem}"
OPTIONS_SSL_SHA256="${OPTIONS_SSL_SHA256:-5e21cc66989f26ec46116d979421e538131cf8ab33ffff3f682fbfe491b0ace8}"
SSL_DHPARAMS_SHA256="${SSL_DHPARAMS_SHA256:-9ba6429597aeed2d8617a7705b56e96d044f64b07971659382e426675105654b}"
LE_PRODUCTION_SERVER="https://acme-v02.api.letsencrypt.org/directory"
LE_STAGING_SERVER="https://acme-staging-v02.api.letsencrypt.org/directory"
STAGING_MODE=0
if [ "${STAGING:-0}" = "1" ]; then
  STAGING_MODE=1
  echo "[aviso] modo STAGING habilitado — o desafio será testado sem salvar certificado."
fi

# Download atômico: curl escreve num temporário e só substitui o destino depois
# de confirmar sucesso + conteúdo não vazio. Isso também recupera instalações
# afetadas pelo 404 antigo, que deixou options-ssl-nginx.conf com zero bytes.
download_tls_asset() {
  local url="$1"
  local dest="$2"
  local expected_sha256="$3"
  local tmp
  local actual_sha256
  tmp="$(mktemp "${dest}.tmp.XXXXXX")"

  if ! curl -fsSL --retry 3 --retry-delay 2 --retry-all-errors "$url" -o "$tmp"; then
    rm -f "$tmp"
    echo "[erro] não foi possível baixar o parâmetro TLS: $url" >&2
    return 1
  fi
  if [ ! -s "$tmp" ]; then
    rm -f "$tmp"
    echo "[erro] o parâmetro TLS baixado veio vazio: $url" >&2
    return 1
  fi
  actual_sha256="$(sha256sum "$tmp" | awk '{print $1}')"
  if [ "$actual_sha256" != "$expected_sha256" ]; then
    rm -f "$tmp"
    echo "[erro] checksum inválido para o parâmetro TLS: $url" >&2
    echo "       esperado=$expected_sha256 recebido=$actual_sha256" >&2
    return 1
  fi

  chmod 0644 "$tmp"
  mv -f "$tmp" "$dest"
}

ensure_tls_asset() {
  local url="$1"
  local dest="$2"
  local expected_sha256="$3"
  local actual_sha256=""

  if [ -s "$dest" ]; then
    actual_sha256="$(sha256sum "$dest" | awk '{print $1}')"
  fi
  if [ "$actual_sha256" = "$expected_sha256" ]; then
    return 0
  fi
  if [ -e "$dest" ]; then
    echo "### parâmetro TLS ausente, vazio ou divergente; restaurando $(basename "$dest")..."
  fi
  download_tls_asset "$url" "$dest" "$expected_sha256"
}

run_certbot() {
  docker compose run --rm --no-deps -T --entrypoint certbot certbot "$@"
}

# Confirma que o certificado é legível, pertence ao domínio e casa com a chave.
# A validade temporal não entra aqui: um cert expirado ainda permite subir o
# nginx e renovar sem substituir o lineage por um dummy.
certificate_pair_is_usable() {
  local scope="$1"
  local host_dir="$CONF_PATH/$scope/$DOMAIN"
  local cert_public_key
  local private_public_key

  [ -s "$host_dir/fullchain.pem" ] && [ -s "$host_dir/privkey.pem" ] || return 1
  openssl x509 -in "$host_dir/fullchain.pem" \
    -noout -checkhost "$DOMAIN" >/dev/null 2>&1 || return 1
  cert_public_key="$(openssl x509 -in "$host_dir/fullchain.pem" \
    -noout -pubkey 2>/dev/null)" || return 1
  private_public_key="$(openssl pkey -in "$host_dir/privkey.pem" \
    -pubout 2>/dev/null)" || return 1
  [ -n "$cert_public_key" ] && [ "$cert_public_key" = "$private_public_key" ]
}

# O projeto gerencia um certificado de domínio único. Um lineage manual com
# SANs adicionais é preservado e exige decisão humana, pois passar apenas -d
# DOMAIN ao Certbot removeria silenciosamente os outros nomes na renovação.
live_certificate_has_exact_domain_set() {
  local san_entries
  local expected_domain
  expected_domain="$(printf '%s' "$DOMAIN" | tr '[:upper:]' '[:lower:]')"
  san_entries="$(openssl x509 -in "$CONF_PATH/live/$DOMAIN/fullchain.pem" \
    -noout -ext subjectAltName 2>/dev/null |
    sed '1d' |
    tr -d '[:space:]' |
    tr '[:upper:]' '[:lower:]')" || return 1
  [ "$san_entries" = "dns:$expected_domain" ]
}

lineage_state_exists() {
  [ -e "$CONF_PATH/live/$DOMAIN" ] || [ -L "$CONF_PATH/live/$DOMAIN" ] ||
    [ -e "$CONF_PATH/archive/$DOMAIN" ] || [ -L "$CONF_PATH/archive/$DOMAIN" ] ||
    [ -e "$CONF_PATH/renewal/$DOMAIN.conf" ] || [ -L "$CONF_PATH/renewal/$DOMAIN.conf" ]
}

# Um lineage objetivamente incompleto não pode impedir uma instalação nova. Ele
# é movido para uma pasta datada, nunca apagado. Pares completos mas inválidos,
# não gerenciados ou multi-SAN são preservados no lugar e causam abort seguro.
quarantine_broken_lineage() {
  local recovery_dir
  local relative_path
  recovery_dir="$DATA_PATH/recovery/$(date -u +%Y%m%dT%H%M%SZ)-$$"

  echo "[aviso] lineage TLS antigo/inválido detectado; preservando em $recovery_dir"
  for relative_path in "live/$DOMAIN" "archive/$DOMAIN" "renewal/$DOMAIN.conf"; do
    if [ -e "$CONF_PATH/$relative_path" ] || [ -L "$CONF_PATH/$relative_path" ]; then
      mkdir -p "$recovery_dir/$(dirname "$relative_path")"
      mv "$CONF_PATH/$relative_path" "$recovery_dir/$relative_path"
    fi
  done
}

ensure_bootstrap_certificate() {
  local bootstrap_dir="$CONF_PATH/bootstrap/$DOMAIN"
  local temp_name=".tmp-${DOMAIN}-$$"
  local temp_host_dir="$CONF_PATH/bootstrap/$temp_name"

  if certificate_pair_is_usable bootstrap; then
    return 0
  fi

  echo "### gerando certificado temporário isolado para $DOMAIN..."
  mkdir -p "$temp_host_dir" "$bootstrap_dir"
  if ! openssl req -x509 -nodes -newkey "rsa:$RSA_KEY_SIZE" -days 7 \
    -keyout "$temp_host_dir/privkey.pem" \
    -out "$temp_host_dir/fullchain.pem" \
    -subj "/CN=$DOMAIN" \
    -addext "subjectAltName=DNS:$DOMAIN"; then
    rm -f "$temp_host_dir/privkey.pem" "$temp_host_dir/fullchain.pem"
    rmdir "$temp_host_dir" 2>/dev/null || true
    return 1
  fi
  chmod 0600 "$temp_host_dir/privkey.pem"
  chmod 0644 "$temp_host_dir/fullchain.pem"
  mv -f "$temp_host_dir/privkey.pem" "$bootstrap_dir/privkey.pem"
  mv -f "$temp_host_dir/fullchain.pem" "$bootstrap_dir/fullchain.pem"
  rmdir "$temp_host_dir"
}

CHALLENGE_FILE=""
cleanup_challenge() {
  if [ -n "$CHALLENGE_FILE" ]; then
    rm -f "$CHALLENGE_FILE"
  fi
}
trap cleanup_challenge EXIT

wait_for_acme_webroot() {
  local challenge_dir="$DATA_PATH/www/.well-known/acme-challenge"
  local token_name
  local expected="nexus-acme-ready-$DOMAIN-$$"
  local response=""
  local attempt
  token_name="nexus-readiness-$(date +%s)-$$"

  mkdir -p "$challenge_dir"
  CHALLENGE_FILE="$challenge_dir/$token_name"
  printf '%s' "$expected" > "$CHALLENGE_FILE"

  for ((attempt = 1; attempt <= 30; attempt++)); do
    if response="$(curl --noproxy '*' -fsS --max-time 2 \
      -H "Host: $DOMAIN" \
      "http://127.0.0.1/.well-known/acme-challenge/$token_name" 2>/dev/null)" &&
      [ "$response" = "$expected" ]; then
      cleanup_challenge
      CHALLENGE_FILE=""
      return 0
    fi
    sleep 1
  done

  cleanup_challenge
  CHALLENGE_FILE=""
  echo "[erro] nginx não serviu o webroot ACME na porta 80 após 30 tentativas." >&2
  echo "       Verifique: docker compose ps; docker compose logs frontend --tail=100" >&2
  return 1
}

wait_for_https() {
  local attempt
  for ((attempt = 1; attempt <= 30; attempt++)); do
    if curl --noproxy '*' -fsS --max-time 4 \
      --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "[erro] HTTPS não respondeu com o certificado real após 30 tentativas." >&2
  return 1
}

# 1. Restaura parâmetros oficiais e também corrige o arquivo de zero bytes
# deixado pelo antigo curl com redirecionamento de saída.
mkdir -p "$CONF_PATH"
echo "### validando parâmetros TLS do Certbot ${CERTBOT_TLS_RELEASE}..."
ensure_tls_asset "$OPTIONS_SSL_URL" "$CONF_PATH/options-ssl-nginx.conf" "$OPTIONS_SSL_SHA256"
ensure_tls_asset "$SSL_DHPARAMS_URL" "$CONF_PATH/ssl-dhparams.pem" "$SSL_DHPARAMS_SHA256"

# 2. Decide qual certificado o nginx pode usar sem tocar num lineage válido.
CERT_SCOPE="bootstrap"
HAVE_LIVE_LINEAGE=0
LIVE_CERT="$CONF_PATH/live/$DOMAIN/fullchain.pem"
LIVE_KEY="$CONF_PATH/live/$DOMAIN/privkey.pem"
if [ -s "$LIVE_CERT" ] && [ -s "$LIVE_KEY" ]; then
  if certificate_pair_is_usable live; then
    CERT_SCOPE="live"
    if [ -s "$CONF_PATH/renewal/$DOMAIN.conf" ] &&
      live_certificate_has_exact_domain_set; then
      HAVE_LIVE_LINEAGE=1
    elif [ "$STAGING_MODE" = "1" ]; then
      echo "[aviso] certificado live válido preservado; o dry-run usará nome isolado."
    else
      echo "[erro] o certificado live é válido, mas não é um lineage Certbot" >&2
      echo "       gerenciado de domínio único. Nada foi movido ou apagado." >&2
      echo "       Revise os SANs e o arquivo renewal/$DOMAIN.conf." >&2
      exit 1
    fi
  elif [ "$STAGING_MODE" = "1" ]; then
    echo "[aviso] lineage existente não foi alterado; STAGING usará o bootstrap isolado."
  else
    echo "[erro] há um par certificado/chave completo, mas ele não pôde ser" >&2
    echo "       validado como lineage gerenciado de domínio único para $DOMAIN." >&2
    echo "       Nada foi movido ou apagado. Revise SAN, chave e renewal config." >&2
    exit 1
  fi
elif lineage_state_exists; then
  if [ "$STAGING_MODE" = "1" ]; then
    echo "[aviso] lineage parcial preservado; STAGING usará o bootstrap isolado."
  else
    quarantine_broken_lineage
  fi
fi
if [ "$CERT_SCOPE" = "bootstrap" ]; then
  ensure_bootstrap_certificate
fi

# 3. Sobe nginx e confirma localmente que o volume/webroot do challenge está
# correto. Se porta 80 estiver ocupada ou o container não ficar pronto, aborta
# antes de chamar o Let's Encrypt.
echo "### iniciando nginx com certificado $CERT_SCOPE..."
NEXUS_CERT_SCOPE="$CERT_SCOPE" docker compose up --build --force-recreate -d frontend
wait_for_acme_webroot

CERTBOT_ARGS=(
  certonly
  --webroot -w /var/www/certbot
  --email "$CERTBOT_EMAIL"
  -d "$DOMAIN"
  --preferred-challenges http
  --agree-tos
  --non-interactive
)

# 4. STAGING agora é um dry-run real: valida DNS/firewall/ACME sem gravar um
# certificado falso no mesmo lineage que depois receberá o de produção.
if [ "$STAGING_MODE" = "1" ]; then
  echo "### testando emissão no ambiente STAGING (nenhum certificado será salvo)..."
  run_certbot "${CERTBOT_ARGS[@]}" \
    --cert-name "${DOMAIN}-nexus-dry-run" --dry-run
  echo "### teste STAGING concluído. Agora rode: ./init-letsencrypt.sh"
  exit 0
fi

# Um lineage salvo por versões antigas em STAGING pode ser migrado sem ser
# apagado antes da nova emissão. Certbot troca os symlinks live só após sucesso.
PRODUCTION_ARGS=(--server "$LE_PRODUCTION_SERVER" --keep-until-expiring)
if [ "$HAVE_LIVE_LINEAGE" = "1" ]; then
  RENEWAL_SERVER="$(sed -n 's/^[[:space:]]*server[[:space:]]*=[[:space:]]*//p' \
    "$CONF_PATH/renewal/$DOMAIN.conf" | tail -n 1 | tr -d '\r')"
  RENEWAL_SERVER="${RENEWAL_SERVER%/}"
  if [ "$RENEWAL_SERVER" = "$LE_STAGING_SERVER" ]; then
    echo "[aviso] certificado STAGING legado detectado; migrando com segurança para produção."
    PRODUCTION_ARGS=(--server "$LE_PRODUCTION_SERVER" --force-renewal)
  elif [ "$RENEWAL_SERVER" != "$LE_PRODUCTION_SERVER" ]; then
    echo "[erro] servidor ACME desconhecido no renewal config: '$RENEWAL_SERVER'" >&2
    echo "       O lineage foi preservado sem alterações; revise-o manualmente." >&2
    exit 1
  fi
fi

echo "### solicitando/renovando certificado real via Let's Encrypt..."
run_certbot "${CERTBOT_ARGS[@]}" --cert-name "$DOMAIN" "${PRODUCTION_ARGS[@]}"

if ! certificate_pair_is_usable live || [ ! -s "$CONF_PATH/renewal/$DOMAIN.conf" ]; then
  echo "[erro] Certbot terminou sem deixar um lineage válido para $DOMAIN." >&2
  exit 1
fi

# Testa a configuração renderizada antes de trocar um frontend que já esteja
# funcionando. Depois recria com o scope live e confirma o HTTPS ponta a ponta.
echo "### validando e ativando o certificado real..."
NEXUS_CERT_SCOPE=live docker compose run --rm --no-deps -T frontend nginx -t
if ! NEXUS_CERT_SCOPE=live docker compose up --build --force-recreate -d frontend ||
  ! docker compose exec -T frontend nginx -t ||
  ! wait_for_https; then
  echo "[erro] falha ao ativar o frontend com o certificado real." >&2
  echo "       Tentando restaurar o escopo anterior: $CERT_SCOPE" >&2
  NEXUS_CERT_SCOPE="$CERT_SCOPE" \
    docker compose up --build --force-recreate -d frontend || true
  exit 1
fi

echo "### pronto! acesse: https://$DOMAIN"
