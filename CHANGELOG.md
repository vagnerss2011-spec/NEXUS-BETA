# Changelog

Todo o que entra em release do NEXUS BACKUP. Versionamento [SemVer](https://semver.org/lang/pt-BR/) (MAJOR.MINOR.PATCH) e formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).

Política de bump:

- **MAJOR** — quebra compatibilidade (rename/drop de coluna do banco, mudança de API que requer rebuild do frontend, alteração de variável obrigatória do `.env`).
- **MINOR** — novo fabricante, novo tipo de device, nova feature visível no painel.
- **PATCH** — bugfix, ajuste de UI, hotfix de segurança que não muda contrato.

## [Não lançado]

_(linhas que vão entrar na próxima tag)_

## [2.2.1] - 2026-05-19

Refinamentos do Mirror FTP após validação real no ibiunet (download de firmware Huawei de 1.1GB).

### Corrigido

- **FTP travava após download grande quando o cliente usava modo ativo.** Sintoma: depois de baixar um firmware (modo passivo OK), reconectar com cliente FTP desktop pra navegar/baixar outros arquivos travava cada operação ~30s e falhava (`Active data channel timed out` no log). Causa: modo FTP **ativo** (PORT/EPRT) é fisicamente impossível atrás do Docker NAT — o servidor tentaria abrir a conexão de dados de volta pro IP que o cliente informa, mas clientes atrás de NAT informam IP privado inalcançável.
  - **Fix:** `NexusFTPHandler` agora **recusa PORT/EPRT pra origens firmware** com `500 Modo ativo nao suportado. Configure o cliente em modo passivo (PASV).` — clientes bem comportados (FileZilla, lftp, devices) caem pra PASV automaticamente. Devices de push (fluxo legado) seguem com o comportamento original, intocados.

### Adicionado

- **Whitelist de IP por origem FTP** (`firmware_origens.origem_cidr`). Campo opcional no cadastro da origem: se preenchido, o FTP só autentica conexões vindas dos IPs/CIDRs cadastrados (defesa extra além da senha); vazio = qualquer IP. Aceita múltiplos separados por vírgula (`200.1.2.3/32, 10.0.0.0/24`). Enforce em `_auth_firmware_origem` via `_ip_match_lista`. Validação de formato no router (400 em CIDR inválido). Coluna exibida na tabela de origens.
- **Ver/copiar credencial a qualquer momento** (`GET /api/firmware-origens/{id}/credencial`). Diferente do device push (senha mostrada 1x), a origem firmware é reconfigurada em vários devices ao longo do tempo — agora o admin recupera usuário+senha pelo botão da chave na listagem sem precisar regerar (o que invalidaria os devices já configurados). Senha Fernet-decrypt no backend; só admin/admin_empresa acessa. O modal de credencial (com toggle exibir/ocultar + botões de copiar) virou reutilizável pra criação, regeneração e consulta.

### Notas técnicas

- Migração idempotente: `ALTER TABLE firmware_origens ADD COLUMN IF NOT EXISTS origem_cidr`. Origens criadas na v2.2.0 ficam sem whitelist (qualquer IP) até o admin preencher.
- **Importante pra quem baixa via cliente desktop:** marque modo **passivo** nas configurações do cliente FTP (FileZilla: Editar → Configurações → Conexão → FTP → Modo passivo). Devices (Mikrotik `/tool fetch`, Huawei) já usam passivo por default.

## [2.2.0] - 2026-05-19

### Adicionado — Mirror FTP de firmwares (aba "Firmwares" no painel)

Nova feature pra usar o NEXUS BACKUP como **mirror de firmwares**: admin sobe firmwares pelo painel, gera credenciais FTP por "origem" (filial, NOC, parceiro, etc.), e devices remotos baixam via `/tool fetch` (Mikrotik) ou comando equivalente em outros fabricantes.

**Backend:**

- 2 tabelas novas: `firmwares` (catálogo com nome/fabricante/modelo/versão/sha256/tamanho) e `firmware_origens` (credenciais FTP com senha cifrada via Fernet).
- 6 valores novos no enum `TipoAtividade` pra audit: `firmware_baixado`, `firmware_enviado`, `firmware_upload_painel`, `firmware_removido`, `firmware_origem_criada`, `firmware_origem_removida`.
- Router `routers/firmwares.py` com endpoints REST:
  - `POST /api/firmwares/upload` — multipart streaming pro disco (chunks de 4MB), SHA256 calculado on-the-fly. Sem limite explícito de tamanho (timeout do axios estendido pra 60min no frontend pra cobrir firmwares de 1-2GB).
  - `GET/PATCH/DELETE /api/firmwares/{id}` + `GET /api/firmwares/{id}/download`.
  - `GET /api/firmwares/orfaos` — lista arquivos físicos em `/var/firmware/` **sem** row correspondente no DB (uploads via FTP de origens, perm `w`). Endpoints separados pra **promover** (`POST /orfaos/{nome}/promover` com metadados) ou **deletar** (`DELETE /orfaos/{nome}`).
  - CRUD completo de origens: `GET/POST /firmware-origens`, `PATCH/DELETE /firmware-origens/{id}`, `POST /firmware-origens/{id}/regen-senha`. Senha gerada (24 chars alfanuméricos, ~143 bits) retornada em texto puro apenas no momento da criação/regeneração via `FirmwareOrigemCredencial`.
- `services/ftp_server.py` ganhou **discriminação por tipo de credencial**:
  - `Device.ftp_user` (fluxo legado de push de backup) — write-only, chroot por device, whitelist CIDR.
  - `FirmwareOrigem.usuario_ftp` (novo) — perms `elrw` (list+read+write), chroot compartilhado em `/var/firmware/`, sem whitelist CIDR (defesa por senha forte + fail2ban + flag `ativo`).
  - Hook `on_file_received` desvia upload de origem firmware pra `_processar_upload_firmware` que só registra atividade e NÃO apaga o arquivo — fica como órfão pro admin promover/deletar pelo painel.
  - Hook novo `on_file_sent` registra `firmware_baixado` toda vez que uma origem completa um RETR — observabilidade pra auditoria.
- `firmware_origens.ultimo_acesso_em` + `ultimo_ip` atualizados em todo auth bem-sucedido — telemetria leve pra admin saber quem tá usando o quê.

**Frontend:**

- Página nova [Firmwares.jsx](frontend/src/pages/Firmwares.jsx) com 2 abas:
  - **Arquivos** — tabela de firmwares catalogados (nome, arquivo, tamanho, SHA256 truncado, fabricante/modelo/versão, criador) + ações download/delete. Seção separada amarela pra **uploads externos** (órfãos) com ações promover/deletar.
  - **Origens FTP** — tabela de credenciais (nome, user, status ativo/inativo, último acesso/IP, criado por) + ações toggle ativo, regerar senha, excluir.
- Modal de upload com barra de progresso (`onUploadProgress` do axios), validação client-side e datalist de fabricantes comuns.
- Modal de credencial: mostra `usuario_ftp` + `senha_ftp` (toggle eye/eye-off) com botões de copy, banner amarelo avisando que a senha não é recuperável. Aparece **uma única vez** após criar/regerar.
- Card "Exemplo de uso no device" no topo da página: comando `/tool fetch ftp://...` dinamicamente preenchido com o IP do `FTP_MASQUERADE_ADDRESS` (via `/api/info/server`) e o `usuario_ftp` da primeira origem ativa. Botão de copy. Se o `.env` não tem o IP, mostra placeholder visível + aviso.
- Item "Firmwares" no Sidebar entre "Operações" e "Usuários" (ícone HardDrive). Visível pra todos os roles autenticados — viewer só lista/baixa, demais roles podem fazer upload e CRUD origem (gate fino no router via `require_role`).

**Permissões:**

| Ação | Roles |
|---|---|
| Listar firmwares + download via painel | qualquer autenticado |
| Upload/edit/delete firmware + promover/deletar órfão | `admin`, `admin_empresa`, `operador` |
| CRUD origens FTP (criar, ativar/desativar, regerar senha, excluir) | `admin`, `admin_empresa` (operador NÃO — é credencial sensível) |
| Acesso FTP via mirror (download/upload de firmwares) | origem FTP autenticada, somente se `ativo=true` |

**Infra:**

- Volume novo no `docker-compose.yml`: `./infra/firmware:/var/firmware` (persistência entre rebuilds). Dimensionar o disco do host conforme catálogo previsto.
- Portas FTP **sem mudança** — reaproveita 21 (control) + 30000-30099 (PASV) já mapeadas pro fluxo de push de backup.

**Notas operacionais:**

- Servidores existentes precisam **redeploy** (`git pull && docker compose up -d --build`) pra subir o backend novo, criar as 2 tabelas (idempotente via `Base.metadata.create_all`) e ganhar a aba no painel.
- Pra Mikrotiks baixarem: `/tool fetch url="ftp://fwm_00001:SENHA@<IP>/firmware.npk" mode=ftp` (comando já mostrado no card de exemplo da página). Outros fabricantes: comando equivalente do fabricante.
- Uploads externos (origem com perm `w` subindo arquivo) ficam como órfãos no painel — admin decide promover (cadastrar metadados) ou apagar.

## [2.1.6] - 2026-05-15

### Corrigido — `install-nexus-backup.sh` Passo 7 UFW

- **`ERROR: Invalid syntax` no Passo 7/9 (UFW firewall).** Reportado em instalação real. UFW em Debian 13 (versão 0.36.2+) parseia comments em modo strict — caracteres especiais como apóstrofe (`'`), colchetes (`[]`) e às vezes parênteses (`()`) fazem UFW retornar "ERROR: Invalid syntax" e abortar o `allow`. Os comments do script tinham:
  - `'HTTP (Let'\\''s Encrypt + redirect)'` — apóstrofe em "Let's"
  - `'SSH host (fail2ban [sshd] jail protege)'` — colchetes `[sshd]`
  - Outros com parênteses.

  Pior: o `ufw_add` helper original silenciosamente ignorava o erro (`>/dev/null` no stdout, sem checar exit code ou stderr), reportando `✓ ufw: 80/tcp` mesmo quando a regra não foi criada. Em produção, isso deixava o firewall **sem as regras esperadas** sem aviso nenhum no log do install.

  **Fix duplo:**

  1. **Simplificou TODOS os comments** pra usar apenas alfanuméricos + espaço + hífen + barra (caracteres que UFW sempre aceita). Mantém significado mas sem caracteres problemáticos:
     - `HTTP - Lets Encrypt e redirect` (sem apóstrofe)
     - `SSH host - fail2ban protege sshd jail` (sem colchetes)
     - `NTP server $cidr RFC1918 RFC6598` (sem parênteses)

  2. **Robusteceu `ufw_add`** pra capturar stdout+stderr, detectar regex `'ERROR|invalid'` no output e abortar com `fail()` se ocorrer. Antes o erro passava em silêncio; agora o script para na primeira regra que falhar com mensagem completa do UFW.

### Notas técnicas

- Backend/frontend de runtime **não mudaram**. Servidores existentes (Bandaa, Camon) não precisam redeploy.
- Versões anteriores do script que rodaram com sucesso aparente em produção podem ter regras UFW faltando. Pra auditar: `ufw status numbered` e comparar com a lista esperada (2288/80/443/21/22/69/30000-30099 + 123/udp por faixa). Reaplique manualmente o que faltar com comments curtos sem caracteres especiais.

## [2.1.5] - 2026-05-15

### Corrigido — `install-nexus-backup.sh`

Achados em instalação real numa VM Debian 13 minimal. Sem essas correções, o operador precisa intervir manualmente em 3 pontos:

- **`curl` não estava instalado** antes do pré-flight. Debian 13 minimal não traz curl por default. O pré-flight tentava `curl https://github.com` e `curl https://api.ipify.org`, falhando antes do passo 1 (que instalaria curl via apt).
  - Fix: novo bloco "ensure-curl" que precede o pré-flight — checa `command -v curl` e roda `apt-get install -y curl ca-certificates` se faltar. Idempotente.

- **Mensagem de erro do pré-flight DNS era pouco didática**. Quando `/etc/resolv.conf` está vazio, o script só dizia "DNS não consegue resolver" sem mostrar como corrigir.
  - Fix: mensagem agora inclui o comando de correção rápida:
    ```
    echo 'nameserver 1.1.1.1' > /etc/resolv.conf
    echo 'nameserver 8.8.8.8' >> /etc/resolv.conf
    ```
  - Também aponta que pra solução permanente o operador deve configurar DNS no
    network manager (systemd-networkd, NetworkManager, etc.).

- **UFW podia reportar enable com sucesso mas ficar `inactive (dead)`** após reboot em Debian 13 + nftables backend. O `ufw enable` retornava OK mas o serviço systemd não estava habilitado.
  - Fix: sequência defensiva: `systemctl enable ufw` → `ufw enable` → `sleep 1` → pós-check de `ufw status` E `systemctl is-active ufw`. Se ainda inactive, mostra warning com comandos de remediação manual.

### Notas técnicas

- Backend/frontend de runtime **não mudaram**. Servidores existentes (Bandaa, Camon) não precisam redeploy.
- Erro cosmético `Deleting nftables IPv4 rules ... delete table ip docker-bridges: No such file or directory` que aparece em alguns reboots do Docker em Trixie é conhecido upstream ([moby/moby#46714](https://github.com/moby/moby/issues/46714)) — Docker tenta limpar tabelas nft que já não existem. Não afeta funcionamento. Documentado na Wiki Troubleshooting.

## [2.1.4] - 2026-05-15

### Adicionado

- **`install-nexus-backup.sh` ganhou pré-flight de conectividade + passo 9 opcional + auto-detecção de IP público.**
  - Pré-flight (antes dos 9 passos): testa `getent ahosts github.com`, `curl https://github.com` e detecta IP público via `https://api.ipify.org`. Falha cedo com mensagem clara se algo crítico estiver fora — em vez de só descobrir no passo 3 (clone) que a rede não tá pronta.
  - **`DB_EXPORT_KEY` (Fernet) agora é gerado automaticamente** no `.env` — antes faltava, fazendo o export `.nxbak` (feature v2.0.0) ficar desabilitado por default em toda instância nova. Documentação completa do significado no comentário inline do `.env`.
  - **`FTP_MASQUERADE_ADDRESS` auto-preenchido** com o IP público detectado no pré-flight. Resolve o erro de instalação mais comum ("FTP push chega com 0 bytes" → causa: campo vazio). Quando os clientes acessam por outro IP (VPN/NAT), o admin sobrescreve manualmente — comentário no `.env` deixa claro quando revisar.
  - **`GITHUB_TOKEN` + `GITHUB_REPO` incluídos no `.env`** como opcionais (vazio = banner de update desabilitado). Antes o admin precisava saber que existiam e adicionar manualmente.
  - **Backup automático do `.env` existente** em `.env.bak.<timestamp>` quando o script é re-executado numa instância que já tem `.env`. Mantém o comportamento de NÃO sobrescrever, mas garante recovery se alguém editou errado.
  - **Passo 9 (opcional)**: instala `/etc/cron.d/nexus-pg-backup` agendando `pg_dump` diário às 03:30 com retenção de 14 dias. Antes era documentado no INSTALL.md como passo manual; agora o script já oferece.

### Notas técnicas

- Backend e frontend de runtime não mudaram. Servidores existentes (backup.bandaa.net.br, nexus.camon.net.br) **não precisam redeploy** — esta tag só beneficia instâncias novas. O bump existe pra alinhar o INSTALL.md (que referencia "desde a v2.1.4") com uma tag no Git.

## [2.1.3] - 2026-05-15

### Corrigido

- **Falsas-falhas em Mikrotiks com CPU fraca durante `/export` via API.**
  Devices single-core <= 700 MHz (ex.: hAP lite, hEX lite, RB750G) levam
  60-120s pra completar `/export file=tmp.rsc`, com a CPU em 100% e a
  resposta da API bloqueada o tempo todo. O `_connect` usava `timeout=30`
  (socket TCP do librouteros) — passados 30s sem `!done`, o socket
  estourava e o backend marcava `falha` mesmo quando o device eventualmente
  terminava. Mesmo problema afetava `fila.get(timeout=60.0)` na fase de
  upload FTP do Plano C.
  - **Fix:** ler `/system/resource` antes do export (custo ~25ms),
    classificar via `_classificar_device` como **lento** se `cpu_count <= 1
    AND cpu_freq <= 700 MHz`, e **reconectar com `timeout=200`** +
    propagar `timeout_fetch=180.0` pro Plano C nesses casos. Devices
    normais seguem com timeouts legados (30s conexão, 60s fetch), sem
    impacto de performance.
  - **Observabilidade:** quando um device é classificado como lento, o
    backend loga em nível INFO:
    ```
    Device id=N board=X classificado como LENTO (cpu=1 core(s), 650 MHz) —
    reconectando com timeout estendido (200s) e fetch_timeout=180s
    ```
    Pra ver quais devices estão usando o caminho estendido:
    ```
    docker logs nexus-beta-backend-1 2>&1 | grep "classificado como LENTO"
    ```

## [2.1.2] - 2026-05-15

### Adicionado

- **Checagem preventiva de NAND nos backups via API Mikrotik.** Após cada
  backup bem-sucedido via Plano C (`/export file=tmp.rsc` + `/tool/fetch`),
  lê `/system/resource` e dispara `log.warning` se NAND livre < 10% (threshold
  hardcoded em `_NAND_THRESHOLD_PCT`). Permite observar devices chegando no
  limite antes do `/export` começar a falhar por falta de espaço, sem
  poluir o canal Telegram com avisos preventivos (decisão consciente —
  só docker logs).
  - Best-effort: qualquer falha na checagem é silenciosa (log.debug). O
    backup já rodou com sucesso quando esta função é chamada, então
    nenhum erro aqui pode regredir o resultado.
  - Pra observar em prod:
    ```
    docker logs nexus-beta-backend-1 2>&1 | grep "NAND com"
    ```

## [2.1.1] - 2026-05-15

### Corrigido

- **Cleanup de arquivos `nexus-api-*.rsc` no Mikrotik não removia nada.**
  Bug crítico re-detectado em prod (CRS328-Asa_Norte ID 52 da empresa Camon)
  após o módulo Operações em massa ser deployado e o usuário rodar
  `cleanup_orfaos` 2× — em ambas a UI reportou "10 arquivos removidos",
  mas o `inventario` subsequente mostrou que o device continuava com 13%
  de espaço livre (mesmo estado de antes). Causa raiz:
  - `librouteros` retorna iterator preguiçoso — chamar `api("/file/remove",
    ...)` sem `list()` cria o objeto Query mas NUNCA envia o comando pro
    device. A função `_cleanup_orfaos_nexus` e a `_try_remove_file` em
    `services/mikrotik_api.py` esqueceram do `list()` que outras chamadas
    do mesmo arquivo já usam (existe inclusive um comentário em
    `aplicar_config_padrao` documentando o mesmo bug de 2026-05-11 em
    `/system/clock/set`).
  - Impacto silencioso: a memória NAND dos Mikrotiks foi acumulando órfãos
    de TODO Plano C (`/export file=tmp.rsc` + `/tool/fetch upload=yes`)
    desde a v1.4.4 quando o Plano C foi introduzido. CRS328 chegou a 12
    arquivos órfãos sem ninguém notar — 16 MB de NAND saturada faz o
    `/export` falhar e leva ao "device acumula configs em 86% mas não
    backupa mais".
  - Fix: envolver `api("/file/remove", ...)` em `list()` nas duas funções.
    Após esse hotfix, cleanup retorna número real de arquivos removidos
    E o `_try_remove_file` no finally do Plano C funciona — fluxo de
    backup por API não deixa mais lixo na NAND.

## [2.1.0] - 2026-05-15

### Adicionado

- **Módulo Operações Mikrotik em massa.** Nova aba "Operações" no menu lateral
  (admin/admin_empresa/operador) permite selecionar N devices Mikrotik com
  protocolo `api` e executar ações curadas em paralelo, sem precisar abrir
  Winbox/SSH device por device.
  - **Catálogo de 8 ações:**
    - *Read-only:* `checar_versao` (firmware + board + arch), `listar_usuarios`
      (nome + group + last-logged-in), `inventario` (modelo + uptime + /file
      livre).
    - *Configuração (write idempotente):* `adicionar_user` (com group/senha),
      `configurar_snmp` (community + trap-target + contact + location),
      `cleanup_orfaos` (remove `nexus-api-*.rsc` órfãos da NAND — reusa o
      `_cleanup_orfaos_nexus` do `mikrotik_api.py` como fonte única da verdade).
    - *Destrutivo:* `remover_user` (com bloqueio: recusa remover o próprio
      usuário usado pelo NEXUS pra conectar via API — senão a próxima coleta
      ficaria órfã).
    - *Admin master only:* `comando_livre` — executa qualquer comando librouteros
      (ex.: `/system/identity/print`). Bloqueia regex de comandos destrutivos
      (`reset-configuration`, `factory-reset`, `system/reboot`, `*/remove`)
      como última linha de defesa antes de mandar pro device.
  - **Paralelismo:** orquestrado por `asyncio.gather` + `Semaphore(5)` no router.
    Cap fixo (mais conservador que o scheduler AIMD) porque é one-shot manual
    sem critério adaptativo — 5 cobre bem sem saturar o servidor.
    `librouteros` é síncrono; cada chamada vai pra `asyncio.to_thread` pra não
    travar o loop do FastAPI.
  - **Auditoria — nova tabela `operacao_massa_log`:** cada execução grava 1 row
    ANTES de disparar os workers (rastreabilidade garantida mesmo se o backend
    crashar no meio). Campos: `acao`, `usuario_id/nome` (snapshot), `empresa_id`,
    `device_ids` (JSON), `params` (JSON, com senha sanitizada → "***"),
    `resultados` (JSON `{device_id: {status, output, duracao_ms}}`), totais e
    timestamps. Frontend tem botão "Histórico" com drill-down por execução.
  - **Frontend (`pages/Operacoes.jsx`):**
    - Lista filtrável de devices com checkboxes + "marcar todos visíveis".
    - Dropdown de ações com badges CHECK/CONFIG/DESTRUTIVO.
    - Form dinâmico de parâmetros (tipo text/password/select/textarea) lido do
      catálogo no próprio componente.
    - Modal de confirmação dupla pra destrutivas: usuário digita "CONFIRMAR" ou
      "EXECUTAR" (palavra varia por ação).
    - Tabela de resultado com **falhas no topo** (mesma lógica do modal de
      scheduler v2.0.2) e linhas colapsáveis com output `<pre>`.
  - **Segurança:**
    - `comando_livre` só aparece no dropdown e é aceito pelo backend pra `role=admin`.
    - Devices fora de escopo (não-master tentando rodar em empresa alheia)
      filtrados pelo router antes da execução.
    - Devices inelegíveis (não-Mikrotik ou não-API) viram falhas registradas
      no resultado com motivo explícito — não causam erro 400 pra toda a
      operação.

### Notas técnicas

- Schema migration é automática via `Base.metadata.create_all` no lifespan
  do FastAPI — não exige `ALTER TABLE` manual.
- Endpoint `GET /api/mikrotik-bulk/acoes` permite ao frontend descobrir
  dinamicamente quais ações o user atual pode rodar (filtra master-only).

## [2.0.2] - 2026-05-12

### Corrigido

- **Dedupe diário do push apagava silenciosamente as falhas do scheduler.**
  `services/push_backup.py:processar_upload` fazia `DELETE FROM backups
  WHERE device_id=N AND criado_em >= inicio_dia` antes de inserir o backup
  push, pra evitar acumular múltiplos arquivos do mesmo dia. Cenário do bug:
  1. Scheduler dispara Plano C (API Mikrotik + `/tool/fetch upload=yes`).
  2. Mikrotik demora, scheduler timeout 60s, marca como `status='falha'`
     com `log_scheduler_id=N` — row criada e commitada no banco.
  3. Mikrotik finalmente termina o `/tool/fetch` e empurra o `.rsc` pro
     FTP server interno do Nexus.
  4. FTP server não acha mais entrada na `_pending_api_uploads` (já passou
     o timeout), cai pra `processar_upload` regular.
  5. **Dedupe deletava a row de falha** que o scheduler tinha criado, e
     inseria o push como `sucesso` com `log_scheduler_id=NULL`.
  Resultado: `log_scheduler.falhas` mostrava N > 0 mas o detalhe da run não
  tinha NENHUM row de falha pra exibir — admin via "7 falhas" no header mas
  zero detalhes no modal. Validado em prod: Camon run id=3 tinha
  `falhas=7` e zero rows com `log_scheduler_id=3 AND status='falha'`.
  - Fix: dedupe agora preserva falhas (`AND status='sucesso'` no WHERE do
    DELETE). Falhas do dia ficam no histórico pra investigação, mesmo quando
    push posterior entrega sucesso. Retenção (offset 7) continua aplicando
    igualmente — limita acúmulo se device ficar em loop falhando.

### Mudado

- **Modal "Detalhes da execução do scheduler" prioriza falhas visualmente.**
  Antes mostrava todos os backups numa lista linear (com falhas no topo via
  `ORDER BY status` no backend), mas com 30+ devices os sucessos empurravam
  as falhas pra fora da tela. Agora:
  - **Falhas** sempre no topo, expandidas, com badge "N falhas — requer
    atenção" em vermelho. Falhas sem `erro` registrado mostram "(sem detalhe
    de erro registrado)" em vez de só o nome silencioso.
  - **Sucessos** em accordion colapsável — fechado por default quando há
    falhas (admin foca no que importa), aberto quando todos foram sucesso.

## [2.0.1] - 2026-05-12

### Corrigido

- **Regressão crítica do scheduler v3 (paralelismo): NENHUM backup automático
  era inserido no banco.** No refactor pra paralelismo, cada worker abre uma
  session SQLAlchemy própria (correto — sessions async não toleram uso
  concorrente entre coroutines). Mas o `LogScheduler` da run era apenas
  `flushed` (não commitado) na session principal antes dos workers
  iniciarem — então as sessions paralelas, em transações próprias, **não
  enxergavam o row do log_scheduler** quando tentavam inserir backups
  referenciando `log_scheduler_id` (FK). Resultado: `ForeignKeyViolationError`
  em todos os inserts da janela.
  - Validado em prod: log_scheduler id=18 do backup.bandaa.net.br tinha
    `total=4, sucessos=0, falhas=0` — devices coletaram via Paramiko, mas
    nenhum row entrou em `backups`.
  - Fix: trocar `await db.flush()` por `await db.commit()` + `db.refresh()`
    ANTES de spawnar os workers. `expire_on_commit=False`
    (`database.py`) garante que `log_run` continua válido pra updates
    posteriores na mesma session principal. Mesma adaptação aplicada ao
    `Configuracao` (consistência — se algum worker dependesse de config
    recém-criada, mesma race aplicaria).
  - Coletas manuais (botão "Executar backup" no painel) **NÃO eram
    afetadas** — usam `log_scheduler_id=NULL` e session do request HTTP.

## [2.0.0] - 2026-05-11

Major release consolidando o ciclo iniciado pós-v1.4.9. Mudanças significativas
no scheduler diário (paralelismo adaptativo + delay AIMD), nova feature de
segurança em profundidade (backup criptografado do banco em `.nxbak`), e
melhorias de UI espalhadas por várias páginas. Banco evolui com migrations
idempotentes — instância existente sobe direto sem intervenção manual além
de gerar e adicionar `DB_EXPORT_KEY` no `.env`.

### Por que MAJOR

Critérios de bump documentados na política deste arquivo:

- **Variável obrigatória nova no `.env`** — `DB_EXPORT_KEY` é necessária pra
  feature de export do banco funcionar. Sem ela o painel mostra aviso e o job
  diário não roda. Tecnicamente é "obrigatória" pra usar a feature, opcional
  pra subir a app.
- **Comportamento do scheduler mudou substancialmente** — antes era sequencial
  puro 1 device por vez; agora é paralelo adaptativo com 2 pools e telemetria
  psutil. Operadores que monitoram a janela noturna podem estranhar o
  comportamento novo.
- **`.nxbak` é formato proprietário versionado** — `format_version=0x01`
  introduzido aqui; futuras mudanças do layout binário vão exigir nova major.

### Adicionado

**Backup criptografado do banco (`.nxbak`)**

- Snapshot diário (default 03:30) com TODOS os backups armazenados, num arquivo
  binário com magic header `NEXUSBACKUPv200\n`, payload comprimido (gzip nível
  9) e criptografado (Fernet com `DB_EXPORT_KEY` dedicada — separada da
  `ENCRYPTION_KEY` por defesa em profundidade).
- Retenção 7 arquivos (igual aos backups normais).
- Upload semanal opcional pra "nuvem de segurança" externa via SFTP (paramiko)
  ou FTP (stdlib `ftplib`), configurável: host/porta/user/senha/path/dia/hora.
- Audit + alerta Telegram (categoria `falha_backup`) quando export ou upload falham.
- 3 endpoints novos: `GET/PUT /api/settings/db-export`, `POST .../run-now`,
  `POST .../upload-now`.
- UI: novo card "Export do Banco (.nxbak)" em Settings (admin master).
- Formato documentado em [docs/NXBAK_FORMAT.md](docs/NXBAK_FORMAT.md) — spec completa pra implementar
  a ferramenta externa de leitura (em projeto separado).

**Paralelismo adaptativo no scheduler diário**

- 2 pools independentes de workers: **API** (cap default 4) e **SSH/Telnet**
  (cap default 2). Pools separados porque API binária é leve;
  Paramiko/Netmiko são pesados (1 thread + socket por sessão).
- Algoritmo **AIMD** (Additive Increase Multiplicative Decrease, inspirado em
  TCP): +1 worker no pool após 3 sucessos consecutivos sem stress;
  `target //= 2` (mínimo 1) quando CPU média > limite OU RAM > limite.
- Telemetria via `psutil` em task asyncio paralela: sample a cada 5s, média
  móvel de 60s. Detecta CPU/RAM acima dos limites (defaults 80%/80%) e dispara
  corte imediato dos pools.
- Hard cap absoluto = 8 workers por pool (mesmo se admin tentar configurar
  mais, é truncado no banco). Protege contra estouro de sockets/threads.
- `run_backup()` agora roda via `asyncio.to_thread()` — paralelismo REAL do
  Paramiko/Netmiko/librouteros (antes bloqueava o event loop mesmo em
  "sequencial sync").
- Cada worker abre sua própria session SQLAlchemy (sessions async não toleram
  uso compartilhado entre coroutines concorrentes).
- Auto OFF (`backup_workers_auto=false`): trava em 1 worker, comportamento
  legado pré-v2.x.

**Delay adaptativo entre coletas (parte do scheduler v2 absorvido aqui)**

- Após cada device, scheduler espera `max(delay_min, fator × duração_anterior)`
  antes do próximo. Defaults: 10s de piso, fator 0.2 (backup de 5min → 60s
  de pausa, backup de 30s → fica no piso).
- Detecção de pico: duração > `fator_pico × média histórica` (últimos 10
  backups do mesmo device) dispara `WARNING` em `docker logs` + alerta
  Telegram. Default `fator_pico=3.0`.
- Detecção de redução suspeita de tamanho: backup novo < 50% do último
  sucesso (mesma extensão de `nome_arquivo`, replicando regra do frontend) —
  registra alerta sem converter em falha (config pode ter genuinamente
  encolhido).
- `Backup.duracao_segundos` (coluna nova) preenchido tanto pelo scheduler
  quanto pelo botão "Executar backup" — alimenta a média histórica usada
  na detecção de pico.

**Página Novidades (changelog amigável)**

- Novo item no menu lateral "Novidades" (ícone Sparkles, visível a todos
  os roles).
- Página lista mudanças em linguagem leiga, agrupadas por tipo
  (novidade/melhoria/correção) com chips de filtro e contadores.
- Fonte de dados estática em [frontend/src/data/changelog.js](frontend/src/data/changelog.js) — pra adicionar
  entrada nova, é só empurrar no topo do array.
- Complementa o `CHANGELOG.md` técnico (que continua sendo a fonte detalhada
  pro time de dev).

**Ordenação clicável da tabela de Dispositivos**

- Cabeçalhos (`ID`, `Nome`, `IP`, `Tipo`, `Fabricante`, `Protocolo`, `Ativo`,
  `Último backup`) ficam clicáveis com seta ↑/↓ visual. Click na coluna ativa
  inverte direção; click em coluna nova começa em "desc" pra ID/Último backup
  e "asc" pras textuais.
- Default = ID descendente (último cadastrado no topo) — facilita gestão
  diária. Botão "Limpar" reseta filtros + ordenação.
- Ordenação de IP é numérica por octeto (192.168.1.2 vem antes de 192.168.1.10),
  não lexicográfica. IPv6 vai sempre pro fim, independente da direção.
- Texto usa `localeCompare(pt-BR, numeric: true)` — "R2" antes de "R10",
  dígitos antes de letras.

**Checkbox "Somente backup manual" no cadastro de dispositivos**

- Novo campo `Device.backup_manual_apenas` (default `false`). Quando `true`:
  - Scheduler diário PULA o device (filtro adicionado em `executar_backups`).
  - Botão "Executar backup" no painel continua funcionando normal.
  - Retenção (`BACKUP_RETENTION_DAYS`) continua valendo — mesmo em manual
    o histórico fica limitado.
- UI: checkbox no modal Novo/Editar device, dentro da seção de protocolo
  (só aparece pra SSH/Telnet/API — push não tem "rodar manual"). Badge
  amber "manual" ao lado do nome na tabela sinaliza que o device está
  fora do agendamento.

**Botão "Excluir falhas" em Backups**

- Apaga em massa todos os backups com `status='falha'` no escopo do usuário.
  Útil pra limpeza após queda de internet sistêmica que deixa dezenas de
  devices falhos por dias.
- Endpoint novo `DELETE /api/backups/?status=falha` (também aceita
  `?status=sucesso` se admin quiser). Admin master pode passar `empresa_id`
  opcional pra filtrar; admin_empresa fica restrito automaticamente à
  própria empresa.
- Audit consolidado: 1 evento `backup_removido` com contagem total
  (em vez de N eventos, evita poluir).
- Visível só pra `admin` e `admin_empresa`, e somente quando há pelo menos
  1 falha no escopo do usuário.

### Mudado

- **Scheduler `_backup_device`** retorna agora `tuple[ok, erro, duracao_seg,
  alerta_tipo, alerta_msg]` em vez de só `tuple[ok, erro]`. Chamadas externas
  ao scheduler service não existem fora dele, mas se alguém customizou
  internamente vai precisar adaptar.
- **`LogScheduler`** ganha 7 colunas novas: `duracao_total_segundos`,
  `duracao_media_segundos`, `picos_detectados`, `alertas_tamanho`,
  `workers_max_atingido_api`, `workers_max_atingido_ssh`, `tempo_sob_stress_seg`.
- **`Configuracao`** ganha 16 colunas novas (3 do delay v2 + 5 do paralelismo
  + 13 do db-export).
- **`Backup`** ganha coluna `duracao_segundos`.

### Compatibilidade

- Migrations idempotentes (`ADD COLUMN IF NOT EXISTS`) — instância existente
  sobe direto sem rodar nada manual.
- `DB_EXPORT_KEY` no `.env` é **opcional pra subir**, **obrigatória pra usar**
  a feature de export. Sem ela, o card no painel mostra aviso explícito e o
  job diário pula silenciosamente.
- Devices/backups/logs existentes continuam funcionando sem mudança.
- API endpoints existentes mantêm contrato — apenas novos endpoints adicionados.

### Notas de operação

- **Anotar a `DB_EXPORT_KEY` em local separado do servidor** (cofre digital,
  password manager). Sem essa chave, os `.nxbak` ficam **irrecuperáveis** —
  por design, é a defesa em profundidade que essa feature oferece.
- Após primeira boot da v2.0.0, validar com botão "Exportar agora" em
  Settings que o `.nxbak` é gerado corretamente.
- O upload semanal pra nuvem de segurança fica OFF por default — admin
  precisa configurar host/user/senha conscientemente antes de ligar.
- Defaults do paralelismo (`api=4, ssh=2, cpu_lim=80, mem_lim=80`) são
  conservadores. Validar 1-2 noites antes de subir caps.

## [1.4.9] - 2026-05-11

### Corrigido

- **`aplicar_config_padrao` não rodava na criação de device API mesmo com v1.4.8.** Investigação no device 54 (CCR1009): a função funcionava perfeitamente quando rodada manualmente via Python (NTP + timezone aplicados), mas via endpoint `POST /api/devices/` não atingia o equipamento. Duas causas em paralelo:
  - **(a) Logs do nível INFO eram suprimidos** pelo uvicorn default — `log.info("Device X: aplicar_config_padrao ok=...")` nunca aparecia, então parecia que a função não rodava. Trocado pra `log.warning` (que aparece) + linha explícita "iniciando aplicar_config_padrao" antes do try.
  - **(b) Passar objeto SQLAlchemy AsyncSession entre threads via `to_thread` é frágil** — `device` carregado em AsyncSession podia disparar lazy load em thread separada e falhar silenciosamente. Refatorado: `aplicar_config_padrao(device_id)` agora aceita só o ID e abre `SyncSession` internamente pra recarregar — autossuficiente, sem dependência da sessão async do request.
- **Timeout aumentado de 15s pra 20s** pra cobrir Mikrotik em LAN remota com latência alta.

## [1.4.8] - 2026-05-11

### Corrigido

- **NTP cliente não era aplicado na criação de device API** (v1.4.7) — `aplicar_config_padrao` chamava `api("/system/ntp/client/set", ...)` direto, sem consumir o iterator retornado. Em `librouteros 3.4`, o retorno de `api(cmd, **kwargs)` é um `Iterator[Dict]` **lazy** — comando só é efetivamente enviado ao Mikrotik quando o iterator é iterado (consumido). Fix: envolver todas as chamadas com `list(...)` pra forçar o envio. Validado em prod no device 53 (RB3011): com `list()`, `primary-ntp` foi setado corretamente.
- **Frontend mostrava modal de "Credencial FTP gerada" ao criar device API.** Era confuso porque essa cred é detalhe interno do NEXUS (backend usa pra disparar o `/tool/fetch upload` do Plano C de coleta) — admin não precisa configurar nada manualmente no equipamento. Esconde o modal quando `protocolo=api`; continua mostrando pros fluxos `ftp_push`/`sftp_push` onde a cred é necessária pro admin configurar no device.

## [1.4.7] - 2026-05-11

### Adicionado

- **Cleanup de arquivos órfãos `nexus-api-*.rsc`** no `/file/` do Mikrotik antes de cada Plano C. Protege contra acúmulo na memória NAND do device (16-64 MB típicos) quando coletas anteriores falham no meio do caminho — timeout no `/tool/fetch`, erro de rede, etc — e o cleanup do try/finally não rodou. Roda sempre no início, idempotente, loga quantos arquivos foram removidos.
- **Aplicação automática de NTP cliente + timezone na criação de device API**. Quando um device com protocolo=api é cadastrado, abre conexão API e aplica:
  - `time-zone-name=America/Sao_Paulo time-zone-autodetect=no`
  - `ntp client enabled=yes` com `servers=<FTP_MASQUERADE_ADDRESS>` (v7) ou `primary-ntp=...` (v6) — tenta v7 primeiro, fallback automático pra v6.
  - Best-effort: se falhar (device offline, permissão), loga warning mas não bloqueia a criação. Roda com timeout 15s em thread separada pra não travar o request. Aplica só na CRIAÇÃO — não em todo backup — pra não mexer em config do device sem ação consciente do admin.

## [1.4.6] - 2026-05-11

### Corrigido

- **Plano C falhava com `input does not match any value of mode`** ao chamar `/tool/fetch upload=yes mode=sftp`. RouterOS aceita só `ftp`, `http`, `https` e `scp` em `mode=` — **não existe `sftp`**. Mudado pra `mode=ftp` (porta 21) — nosso FTP server interno já trata o upload. SCP não usado porque exige subsystem `exec` no paramiko (não implementado).
- **FTP server agora aceita protocolo=api** (mesma adaptação feita no SFTP server em v1.4.4): whitelist de IP é pulada quando `protocolo=api`, cred única gerada por device garante autorização. Sem isso, Mikrotik que abrisse conexão FTP no NEXUS levaria `AUTH_FAIL: usuário FTP inexistente ou device desabilitado`.
- **Hook do Plano C também no FTP server** — antes só SFTP tinha o hook `has_pending_api_upload` / `deliver_api_upload`. Agora FTP também detecta e entrega na fila do Plano C em vez de chamar `processar_upload` (evita dedupe diário apagando históricos).

### Sabidos

- Mikrotik exige policy `write,ftp` no grupo do usuário (além de `read,sensitive`) pra `/tool/fetch upload` funcionar. Grupo `read` default NÃO basta. Comando:
  ```
  /user group add name=nexus-backup policy=read,write,test,sensitive,ftp,api
  /user set <user> group=nexus-backup
  ```

## [1.4.5] - 2026-05-11

### Corrigido

- **Plano C de coleta API (v1.4.4) tinha bug crítico de perda de dados**: o caminho antigo deixava a pipeline `processar_upload` criar o backup push normalmente e depois deletava o row. Mas o `processar_upload` faz **dedupe diário** que **apaga TODOS os backups do mesmo dia** antes de inserir o novo — então o "deletar depois" perdia todos os históricos do dia também. Refatorado: usa fila em memória (`_pending_api_uploads`) — quando `run_backup_via_api` dispara `/tool/fetch`, registra device_id na fila ANTES; `processar_upload_local` no SFTP server checa essa fila ao receber upload e, se o device tem entry, entrega conteúdo direto na fila e **pula** `processar_upload` (não cria backup push, não aciona dedupe). Pipeline normal de push continua funcionando inalterada pra `sftp_push` "puro".

## [1.4.4] - 2026-05-11

### Adicionado

- **Plano C de coleta API Mikrotik: upload SFTP iniciado pelo equipamento.** Substitui o Plano B (que dependia do atributo `.contents` em `/file/print`, limitado a ~4KB pelo firmware). Fluxo: backend conecta via API → `/export file=tmp.rsc` no Mikrotik → backend dispara `/tool/fetch upload=yes mode=sftp address=<NEXUS> port=22 user=<gerado> password=<gerado>` → Mikrotik conecta no SFTP server interno do NEXUS e empurra o arquivo → pipeline padrão de push processa → backup criado. `run_backup_via_api` polla o banco esperando esse backup (timeout 60s), lê o conteúdo, deleta o row do push (pra evitar duplicação) e retorna o conteúdo pro caller criar Backup normal com origem='manual'. Resolve o problema de `.contents` vazio em configs grandes (CRS328 e similares).
- **Auto-gerar credencial SFTP em devices com protocolo=api**: ao criar, backend gera `ftp_user` + `ftp_senha_enc` (mesmo fluxo do `sftp_push`). UI já mostra a senha em texto puro UMA VEZ na resposta da criação (modal genérico de credencial reaproveitado). Necessário pro Plano C — Mikrotik usa essa cred pra fazer o upload.
- **SFTP server aceita protocolo=api**: além de `sftp_push`. Whitelist de IP de origem é pulada quando `protocolo=api` (o admin não pré-cadastra CIDR no fluxo API — o backend dispara o upload sob demanda e a credencial única por device já garante autorização).

### Removido

- **Plano B (`_export_via_arquivo`)** que lia `.contents` de `/file/print`. Era frágil (limite ~4KB do firmware) e sempre falhava em configs reais. Plano A (export direto) cobre configs pequenas; Plano C (upload SFTP) cobre todo o resto.

## [1.4.3] - 2026-05-11

### Corrigido

- **Coleta API Mikrotik intermitente em CRS328 e routers com disco lento.** Plano B (`/export file=tmp` + ler arquivo) tinha timeout de 5s pro arquivo aparecer com conteúdo populado — insuficiente em alguns firmwares. Aumentado pra **30s** com poll a cada 500ms. Mensagem de erro agora distingue dois casos: (1) arquivo nunca apareceu (problema de permissão ou timeout maior); (2) arquivo apareceu com size>0 mas `.contents` veio vazio (firmware Mikrotik limita conteúdo retornado via API — geralmente >4KB).
- **Diagnóstico do Plano A** (`/export` direto via API): quando vier 0 replies ou replies sem chaves conhecidas (`ret`/`line`/`message`), agora loga `WARNING` com chaves observadas no reply pra ajudar diagnóstico de firmwares específicos. Antes caía silencioso pro Plano B sem rastro.

### Sabidos

- Erro `not enough permissions (9)` no `/export` via API significa que o usuário do Mikrotik está num grupo sem permissão de leitura sensível. Solução: criar grupo customizado (`/user group add name=nexus-backup policy=read,test,sensitive,api`) e atribuir esse grupo ao usuário de backup. Grupo `read` default NÃO inclui `sensitive`.

## [1.4.2] - 2026-05-10

### Corrigido

- **Coleta via API Mikrotik falhava com `Path.__call__() missing 1 required positional argument: 'cmd'`.** O `librouteros==3.4.0` mudou a interface: `api.path('/export')(**kwargs)` não funciona mais — `Path.__call__` exige `cmd` posicional (representando o sub-comando dentro da path). Forma correta é `api('/cmd', **kwargs)` direto, que aceita qualquer comando absoluto. Refatorado `services/mikrotik_api.py` pra usar a API top-level callable em todos os pontos: `_export_via_command`, `_export_via_arquivo` (com `/export`, `/file/print`, `/file/remove`). Substituído `Path.select().where()` por loop linear em `/file/print` no `_find_file` — simples e funciona em qualquer versão do firmware.

## [1.4.1] - 2026-05-10

### Corrigido

- **Trocar protocolo no formulário agora SEMPRE reseta a porta pro default** do novo protocolo. Antes preservava porta custom (ex.: SSH 2399 → API ficava em 2399 e quebrava com `Unknown control byte 0xff` porque o librouteros tentava falar API binária com SSH na outra ponta). Validado em prod: CRS317_26_Fibra com SSH em 2399 → mudou pra API → porta ficou 2399 → quebrou. Se usuário quiser porta custom, edita o campo Porta DEPOIS de escolher o protocolo. Mesma simplificação aplicada no toggle TLS da API Mikrotik (sempre vai pra 8728/8729 conforme o checkbox).

## [1.4.0] - 2026-05-10

### Adicionado

- **Novo protocolo de coleta: `api` — RouterOS API binária** para Mikrotik v6 e v7. Convive com SSH/Telnet existentes (não substitui). Selecionável no formulário de Novo/Editar Dispositivo somente quando fabricante é `mikrotik` ou `mikrotik_v7`. Vantagem sobre SSH: não exige abrir SSH no equipamento, sem problemas de detecção de prompt dinâmico, e a coleta usa o canal API nativo do RouterOS.
- **TLS configurável por device** via checkbox quando protocolo=API. Plain = porta 8728 (default), TLS = porta 8729. TLS aceita self-signed (Mikrotik não vem com cert publicamente confiável — admin pode gerar self-signed via `/certificate`). Badge na lista de devices mostra `TLS` sutilmente quando ativo.
- **`backend/services/mikrotik_api.py`** dedicado, usando `librouteros==3.4.0`. Implementa Plano A (export direto via API — alguns firmwares retornam linhas em replies) com fallback automático pro Plano B (escreve arquivo temp + lê via `/file/print detail` + remove). Compatível com `/export show-sensitive=yes` em v7 (com fallback se firmware rejeitar).
- **Schema migration** (idempotente): novo valor `api` no enum `protocolo` + coluna `api_tls BOOLEAN NOT NULL DEFAULT FALSE` em `devices`.

### Sabidos

- API binária Mikrotik **não suporta autenticação por chave SSH** (só usuário + senha). O frontend bloqueia auth por chave quando protocolo=API e o backend recusa explicitamente.
- O `/export` via API binária tem variações por firmware (alguns retornam linhas no reply, outros só escrevem arquivo). O fallback automático cobre a maioria dos casos, mas firmwares muito antigos podem falhar — nesses casos, manter SSH como protocolo.

## [1.3.4] - 2026-05-10

### Corrigido

- **Escudo do logo aparecia deslocado pra esquerda** em relação ao título `NEXUS BACKUP` no Login, mesmo com `mx-auto`. Causa: centro de massa visual do escudo na imagem original ficava 134px à esquerda do centro geométrico do canvas — `mx-auto` centraliza a IMAGEM mas não o conteúdo dentro dela. Fix: regenerar assets calculando o centro de massa do alpha (numpy) e expandindo o canvas pra que o escudo fique simetricamente posicionado dentro do PNG. Agora `mx-auto` resulta em escudo visualmente alinhado com o h1.

## [1.3.3] - 2026-05-10

### Corrigido

- **Logo no Login parecia "muito acima" do título** `<h1>NEXUS BACKUP</h1>` porque a imagem original tinha o texto "NEXUS BACKUP / by innetsolutions" embutido no rodapé, criando duplicação visual. Regenerados todos os assets (`nexus-logo.png`, `nexus-logo.webp`, `nexus-logo-sm.png`, `favicon.*`) com auto-detecção do gap entre escudo e texto (Pillow + numpy) — só o escudo é mantido, o texto da imagem é descartado. O h1 do código vira o único texto "NEXUS BACKUP" visível. CSS atualizado pra preservar aspect ratio real do escudo (763×588 source → `h-36 sm:h-44 w-auto` no Login, `h-8 w-auto` no Sidebar).

## [1.3.2] - 2026-05-10

### Corrigido

- **Logo na tela de Login agora aparece maior e mais centralizado verticalmente.** Em v1.3.1 ficava pequeno (`w-28` / 112px) e colado no topo — feedback do usuário pedindo proporção próxima dos 30% da viewport. Aumentado pra `w-48 sm:w-56` (192px mobile / 224px desktop) e adicionado `mt-16` no bloco do logo pra empurrar pra mais baixo (perto da posição do `Shield` original do v1.3.0).

## [1.3.1] - 2026-05-10

### Adicionado

- **Logomarca oficial do NEXUS BACKUP** substituindo o ícone genérico `Shield` (lucide-react) que era usado como placeholder. Aplicada no header do Sidebar (versão 128px, ~12 KB) e na tela de Login (versão 512px, ~120 KB). Original 1.5 MB foi otimizada com Pillow (`optimize=True`, LANCZOS resampling) — redução de >90% sem perda visual.
- **Favicon** (`favicon.ico` multi-res + PNG 16/32) configurado no `index.html`. Antes a aba do browser mostrava ícone default do Vite.
- **Crédito autoral no rodapé do Sidebar**: *"Idealizado e testado por Vagner — [innetsolutions.com.br](https://innetsolutions.com.br)"*. Link sutil em hover sky-blue, abre em aba nova.

## [1.3.0] - 2026-05-10

### Mudança importante

- **Fabricante ZTE renomeado para "ZTE C3XX"** no painel (família C300/C320 com firmware ZXA10), pra diferenciar da nova linha **ZTE C6XX Titan** que tem firmware diferente e ainda não tem suporte. Enum interno permanece `zte` — só o label visível muda.
- **Backup ZTE C3XX agora suporta APENAS Telnet.** SSH foi removido após sequência de releases (v1.2.4 → v1.2.9) tentar resolver truncamento do `show running-config` em configs com muitas ONUs. Diagnóstico via `ZTE_DEBUG_LOG=1` confirmou que o firmware ZXA10 tem rate-limit/flow-control interno no canal SSH que pausa o stream por mais de 2 min entre seções — coleta sempre retorna parcial mesmo com `IDLE_TIMEOUT=180s`. Telnet não tem essa pausa e foi validado entregando running-config completo (~6200 linhas, terminando em `end`). Quando criar/editar device ZTE no painel, **SSH é escondido do select de protocolo** e ao escolher fabricante ZTE o protocolo é forçado pra Telnet. Backend retorna falha clara `"ZTE C3XX: backup via SSH não é suportado nesta versão..."` se algum device ZTE acabar com protocolo SSH (ex.: criado em versão anterior).

### Adicionado

- **Backend `_run_zte_netmiko`** (v1.2.4): coleta dedicada pra ZTE usando `write_channel`/`read_channel` em loop manual, igual ao Huawei/Datacom — necessário porque `send_command` do Netmiko quebra com `Pattern not detected: 'ZXAN#'` quando o hostname da OLT difere do default ou o output é grande.
- **Early-exit por linha `end`** no loop de coleta ZTE (v1.2.7): detecta a marca natural de fim do `show running-config` ZTE/Cisco-like e encerra o loop na hora, sem depender de idle timeout.
- **Driver Telnet dedicado** `zte_zxros_telnet` (v1.2.8): antes usava `cisco_ios_telnet` que auto-envia `terminal width 511` no session_preparation e a ZTE responde `Invalid input`.
- **Debug opcional via `ZTE_DEBUG_LOG=1`** (v1.2.6): grava stream raw da sessão Netmiko em `/tmp/zte_session_<device_id>.log` no container backend. Mantido na release pra diagnósticos futuros.

### Corrigido

- **Modal "Editar Dispositivo" agora esconde protocolos push (SFTP/FTP/TFTP)** do select de protocolo. Antes, ao editar um device SSH/Telnet, o usuário podia acidentalmente mudar pra push e quebrar a coleta — esses protocolos só fazem sentido no fluxo "Novo via Upload" que tem todo o setup de credencial gerada. Devices que JÁ são push têm o select inteiro escondido na edição (só permitem editar nome, IP, CIDR — não trocar o modo).

## [1.2.9] - 2026-05-10

### Corrigido

- **Backup SSH de ZTE C320 ainda truncava (~5820 linhas) mesmo com early-exit por `end` do v1.2.7.** Diagnosticado via `ZTE_DEBUG_LOG=1` (segunda rodada): o stream SSH chega completo no Netmiko (session_log mostra até `end`), mas a OLT C320 **pausa entre 90-120s** no canal SSH entre as seções `pon-onu-mng` (muitas ONUs) e o restante da config — rate-limit/flow-control interno do firmware. Nosso loop saía por `IDLE_TIMEOUT=60s` durante essa pausa, antes do stream final chegar, e o conteúdo aparecia só no session_log do Netmiko via `disconnect()`. Fix: `IDLE_TIMEOUT` agora diferencia por protocolo — `60s` em Telnet (continua bom), `180s` em SSH (cobre a pausa grande da OLT). Telnet validado completo em v1.2.8.

## [1.2.8] - 2026-05-10

### Corrigido

- **Backup Telnet de ZTE C320 falhava com `Pattern not detected: 'terminal width 511' in output`** logo no setup da conexão. Causa: `_run_zte_netmiko` usava `cisco_ios_telnet` como driver Netmiko quando o protocolo era Telnet, e esse driver auto-envia `terminal width 511` no `session_preparation`. A ZTE responde `%Error 20200: Invalid input detected` (não conhece o comando), Netmiko não vê o echo esperado e levanta exception antes mesmo de entrar no loop de coleta manual. Fix: trocar para `zte_zxros_telnet` (driver dedicado da ZTE no Netmiko, conhece a sintaxe certa), tanto em `_run_zte_netmiko` quanto no mapeamento `DEVICE_TYPES_TELNET`.

## [1.2.7] - 2026-05-10

### Corrigido

- **Backup SSH de ZTE C320 ainda truncava em ~5760 linhas mesmo após v1.2.5/v1.2.6** (configs com 6200+ linhas voltavam sem as seções finais `username`, `snmp`, `ntp`, `ssh server`, etc.). Diagnosticado via `ZTE_DEBUG_LOG=1`: a OLT pausa >30s entre as seções `pon-onu-mng` e o resto da config quando tem muitas ONUs, e o loop de coleta saía por `IDLE_TIMEOUT`. Fix: detectar a linha literal `end` (marca natural de fim do `show running-config` ZTE/Cisco-like) via regex e encerrar o loop na hora, sem depender de idle timeout. `IDLE_TIMEOUT` ainda subiu de `30s`→`60s` como fallback pra caso `end` não venha (erro de comando, sessão derrubada).

## [1.2.6] - 2026-05-10

### Adicionado

- **Debug opcional do coletor ZTE via `ZTE_DEBUG_LOG=1` no `.env`** — quando ativo, cada coleta SSH de ZTE grava o stream raw da sessão Netmiko (todo input/output da OLT) em `/tmp/zte_session_<device_id>.log` dentro do container backend. Sobrescreve a cada coleta. Usado pra diagnosticar coleta incompleta — após v1.2.5, configs ZTE C320 grandes ainda vinham truncadas em ~5900 linhas (esperado 20k+) sem exception no backend, indicando que o loop de paginação manual está travando num formato de `--More--` que o regex `_MORE_RE` não captura. Manter desligado em prod normal (verboso, escreve no disco a cada backup).

## [1.2.5] - 2026-05-10

### Corrigido

- **Backup SSH de ZTE C320 vinha incompleto em configs grandes** (~5900 linhas em vez de 20k+). O loop `_run_zte_netmiko` cortava em `IDLE_TIMEOUT=8s`, mas a OLT pausa entre 10-20s por página em runnings grandes pra liberar buffer interno — o loop interpretava a pausa como "fim de saída" e retornava parcial. `IDLE_TIMEOUT` aumentado pra `30s` e `TOTAL_TIMEOUT` de `600s` pra `900s` (15min) pra cobrir configs muito grandes sem corte. Adicionada variante extra `terminal no length` na sequência de desabilitar paginação (cobre firmware ZXA10 com syntax cisco-like estrita).

## [1.2.4] - 2026-05-10

### Corrigido

- **Backup SSH de ZTE ZXR10/ZXA10 (C300/C320/C600)** quebrava com `Pattern not detected: 'ZXAN\#' in output` quando o hostname da OLT não era o default `ZXAN` (ex.: cliente renomeou pra `OLT-camon`) ou quando o `show running-config` era grande o suficiente pra estourar o `read_timeout=60` interno do Netmiko na detecção de prompt. Mesma classe de bug que já tinha resolvido pra Huawei/Datacom: `send_command` do Netmiko depende de match de prompt, e prompt dinâmico/output grande arrebenta. Solução: nova função `_run_zte_netmiko` em `ssh_service.py` que usa `write_channel` + `read_channel` em loop com idle timeout (8s) e total timeout (600s), paginando manualmente o `--More--` ZTE via space. Desabilita paginação tentando `terminal length 0` e `screen-length 0` (cobre ZXR10 e ZXA10 mais antigos).

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

[Não lançado]: https://github.com/vagnerss2011-spec/NEXUS-BETA/compare/v2.0.2...HEAD
[2.0.2]: https://github.com/vagnerss2011-spec/NEXUS-BETA/compare/v2.0.1...v2.0.2
[2.0.1]: https://github.com/vagnerss2011-spec/NEXUS-BETA/compare/v2.0.0...v2.0.1
[2.0.0]: https://github.com/vagnerss2011-spec/NEXUS-BETA/compare/v1.4.9...v2.0.0
[1.2.3]: https://github.com/vagnerss2011-spec/NEXUS-BETA/compare/v1.2.2...v1.2.3
[1.2.2]: https://github.com/vagnerss2011-spec/NEXUS-BETA/compare/v1.2.1...v1.2.2
[1.2.1]: https://github.com/vagnerss2011-spec/NEXUS-BETA/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/vagnerss2011-spec/NEXUS-BETA/compare/v1.1.2...v1.2.0
[1.1.2]: https://github.com/vagnerss2011-spec/NEXUS-BETA/compare/v1.1.1...v1.1.2
[1.1.1]: https://github.com/vagnerss2011-spec/NEXUS-BETA/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/vagnerss2011-spec/NEXUS-BETA/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/vagnerss2011-spec/NEXUS-BETA/releases/tag/v1.0.0
