# Changelog

Todo o que entra em release do NEXUS BACKUP. Versionamento [SemVer](https://semver.org/lang/pt-BR/) (MAJOR.MINOR.PATCH) e formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).

Política de bump:

- **MAJOR** — quebra compatibilidade (rename/drop de coluna do banco, mudança de API que requer rebuild do frontend, alteração de variável obrigatória do `.env`).
- **MINOR** — novo fabricante, novo tipo de device, nova feature visível no painel.
- **PATCH** — bugfix, ajuste de UI, hotfix de segurança que não muda contrato.

## [Não lançado]

_(linhas que vão entrar na próxima tag)_

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

[Não lançado]: https://github.com/vagnerss2011-spec/NEXUS-BETA/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/vagnerss2011-spec/NEXUS-BETA/releases/tag/v1.0.0
