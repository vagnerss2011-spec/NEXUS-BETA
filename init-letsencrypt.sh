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

DATA_PATH="./infra/certbot"
RSA_KEY_SIZE=4096
STAGING_ARG=""
if [ "${STAGING:-0}" = "1" ]; then
  STAGING_ARG="--staging"
  echo "[aviso] modo STAGING habilitado — certificado NÃO será confiável pelo navegador."
fi

# 1. Baixa parâmetros recomendados (SSL options + dhparams), se ainda não existirem.
if [ ! -e "$DATA_PATH/conf/options-ssl-nginx.conf" ] || [ ! -e "$DATA_PATH/conf/ssl-dhparams.pem" ]; then
  echo "### baixando parâmetros TLS recomendados..."
  mkdir -p "$DATA_PATH/conf"
  curl -fsSL https://raw.githubusercontent.com/certbot/certbot/master/certbot-nginx/certbot_nginx/_internal/tls_configs/options-ssl-nginx.conf \
    > "$DATA_PATH/conf/options-ssl-nginx.conf"
  curl -fsSL https://ssl-config.mozilla.org/ffdhe2048.txt \
    > "$DATA_PATH/conf/ssl-dhparams.pem"
fi

# 2. Cria certificado dummy para o nginx conseguir subir na porta 443 antes do real existir.
LIVE_DIR="$DATA_PATH/conf/live/$DOMAIN"
if [ ! -e "$LIVE_DIR/fullchain.pem" ]; then
  echo "### gerando certificado dummy para $DOMAIN..."
  mkdir -p "$LIVE_DIR"
  docker compose run --rm --entrypoint "\
    openssl req -x509 -nodes -newkey rsa:$RSA_KEY_SIZE -days 1 \
      -keyout '/etc/letsencrypt/live/$DOMAIN/privkey.pem' \
      -out '/etc/letsencrypt/live/$DOMAIN/fullchain.pem' \
      -subj '/CN=localhost'" certbot
fi

# 3. Sobe o nginx com o cert dummy (para atender o ACME challenge no /.well-known).
echo "### iniciando nginx..."
docker compose up --force-recreate -d frontend

# 4. Remove o cert dummy e pede o real.
echo "### apagando cert dummy e solicitando o real via Let's Encrypt..."
docker compose run --rm --entrypoint "\
  rm -rf /etc/letsencrypt/live/$DOMAIN \
         /etc/letsencrypt/archive/$DOMAIN \
         /etc/letsencrypt/renewal/$DOMAIN.conf" certbot

docker compose run --rm --entrypoint "\
  certbot certonly --webroot -w /var/www/certbot \
    $STAGING_ARG \
    --email $CERTBOT_EMAIL \
    -d $DOMAIN \
    --rsa-key-size $RSA_KEY_SIZE \
    --agree-tos \
    --non-interactive \
    --force-renewal" certbot

# 5. Recarrega o nginx agora que os arquivos reais existem.
echo "### recarregando nginx..."
docker compose exec frontend nginx -s reload

echo "### pronto! acesse: https://$DOMAIN"
