# Changelog

Todo o que entra em release do NEXUS BACKUP. Versionamento [SemVer](https://semver.org/lang/pt-BR/) (MAJOR.MINOR.PATCH) e formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).

Política de bump:

- **MAJOR** — quebra compatibilidade (rename/drop de coluna do banco, mudança de API que requer rebuild do frontend, alteração de variável obrigatória do `.env`).
- **MINOR** — novo fabricante, novo tipo de device, nova feature visível no painel.
- **PATCH** — bugfix, ajuste de UI, hotfix de segurança que não muda contrato.

## [Não lançado]

_(linhas que vão entrar na próxima tag)_

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

[Não lançado]: https://github.com/vagnerss2011-spec/NEXUS-BETA/compare/v1.1.1...HEAD
[1.1.1]: https://github.com/vagnerss2011-spec/NEXUS-BETA/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/vagnerss2011-spec/NEXUS-BETA/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/vagnerss2011-spec/NEXUS-BETA/releases/tag/v1.0.0
