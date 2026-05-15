# Instalação do NEXUS BACKUP — Debian 13 (Trixie) em Proxmox

Guia ponta-a-ponta pra subir uma instância nova. Pareado com [scripts/install-nexus-backup.sh](../scripts/install-nexus-backup.sh): o script cobre §4–§5; o restante (provisionamento, SSH, DNS, certbot, primeiro admin) é manual e está aqui.

> **Atalho:** se você só precisa do passo-a-passo simples pra mover SSH pra 2288 e configurar deploy key do GitHub privado antes do install, ver [QUICKSTART.md](QUICKSTART.md).

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

Antes dos passos, faz **pré-flight de conectividade**: testa DNS (`getent ahosts github.com`), HTTPS pra `github.com` e detecta IP público via `api.ipify.org`. Falha cedo com mensagem clara se algo crítico estiver fora. O IP detectado é reaproveitado no passo 8 pra preencher `FTP_MASQUERADE_ADDRESS` automaticamente.

O que ele faz, em ordem (9 passos):
1. **Sistema base** — locale `pt_BR.UTF-8` + timezone `America/Sao_Paulo` + apt deps (git, ufw, fail2ban, chrony, etc.)
2. **Docker Engine** — repo oficial Docker + `/etc/docker/daemon.json` com `bip 10.17.0.1/24` + pool `10.18.0.0/16` + IPv6
3. **Clone do repo** — em `/root/NEXUS-BETA` na tag mais recente. **Default: SSH com deploy key** (ver §3.5 abaixo). Pra HTTPS público use `REPO_URL=https://...`
4. **Diretórios persistentes** — `infra/ftp-logs/ftp-auth.log` e `infra/state/` (criados antes do fail2ban porque o jail `nexus-ftp` precisa do log file existir no startup)
5. **Chrony NTP** — `allow` RFC1918+RFC6598 + ratelimit. Faixas adicionais via env var `NEXUS_EXTRA_CIDRS=cidr1,cidr2,...`
6. **Fail2ban** — action `docker-allports` (com bloco `[Init]` definindo `iptables=/usr/sbin/iptables` — necessário em Trixie), filter+jail `nexus-ftp` apontando pro log do passo 4
7. **UFW** — allow 2288/80/443/21/22/69/30000-30099 + 123/udp pra RFC1918+RFC6598+`NEXUS_EXTRA_CIDRS`, depois `ufw enable`
8. **`.env`** — secrets gerados (`SECRET_KEY`, `ENCRYPTION_KEY`, `DB_EXPORT_KEY` v2.0.0+, `POSTGRES_PASSWORD` via openssl) + `FTP_MASQUERADE_ADDRESS` auto-preenchido com o IP público detectado + placeholders editáveis (`DOMAIN`, `CERTBOT_EMAIL`, opcional `GITHUB_TOKEN`). Se `.env` já existir, faz backup em `.env.bak.<timestamp>` e NÃO sobrescreve.
9. **Cron pg-backup.sh** — OPCIONAL: instala `/etc/cron.d/nexus-pg-backup` rodando `pg_dump` todo dia às 03:30, retendo 14 dias em `/var/backups/nexus-postgres/`. Use quando a VM **não** tem snapshot do hipervisor cobrindo o disco.

### Variáveis de ambiente opcionais

```bash
# Faixas adicionais de IP que poderão pedir hora ao NTP (chrony) e passar
# pelo UFW na 123/udp. Default: nenhuma (só RFC1918+RFC6598).
NEXUS_EXTRA_CIDRS=200.150.30.0/24,45.7.68.0/22 bash /tmp/install.sh -i

# Override do repo (default git@github.com:vagnerss2011-spec/NEXUS-BETA.git).
# Use HTTPS se o repo for público:
REPO_URL=https://github.com/vagnerss2011-spec/NEXUS-BETA.git bash /tmp/install.sh -i
```

> Se você precisar recriar do zero, apaga `/etc/docker/daemon.json`, `/etc/fail2ban/jail.local`, `/root/NEXUS-BETA` e roda de novo.

### §3.5 — Deploy key SSH (repo privado)

Quando o passo 3 do script roda com `REPO_URL` SSH (default), ele:
1. Gera `/root/.ssh/nexus_deploy_key` se não existir (ed25519, sem passphrase)
2. Configura `/root/.ssh/config` pra rotear `github.com` via essa chave
3. Testa autenticação no GitHub com `ssh -T git@github.com`
4. Se der "successfully authenticated" → segue
5. Se der "Permission denied" → mostra a public key na tela e pede pra colar em GitHub → **Settings → Deploy Keys → Add deploy key** (read-only). Aí pressione Enter pra continuar.

A public key fica em `/root/.ssh/nexus_deploy_key.pub`. Pode pegar com:
```bash
cat /root/.ssh/nexus_deploy_key.pub
```

---

## §5 — Editar o `.env`

```bash
nano /root/NEXUS-BETA/.env
```

Desde a v2.1.4 o script auto-preenche `FTP_MASQUERADE_ADDRESS` com o IP público detectado via `api.ipify.org`. Os 2 campos abaixo continuam sendo obrigatórios:

| Campo | O que colocar |
|---|---|
| `DOMAIN` | O FQDN público que aponta pra essa VM (ex.: `backup.cliente.com.br`) |
| `CERTBOT_EMAIL` | E-mail real (Let's Encrypt manda alerta de expiração) |

E **revise** o que foi auto-detectado:

| Campo auto | Quando editar manualmente |
|---|---|
| `FTP_MASQUERADE_ADDRESS` | Se os clientes acessam por outro IP (VPN, NAT específico, IP secundário). Sem isso correto, **FTP push vem com 0 bytes.** |

Os campos abaixo já vieram gerados — **NÃO troque depois**:

| Campo | O que é |
|---|---|
| `SECRET_KEY` | Assina os JWT do painel (rotacionar invalida sessões) |
| `ENCRYPTION_KEY` | Cifra senhas SSH dos devices no banco. Se trocar, **todas as senhas viram lixo** |
| `DB_EXPORT_KEY` | Cifra o `.nxbak` (snapshot diário, v2.0.0+). Se perder, arquivos `.nxbak` são irrecuperáveis |
| `POSTGRES_PASSWORD` | Auth do user do banco — fixado quando o container db inicializou pela primeira vez |

E os opcionais (vazio = feature desabilitada):

| Campo | Pra que serve |
|---|---|
| `GITHUB_TOKEN` | PAT do GitHub pra ativar banner "v2.X.Y disponível" no painel. Crie em [github.com/settings/tokens](https://github.com/settings/tokens) com scope `repo` read |
| `GITHUB_REPO` | Repo no formato `owner/repo`. Default já aponta pro repo oficial |

> **Faça backup do `.env` num cofre offline.** Guarde `ENCRYPTION_KEY` e `DB_EXPORT_KEY` em local **separado** do servidor — sem elas o histórico criptografado vira lixo.

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

Não tem rota pública de signup. Cria direto no banco — **via Python no container backend**, NÃO via shell + bcrypt + psql separados.

> ⚠️ **Por que NÃO fazer em 2 passos (gera hash → INSERT via psql):**
> O hash bcrypt começa com `$2b$12$...`. Se você passa pelo shell, `$2b` e `$12` viram tentativas de expansão de variável — bash come os primeiros 6-8 chars do hash silenciosamente, deixando algo tipo `b2.XRf.` no banco. Login falha com `UnknownHashError: hash could not be identified`.

**Forma correta** — hash + INSERT na mesma sessão Python, zero shell:

```bash
cd /root/NEXUS-BETA

docker compose exec -T backend python <<'PYEOF'
import asyncio
from passlib.hash import bcrypt
from sqlalchemy import text
from database import engine

EMAIL = "voce@exemplo.com.br"      # ← TROCA
NOME  = "Seu Nome"                 # ← TROCA
SENHA = "SuaSenhaForte"            # ← TROCA (vai ser temporária — força troca no 1o login)

async def main():
    h = bcrypt.hash(SENHA)
    async with engine.begin() as conn:
        await conn.execute(
            text("""INSERT INTO users (nome, email, senha_hash, role, ativo,
                                       tentativas_falhas, senha_temporaria, criado_em)
                    VALUES (:n, :e, :h, 'admin', true, 0, true, NOW())"""),
            {"n": NOME, "e": EMAIL, "h": h}
        )
    print(f"INSERT OK — admin {EMAIL} criado")

asyncio.run(main())
PYEOF
```

Logado, troca a senha (a flag `senha_temporaria=true` força isso no primeiro acesso).

### Bloco SQL (forma manual — só se você sabe o hash de antemão)

Se você gerar o hash em outro lugar e quiser fazer só o INSERT:

```bash
docker compose exec -T db psql -U nexus -d dbnexus <<SQL
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

Desde a v2.1.4 o passo 9 do `install-nexus-backup.sh` já oferece instalar a cron automaticamente. Se pulou na hora do install, dá pra instalar avulso:

```bash
sudo tee /etc/cron.d/nexus-pg-backup > /dev/null <<'EOF'
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
30 3 * * * root cd /root/NEXUS-BETA && ./scripts/pg-backup.sh >> /var/log/nexus-pg-backup.log 2>&1
EOF
sudo chmod 644 /etc/cron.d/nexus-pg-backup
sudo touch /var/log/nexus-pg-backup.log
```

Roda diário às 03:30, retém 14 dias em `/var/backups/nexus-postgres/`. Restore:

```bash
docker compose exec -T db pg_restore -U $POSTGRES_USER -d $POSTGRES_DB --clean --if-exists < <dump_file>
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
