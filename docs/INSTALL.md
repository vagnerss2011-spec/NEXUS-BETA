# Instalação do NEXUS BACKUP — Debian 13 (Trixie) em Proxmox

Guia ponta-a-ponta pra subir uma instância nova. Pareado com [scripts/install-nexus-backup.sh](../scripts/install-nexus-backup.sh): o script cobre §4–§5; o restante (provisionamento, SSH, DNS, certbot, primeiro admin) é manual e está aqui.

---

## §1 — Pré-requisitos

Antes de criar a VM, garante que tem:

- [ ] **Proxmox** com ISO Debian 13 (Trixie) baixada (`debian-13.x.x-amd64-netinst.iso`).
- [ ] **DNS** público apontando pro IP que a VM vai ter (ex.: `backup.cliente.com.br` → `IP_PUBLICO`). DNS precisa estar resolvendo **antes** de rodar o certbot. Testa com `dig backup.cliente.com.br +short` em outro host.
- [ ] **Roteador/firewall de borda** liberando entrada nas portas:

| Porta | Proto | Quem usa | De onde |
|---|---|---|---|
| 80, 443 | TCP | Painel + ACME | Internet |
| 21 | TCP | FTP push controle | IPs dos clientes |
| 22 | TCP | SFTP push | IPs dos clientes |
| 69 | UDP | TFTP push | IPs dos clientes |
| 30000-30099 | TCP+UDP | PASV FTP + efêmera TFTP | IPs dos clientes |
| 2288 | TCP | SSH host (admin) | seu IP de gestão |
| 123 | UDP | NTP server (opcional) | RFC1918+RFC6598 dos clientes |

> Dica: se o cliente vai pushar de IPs dinâmicos, libera no edge a faixa `/24` ou `/22` ao invés de host.

---

## §2 — Provisionar a VM no Proxmox

**VM, não LXC.** LXC funciona com Docker mas exige nesting + AppArmor + cgroup tweaks. VM é isolamento real e simples.

Configuração sugerida (até 300 devices):

| Recurso | Valor |
|---|---|
| CPU | 4 vCPU (host CPU type) |
| RAM | 4 GiB (sem ballooning) |
| Disco | 40 GiB (SCSI virtio, SSD-backed) |
| Rede | virtio bridge, **MAC fixo** |
| BIOS | OVMF (UEFI) ou SeaBIOS — tanto faz |
| Agent | habilitar QEMU Guest Agent |

Durante o install do Debian:
- Hostname: `nexus-backup-<cliente>` (sem espaços).
- Domain: vazio ou o que fizer sentido.
- **Sem desktop environment.** Marca só `SSH server` e `standard system utilities`.
- Particionamento: guided, todo o disco em uma partição (ext4). Sem swap separado em VM com 4GB+ é OK; ou 2GiB de swap se quiser segurança.
- Usuário comum: cria um (ex.: `vagner`), adiciona ao grupo `sudo` depois.
- Senha root: forte, anota num cofre.

Após boot:

```bash
# No console do Proxmox (login como root)
apt update && apt install -y sudo qemu-guest-agent
systemctl enable --now qemu-guest-agent
adduser vagner sudo   # ou o usuário que você criou
```

---

## §3 — Mover SSH pra 2288 (CRÍTICO — fazer pelo console)

O backend ouve SFTP push na porta 22 (OLTs/switches geralmente não aceitam porta SFTP custom). Por isso o SSH do host precisa sair da 22 antes do Docker subir.

**Faz pelo console do Proxmox**, não pelo SSH atual — se errar, fica trancado.

```bash
# Backup do sshd_config original
cp /etc/ssh/sshd_config /etc/ssh/sshd_config.orig

# Edita: descomenta/altera a linha Port
sed -i 's/^#\?Port .*/Port 2288/' /etc/ssh/sshd_config

# Valida config (não reinicia ainda — quer ver erro antes)
sshd -t && echo OK

# Aplica
systemctl restart ssh

# Confirma que escuta na 2288
ss -tlnp | grep :2288
```

**Validação de outro terminal (não fecha o console):**

```bash
ssh -p 2288 vagner@<IP_DA_VM>
```

Só fecha o console do Proxmox depois de confirmar que login na 2288 funciona.

> **Hardening adicional opcional:** depois de validar, em `/etc/ssh/sshd_config` desabilita `PasswordAuthentication no` e usa só chave pública. Faça SÓ depois de ter chave instalada.

---

## §4 — Rodar o script de bootstrap

Já dentro da VM (via SSH na 2288), como root. **Dois modos** — escolha um:

### Modo automático (rápido, 1 comando)

```bash
curl -fsSL https://raw.githubusercontent.com/vagnerss2011-spec/NEXUS-BETA/backup/scripts/install-nexus-backup.sh -o /tmp/install.sh
bash /tmp/install.sh
```

Roda os 8 passos em sequência sem perguntar nada. Bom quando você já confia no que o script faz e quer ir direto.

### Modo interativo (recomendado na primeira instalação)

```bash
curl -fsSL https://raw.githubusercontent.com/vagnerss2011-spec/NEXUS-BETA/backup/scripts/install-nexus-backup.sh -o /tmp/install.sh
bash /tmp/install.sh --interactive
```

Antes de cada passo, mostra um cabeçalho com:
- O número do passo (`Passo X/8 — Título`)
- Descrição do que vai fazer e **por quê**
- Pergunta `[s]im / [n]ão / [a]ll restantes / [q]uit`

Útil pra você acompanhar o que cada bloco faz (especialmente em ambientes que têm peculiaridades — interface de rede com nome diferente, conflito de IP, etc.). Pode usar `[a]` no meio do caminho pra parar de perguntar.

> **Idempotência:** ambos os modos são idempotentes — pode rodar várias vezes sem quebrar. Cada bloco verifica se já fez o trabalho e marca como pulado (`·`) se sim.

### Output que você vai ver

Cada passo emite uma das marcações:

| Símbolo | Significado |
|---|---|
| `✓` (verde) | Ação executada com sucesso |
| `·` (cinza) | Pulado porque já estava feito |
| `!` (amarelo) | Aviso — geralmente algo que precisa de atenção depois |
| `✗` (vermelho) | Erro — script aborta com `set -euo pipefail` |

O que ele faz, em ordem:
1. Locale `pt_BR.UTF-8` + timezone `America/Sao_Paulo`
2. Instala: git, ufw, fail2ban, chrony, ca-certificates, curl, gnupg, jq
3. Instala Docker Engine via repo oficial (não o do Debian — versão muito antiga)
4. Escreve `/etc/docker/daemon.json` com bip `10.17.0.1/24` + pool `10.18.0.0/16`
5. Adiciona `allow` + `ratelimit` no `chrony.conf`
6. Cria action `docker-allports`, filter `nexus-ftp`, jail `nexus-ftp` no fail2ban
7. Configura UFW (allow 2288/80/443/21/22/69/30000-30099 + 123/udp pra RFC1918)
8. Clona o repo em `/root/NEXUS-BETA` na tag mais recente
9. Cria `infra/ftp-logs/` e `infra/state/` (host key SFTP persiste aqui)
10. Gera `.env` com secrets aleatórios + 3 placeholders pra você editar

> Se você precisar recriar do zero, apaga `/etc/docker/daemon.json`, `/etc/fail2ban/jail.local`, `/root/NEXUS-BETA` e roda de novo.

---

## §5 — Editar o `.env`

```bash
nano /root/NEXUS-BETA/.env
```

Os 3 campos com `❗` no comentário precisam ser preenchidos:

| Campo | O que colocar |
|---|---|
| `DOMAIN` | O FQDN público que aponta pra essa VM (ex.: `backup.cliente.com.br`) |
| `CERTBOT_EMAIL` | E-mail real (Let's Encrypt manda alerta de expiração) |
| `FTP_MASQUERADE_ADDRESS` | IP que os clientes usam pra alcançar essa VM (público se via NAT, privado se via VPN). **Sem isso, FTP push vem com 0 bytes.** |

Os outros campos (`SECRET_KEY`, `ENCRYPTION_KEY`, `POSTGRES_PASSWORD`) já vieram preenchidos. **Faça backup do `.env` num cofre offline** — se perder `ENCRYPTION_KEY`, todas as senhas SSH salvas no banco viram lixo.

---

## §6 — Tirar o certificado Let's Encrypt

Antes de rodar, **confirma de outro host** que o DNS resolve pro IP certo:

```bash
dig +short $(grep ^DOMAIN= /root/NEXUS-BETA/.env | cut -d= -f2)
# Deve retornar o IP público da VM
```

Se o DNS ainda não propagou (TTL alto), **espera** — sem isso o certbot falha e você queima rate-limit do Let's Encrypt (50 falhas/hora/IP).

Aí roda:

```bash
cd /root/NEXUS-BETA

# Primeira tentativa: STAGING (não consome rate-limit, valida o fluxo)
STAGING=1 ./init-letsencrypt.sh

# Se chegou em "pronto! acesse: https://...", roda o real:
./init-letsencrypt.sh
```

O script sobe o nginx com cert dummy, baixa o real, recarrega. Renovação automática roda no container `certbot` a cada 12h.

---

## §7 — Subir o stack

```bash
cd /root/NEXUS-BETA
docker compose up -d
docker compose ps   # tudo Up + db Healthy
```

Tail dos logs no primeiro start (verifica que migration rodou e PASV está com masquerade):

```bash
docker compose logs backend -f --tail=50
```

Procure por:
- `Application startup complete.`
- `masquerade (NAT) address: <seu IP>`
- `passive ports: 30000->30099`
- Sem `Traceback` ou `ERROR`

---

## §8 — Criar o primeiro usuário admin

Não tem rota pública de signup. Cria direto no banco:

```bash
docker compose exec db psql -U nexus -d dbnexus <<'SQL'
-- Substitua o e-mail e gere o hash bcrypt da senha primeiro:
--   docker compose exec backend python -c "from passlib.hash import bcrypt; print(bcrypt.hash('SuaSenhaForte'))"
INSERT INTO users (nome, email, senha_hash, role, ativo, senha_temporaria)
VALUES ('Admin', 'admin@exemplo.com.br', '<COLE O HASH AQUI>', 'admin', true, true);
SQL
```

Logado, troca a senha (a flag `senha_temporaria=true` força isso no primeiro acesso).

---

## §9 — Validação final

| Check | Comando |
|---|---|
| Painel acessível | `curl -sk https://$DOMAIN/ \| grep NEXUS` |
| Versão certa exibida | `curl -sk https://$DOMAIN/ \| grep -oE 'v[0-9.]+'` |
| Backend respondendo | `curl -sk -o /dev/null -w '%{http_code}\n' https://$DOMAIN/docs` (200) |
| FTP escutando externo | `nc -zv $DOMAIN 21` (de outro host) |
| SFTP escutando externo | `nc -zv $DOMAIN 22` |
| TFTP escutando externo | `nmap -sU -p 69 $DOMAIN` (de outro host) |
| fail2ban ativa | `fail2ban-client status nexus-ftp` |
| UFW ativa | `ufw status numbered` |
| chain DOCKER-USER hooked | `iptables -L DOCKER-USER -n \| head -3` (deve listar f2b-nexus-ftp) |

---

## §10 — Operação contínua

### Atualizar pra uma versão nova

Quando uma nova tag sair (ex.: `v1.1.0`), seguir [RELEASING.md §"Aplicar uma versão num servidor"](../RELEASING.md):

```bash
cd /root/NEXUS-BETA
docker compose exec db pg_dump -U nexus -d dbnexus | gzip > /root/backups/db-pre-v1.1.0-$(date +%Y%m%d-%H%M).sql.gz
git fetch --tags && git checkout v1.1.0
docker compose up -d --build
```

### Backup contínuo do DB (cron)

Já tem `scripts/pg-backup.sh` pra isso — habilitar via cron:

```bash
crontab -e
# Diário às 03:00, retém 14 dias
0 3 * * * /root/NEXUS-BETA/scripts/pg-backup.sh >> /var/log/nexus-pg-backup.log 2>&1
```

### Reiniciar o stack após mudança no daemon.json

Importante: restart do Docker recria a chain `DOCKER-USER` do zero, removendo o hook `f2b-nexus-ftp`. Sequência correta:

```bash
docker compose down
systemctl restart docker
docker compose up -d
systemctl restart fail2ban   # ← obrigatório
```

---

## Apêndice A — Troubleshooting

| Sintoma | Provável causa |
|---|---|
| FTP push chega com 0 bytes | `FTP_MASQUERADE_ADDRESS` vazio no `.env` ou IP errado |
| Container backend reiniciando | Erro de migration → `docker compose logs backend` |
| Senha SSH no banco vira lixo após reinstalar | `ENCRYPTION_KEY` diferente da anterior |
| fail2ban "no chain DOCKER-USER" | Docker reiniciou e fail2ban não foi reiniciado depois |
| Cert Let's Encrypt 429 (rate limit) | Bateu em produção sem STAGING — esperar 1h ou pedir ao LE |
| Conflito IP container ↔ cliente | Cliente em `10.17.x.x` ou `10.18.x.x` — ajustar `bip`/`pools` no daemon.json (ver memória `project_docker_network.md` no projeto) |
| 502 Bad Gateway no painel | Container backend down ou nginx não consegue resolver `backend:8000` (rede Docker) |

---

## Apêndice B — Diferenças vs. o servidor antigo (Debian 12)

| | Server antigo (`backup.bandaa.net.br`) | Servidor novo (Trixie) |
|---|---|---|
| Distro | Debian 12 (Bookworm) | Debian 13 (Trixie) |
| Kernel | 6.1 LTS | 6.12 LTS |
| Docker | repo oficial Docker | repo oficial Docker (mesmo) |
| Postgres | 16 (container) | 16 (container) |
| iptables backend | nft com legacy disponível | nft "puro" — validar action `docker-allports` |
| Faixas Docker | 10.17/24 + 10.18/16 | mesmo |
| SSH host port | 2288 | 2288 |

A action `docker-allports` usa `iptables` direto, que em Trixie é shim pro `iptables-nft`. Inserções/deleções simples (insert/delete) funcionam igual. Validar com:

```bash
fail2ban-client status nexus-ftp
iptables -n -L DOCKER-USER | grep f2b-nexus-ftp
# Banir manualmente um IP fake e ver se aparece na chain:
fail2ban-client set nexus-ftp banip 198.51.100.99
iptables -n -L f2b-nexus-ftp
fail2ban-client set nexus-ftp unbanip 198.51.100.99
```

Se algo der errado aqui, abrir issue/anotar pra ajustar a action.
