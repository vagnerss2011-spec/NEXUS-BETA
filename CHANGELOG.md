# Changelog

Todo o que entra em release do NEXUS BACKUP. Versionamento [SemVer](https://semver.org/lang/pt-BR/) (MAJOR.MINOR.PATCH) e formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).

Política de bump:

- **MAJOR** — quebra compatibilidade (rename/drop de coluna do banco, mudança de API que requer rebuild do frontend, alteração de variável obrigatória do `.env`).
- **MINOR** — novo fabricante, novo tipo de device, nova feature visível no painel.
- **PATCH** — bugfix, ajuste de UI, hotfix de segurança que não muda contrato.

## [Não lançado]

_(linhas que vão entrar na próxima tag)_

## [1.2.3] - 2026-05-10

### Corrigido

- **SFTP: arquivos órfãos quando cliente fecha sessão sem `SSH_FXP_CLOSE`** — alguns clientes (validado: ZTE ZXA10 C320 com `file-server manual-backup cfg ... sftp`) escrevem o arquivo completo no disco mas fecham a conexão SSH abruptamente sem enviar `SSH_FXP_CLOSE` no handle SFTP. Resultado: `NexusSFTPHandle.close()` nunca era chamado, `processar_upload_local` não rodava, e o arquivo ficava órfão em `infra/sftp-uploads/<user>/.../arquivo` sem chegar ao banco. Adicionado hook `NexusSFTPServerInterface.session_ended()` que processa qualquer arquivo "dirty" (aberto pra escrita nesta sessão) que ainda existe no disco quando o subsystem SFTP termina. Idempotente: em fluxo normal (cliente envia CLOSE), o handle.close() já processou e deletou — session_ended encontra disco vazio e pula.

### Adicionado

- Exemplo SFTP push pra **ZTE ZXA10 (C300/C320/C600)** no painel — sintaxe `file-server manual-backup cfg server-index 1 ipaddress <SERVIDOR> sftp path <nome> user <USUARIO> password <SENHA>` + comando `show auto-backup progress manual` pra acompanhar. Antes o painel só tinha exemplo pra ZXR10 (switch/router), e usuários adaptavam pra OLT sem `path` correto.

## [1.2.2] - 2026-05-10

### Corrigido

- **`paramiko==4.0.0` pinada explicitamente** no `requirements.txt`. Antes vinha como dep transitiva do `netmiko` sem pin, e o `paramiko 5.0.0` (default no pip atual) **remove** os algoritmos KEX legados (`diffie-hellman-group14-sha1`, `diffie-hellman-group1-sha1`) que OLTs antigas (Huawei MA5800/MA5680T, ZTE C320, etc.) ainda oferecem por default. Sintoma: SFTP push falhava em rebuild fresh com `paramiko.ssh_exception.IncompatiblePeer: no acceptable kex algorithm`. Em servidores antigos (já buildados antes do upgrade do paramiko 5) funcionava — o bug só aparecia em instalações novas.
- O `_aplicar_compat_legacy` em `sftp_server.py` já tinha esses algos no `desired_kex`, mas filtrava pelo `_kex_info` em runtime — em paramiko 5 essa lista vinha vazia, e o resultado da intersecção era um set sem KEX comum com o equipamento.

### Sabidos

- Pin em paramiko 4.0 mantém algoritmos legados disponíveis (`group14-sha1`, etc.) por escolha consciente — equipamento de cliente em VRP/ZTE legacy depende deles. Não é "vulnerabilidade", é compatibilidade obrigatória.
- Quando paramiko 4.x sair de manutenção (provável 2027+), reavaliar: implementar GEX-sha1 na mão, ou rodar bridge legacy em container separado.

## [1.2.1] - 2026-05-10

### Corrigido

- **IP do servidor nos exemplos por fabricante (página Dispositivos) agora é dinâmico.** Antes era hardcoded `45.5.16.28` (IP da `backup.bandaa.net.br`) — uma instância nova mostrava o IP da instância antiga nos exemplos de FTP push, NTP, etc. Agora o frontend busca o IP via novo endpoint `GET /api/info/server` (retorna `FTP_MASQUERADE_ADDRESS` da instância) e substitui o placeholder `<SERVIDOR>` em runtime.
- Comentários em `Devices.jsx` referentes a "servidor 45.5.16.28" e instruções "atualizar AQUI" removidos — agora multi-instância sem mudança de código.

### Adicionado

- Novo endpoint `GET /api/info/server` (auth required) — retorna `{ftp_endpoint}` com o IP que clientes usam pra alcançar essa instância (FTP_MASQUERADE_ADDRESS). Usado pelo frontend pra dinamizar exemplos de comando.

### Sabidos

- Se `.env` não tiver `FTP_MASQUERADE_ADDRESS` preenchido, a coluna "Servidor" no modal de credencial gerada mostra `<configure FTP_MASQUERADE_ADDRESS no .env>` em vez de IP — sinal claro pro admin que a config está incompleta.

## [1.2.0] - 2026-05-10

### Adicionado

- **Banner de "versão nova disponível" no painel** — aparece em todas as páginas (via `Layout.jsx`) quando o backend detecta uma tag `vX.Y.Z` mais recente que a versão rodando. Botão "Ver mudanças" abre modal com o changelog resumido (entradas entre versão atual e mais recente). Botão "Dispensar" guarda em `localStorage` por tag — quando vier release nova, o banner volta automaticamente. Update continua **manual via SSH** (notificação-only, respeita `feedback_no_auto_sync_remoto`).
- **`GET /api/version/check`** (auth required) — retorna `{current, latest, update_available, changelog_summary, checked_at}`. Cache em memória TTL 1h pra evitar estourar rate limit do GitHub. Falha-tolerante: token errado / API fora = retorna `latest=null` (banner não aparece, sem ruído).
- **`backend/version.py`** com `APP_VERSION` — fonte única consultada pelo endpoint de check e usado em `FastAPI(title=..., version=APP_VERSION)`. Bump junto com `frontend/package.json` a cada release (documentado em `RELEASING.md` checklist).
- **Settings novos no `.env`:**
  - `GITHUB_TOKEN` (PAT scope `repo` read) — vazio = checagem desabilitada, banner não aparece.
  - `GITHUB_REPO` (default `vagnerss2011-spec/NEXUS-BETA`) — override pra fork interno.

### Mudado

- `FastAPI(title=...)` mudou de `"NEXUS BETA - Backup Manager"` pra `"NEXUS BACKUP"` (alinhamento com rename feito em v1.0.0).
- `FastAPI(version=...)` agora vem de `backend/version.py` em vez de hardcoded `"1.0.0"` — `/docs` mostra a versão correta.
- Endpoint `GET /` ainda retorna `{status, version}`, mas com label "NEXUS BACKUP online" e versão dinâmica.

## [1.1.2] - 2026-05-10

PATCH com 5 correções no `scripts/install-nexus-backup.sh` descobertas durante a primeira instalação real em VM Debian 13 Trixie limpa (`nexus.camon.net.br`, validada em prod com OLT/UNM2000).

### Corrigido

- **Action `docker-allports` do fail2ban precisa de bloco `[Init]` em Trixie.** Em Debian 13 o pacote `fail2ban 1.1.0` removeu `iptables-common.conf`, então a variável `<iptables>` ficava literal e o shell tentava executar `iptables` como comando relativo — falhava com `cannot open iptables: No such file`. Action agora declara `iptables = /usr/sbin/iptables` em `[Init]`.
- **Ordem dos passos:** `Clone do repo` (era 6) e `Diretórios persistentes` (era 7) movidos pra **passos 3 e 4**, antes do fail2ban (passo 6 agora). Sem isso, fail2ban 1.1.0 falha o startup ao não encontrar o `infra/ftp-logs/ftp-auth.log` que o jail tail-a (1.1.0 não tolera logpath inexistente, diferente de versões antigas).
- **Clone via SSH (deploy key)** em vez de HTTPS — repo privado não responde clone HTTPS sem credencial. Script agora gera `~/.ssh/nexus_deploy_key` automaticamente, configura `~/.ssh/config` pra rotear `github.com` via essa chave, testa auth, e se falhar mostra a public key na tela com instrução pra colar em GitHub → Settings → Deploy Keys. Override pra HTTPS via `REPO_URL=https://...`.
- **Geração de `ENCRYPTION_KEY` agora é openssl-only** (`openssl rand -base64 32 | tr '+/' '-_'`). Era 2-fallback (python3 → docker), frágil em VM nova sem `python3-cryptography`. Resultado byte-equivalente a `Fernet.generate_key()` mas sem deps externas.
- **Documentação do primeiro admin** atualizada — usa Python no container (hash bcrypt + INSERT via SQLAlchemy na mesma sessão) em vez de gerar hash via shell + INSERT separado via psql. Hash bcrypt começa com `$2b$12$...` e o `$2b`/`$12` viravam expansão de variável no shell, truncando o hash silenciosamente. Sintoma: login falhava com `passlib.exc.UnknownHashError`.

### Adicionado

- **`NEXUS_EXTRA_CIDRS` env var** — CSV de faixas adicionais autorizadas a pedir hora ao chrony (NTP) e a passar pelo UFW na 123/udp. Era `45.5.16.0/22` hardcoded (faixa do provedor antigo). Agora cada instalação passa as próprias faixas: `NEXUS_EXTRA_CIDRS=200.150.30.0/24,45.7.68.0/22 bash install-nexus-backup.sh -i`. Default vazio = só RFC1918+RFC6598.
- Script detecta bloco antigo `# === NEXUS BACKUP === ` no `chrony.conf` e reescreve idempotentemente (antes era append-only e duplicava as `allow` se rodasse 2x).
- Script restart o fail2ban com `sleep 2` + check `is-active` pós-restart — antes seguia em frente assumindo OK, mascarando falhas de config.

### Documentação

- `docs/INSTALL.md` — nova subseção `§3.5 — Deploy key SSH (repo privado)` com fluxo de geração automática + add no GitHub.
- `docs/INSTALL.md §8` — bloco Python pro primeiro admin (anti shell-expansion); SQL puro fica documentado como "forma manual se você gerar o hash em outro lugar".

## [1.1.1] - 2026-05-10

### Adicionado

- Excluir eventos de push manualmente na página Logs: trash em cada linha de push (igual scheduler) + botão "Excluir logs push" no header (mass delete só dos tipos push, preserva backups recebidos). admin_empresa só apaga eventos da própria empresa; eventos com `empresa_id` NULL (auth fail antes de identificar device) ficam restritos ao admin master.
- `DELETE /api/atividades/{id}` (single) e `DELETE /api/atividades/?tipos=...&empresa_id=...` (mass com filtros). Sem filtros explícitos, mass delete restringe automaticamente ao escopo do user (admin_empresa nunca apaga fora da própria empresa).

## [1.1.0] - 2026-05-10

### Adicionado

- **Logs do Sistema (página Logs)** agora mostra também os eventos de push (FTP/SFTP/TFTP) misturados cronologicamente com as execuções do scheduler. Filtros por chip (Tudo / Scheduler / Push) sem perder o ordenamento global. Cada linha de push exibe protocolo, status (recebido / falha / negado / volume alto), device, IP origem, motivo e nome do arquivo. Cliques em linhas de scheduler continuam abrindo o modal de detalhe por device.
- Novo `TipoAtividade.ftp_backup_falha` — registra **falhas de transferência/processamento APÓS auth ok** (arquivo 0 bytes, tamanho > limite, exceção no parser binário, OSError lendo arquivo). Antes essas falhas só iam pro logging do uvicorn — agora ficam visíveis no painel. Distinto de `ftp_acesso_negado` (auth fail).
- Audit de push agora carrega **protocolo** (FTP/SFTP/TFTP) e **nome do arquivo** no campo `detalhe`, separados por ` · ` (formato: `PROTOCOLO · motivo · arquivo`). UI parseia pra render.
- `/api/atividades?tipos=...` aceita filtro CSV de `TipoAtividade` (ex.: `tipos=ftp_backup_recebido,ftp_backup_falha`). Tipos inválidos são silenciosamente ignorados (resiliente a typo). Limite máximo subiu de 200 → 500.
- `scripts/install-nexus-backup.sh` — bootstrap idempotente pra Debian 13 (Trixie). Configura locale/timezone, Docker (repo oficial + daemon.json com bip/pools), chrony (allow + ratelimit), fail2ban (action docker-allports + filter+jail nexus-ftp), UFW, clona o repo na tag mais recente, gera `.env` com secrets aleatórios. Não toca em SSH (deixa pro operador via console). Suporta flag `--interactive` que apresenta cada um dos 8 passos com descrição + por quê e pede confirmação ([s]im/[n]ão/[a]ll/[q]uit).
- `docs/INSTALL.md` — runbook ponta-a-ponta pra provisionar nova instância (Proxmox VM Debian 13 → mover SSH pra 2288 → script bootstrap → editar `.env` → certbot → compose up → criar admin → validar). Inclui troubleshooting e diferenças vs. servidor Debian 12 antigo.

### Mudado

- Página `Logs` renomeada de "Logs do Scheduler" para "Logs do Sistema" — agora abrange ambas as fontes (scheduler + push).
- Mensagem do alerta Telegram em `push_negado` agora inclui o protocolo (FTP/SFTP/TFTP) — antes só mostrava IP e usuário, sem dizer qual servidor recebeu o ataque.

## [1.0.0] - 2026-05-10

Primeira versão estável. Em produção em `backup.bandaa.net.br` desde abril/2026.

### Adicionado

**Multi-tenant + autenticação**
- Modelo de empresas isoladas (`empresa_id` em todos os recursos).
- Roles: `admin` (master), `admin_empresa`, `operador`, `viewer`.
- Login com JWT, troca obrigatória de senha no primeiro acesso, reset por admin.
- Lockout de conta após 5 senhas erradas + rate limit no nginx.

**Backup ativo (pull) — SSH/Telnet/SFTP**
- Scheduler diário (APScheduler) às 02:00 com timezone `America/Sao_Paulo`.
- Comandos por fabricante: MikroTik (`/export`), Huawei (`display current-configuration`), Ubiquiti (`cat /tmp/system.cfg`), V-SOL (OLT), ZTE, Nokia, Fiberhome, Intelbras, "Outro" (`show running-config`).
- Variação MikroTik V7 com `/export show-sensitive`.
- Autenticação por senha **ou** chave SSH.
- Compatibilidade Huawei VRP MA5800: KEX/cipher/MAC legados reativados, DH-GEX removido (paramiko 4.0 server-side broken).
- Retenção configurável (`BACKUP_RETENTION_DAYS`, default 7 dias por device).

**Backup passivo (push) — FTP / SFTP / TFTP**
- Botão "Novo via FTP/SFTP/TFTP push" no painel com exemplos por fabricante.
- Whitelist de IP por device (CIDR).
- Credenciais geradas pelo painel; FTP user no formato `ftp_NNNNN` (ou `ftpNNNNN` para UNM2000).
- Logs de auth failures em arquivo (`/var/log/nexus`) p/ fail2ban tailar.
- SFTP padronizado em porta 22 (SSH host movido pra 2288); host key persistente em `/var/lib/nexus`.
- TFTP server no compose (porta 69/udp + faixa efêmera 30000-30099).
- FTP passivo na faixa 30000-30099 com `FTP_MASQUERADE_ADDRESS` configurável (resolve PASV atrás de Docker NAT).

**Fluxo Fiberhome OLT via UNM2000 (NMS)**
- Tipo de device dedicado `unm2000` com aba separada no painel.
- Suporte a Configuration Export Task: UNM2000 manda comando TL1 pra OLT executar upload.
- Limite de senha por equipamento: 16 chars para OLT Fiberhome AN5516 (CLI trunca silenciosamente), 20 chars para EMS UNM2000.
- Username UNM2000 sem `_` (EMS rejeita chars especiais).
- Recebe múltiplos arquivos por export (.txt + .zip) sem flagar como corrupto.
- Conteúdo binário armazenado como `BASE64:<b64>` em `Backup.conteudo`; campo `nome_arquivo` preserva extensão original (.zip).
- Botão "visualizar" oculto para devices `unm2000` (só download).

**UI**
- Sidebar drawer + tabelas com scroll horizontal (responsividade mobile Tier 1).
- Headers, padding e StatCard responsivos (Tier 2).
- Coluna "Origem" no histórico de backups (Manual / Scheduler / Push) com badges coloridos.
- Coluna "Tamanho" + alerta laranja de redução suspeita (compara só arquivos de mesma extensão).
- Logs de scheduler mostram empresas afetadas em cada execução.
- Exemplos de NTP client por fabricante; exemplo Huawei completo validado em prod.
- Confirmação do fabricante antes de salvar novo dispositivo.
- Versão atual exibida na Sidebar e Login.

**Alertas**
- Notificações Telegram com bot único + chat por empresa.
- Eventos: `falha_backup`, `push_negado`, `volume_alto`.

**Infra**
- Docker Compose: db (Postgres 16), backend (FastAPI), frontend (nginx + React build), certbot.
- IPv6 habilitado (necessário pra SSH em links BGP ISP).
- Bridges Docker em `10.17.0.0/24` (docker0) + pool `10.18.0.0/16` size 24 (compose) — saiu de 172.17/172.18 default por colidir com IPs de cliente.
- TLS Let's Encrypt via certbot, renovação automática a cada 12h.
- nginx: no-cache em `index.html`, `immutable` em `/assets`.

### Segurança

- Criptografia Fernet das senhas SSH no banco (`ENCRYPTION_KEY`).
- fail2ban com action customizada `docker-allports` hookada na chain `DOCKER-USER` (UFW não banimentos tráfego que entra via NAT do Docker).
- chrony com `ratelimit interval 1 burst 16 leak 2` (NTP server abusado).
- CORS RFC1918 via regex no `main.py` (rede local) + domínio público pelo certbot.
- SSH host na 2288, porta 22 reservada para SFTP push.

### Sabidos / pendências conhecidas

- Migrations no startup só usam `IF NOT EXISTS` — quando precisar rename/drop, vai exigir alembic.
- Backup do volume `pgdata` + `infra/state/` (host key) é manual via cron — não há job automático.
- Sem checagem de versão no painel: cada instância roda a tag que foi deployada manualmente (ver [RELEASING.md](RELEASING.md)).

[Não lançado]: https://github.com/vagnerss2011-spec/NEXUS-BETA/compare/v1.2.3...HEAD
[1.2.3]: https://github.com/vagnerss2011-spec/NEXUS-BETA/compare/v1.2.2...v1.2.3
[1.2.2]: https://github.com/vagnerss2011-spec/NEXUS-BETA/compare/v1.2.1...v1.2.2
[1.2.1]: https://github.com/vagnerss2011-spec/NEXUS-BETA/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/vagnerss2011-spec/NEXUS-BETA/compare/v1.1.2...v1.2.0
[1.1.2]: https://github.com/vagnerss2011-spec/NEXUS-BETA/compare/v1.1.1...v1.1.2
[1.1.1]: https://github.com/vagnerss2011-spec/NEXUS-BETA/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/vagnerss2011-spec/NEXUS-BETA/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/vagnerss2011-spec/NEXUS-BETA/releases/tag/v1.0.0
