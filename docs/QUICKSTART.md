# Quickstart — Preparar a VM antes do install

Os 2 passos manuais que você precisa fazer **antes** de rodar `install-nexus-backup.sh`:

1. **Mover o SSH do host pra porta 2288** (a porta 22 vai ficar pro container SFTP do app)
2. **Liberar o acesso do servidor ao repo privado do GitHub** (deploy key)

Pra contexto completo (provisionamento da VM, DNS, certbot, criar admin, etc.) ver [INSTALL.md](INSTALL.md). Este doc cobre só esses 2 pontos.

---

## 1. Mover o SSH pra porta 2288

### Por que

O NEXUS BACKUP recebe `sftp_push` dos equipamentos na porta **22** (OLTs e switches geralmente não aceitam SFTP em porta custom). Então o SSH do host **precisa sair da 22** antes do Docker subir, senão dá conflito.

### ⚠️ Faça pelo console do Proxmox/IPMI, NÃO pelo SSH atual

Se você errar a config do `sshd` e estiver conectado via SSH, vai ficar **trancado pra fora** — só recupera entrando pelo console do hypervisor. Já abra o console antes de começar.

### Passo a passo

```bash
# 1) Backup do sshd_config (pra reverter se precisar)
cp /etc/ssh/sshd_config /etc/ssh/sshd_config.orig

# 2) Troca a linha Port pra 2288 (descomenta se estiver comentada)
sed -i 's/^#\?Port .*/Port 2288/' /etc/ssh/sshd_config

# 3) Valida a config ANTES de reiniciar (se tiver erro, mostra aqui)
sshd -t && echo "config OK"

# 4) Reinicia o SSH
systemctl restart ssh

# 5) Confirma que tá escutando na 2288
ss -tlnp | grep :2288
```

Se o passo 5 mostrar uma linha tipo `LISTEN 0 128 *:2288 *:* users:(("sshd",...))`, está pronto.

### Antes de fechar o console — VALIDE de outro terminal

Abre uma janela nova no seu computador (não fecha o console!) e tenta conectar pela 2288:

```bash
ssh -p 2288 root@<IP_DA_VM>
```

Só feche o console **depois** que esse login funcionar. Se não funcionar, volta no console e reverte:

```bash
cp /etc/ssh/sshd_config.orig /etc/ssh/sshd_config
systemctl restart ssh
```

### Se o firewall já estiver na frente

Se a VM está atrás de um firewall que filtra portas (Proxmox firewall, edge router), libere a **2288 TCP** antes da troca — senão você não consegue conectar nem pelo console.

---

## 2. Acesso ao repo privado do GitHub (deploy key)

### Como funciona

O servidor precisa autenticar no GitHub pra clonar e atualizar o código. Como o repo é **privado**, não dá pra usar `git clone https://...` sem credencial. A abordagem padrão:

- **Deploy key** = par de chaves SSH dedicado a um repo (read-only, opcionalmente read-write). A public key fica cadastrada no GitHub no escopo do repo (não da conta inteira) — se vazar, só compromete esse repo.
- O `install-nexus-backup.sh` **gera a chave automaticamente** no passo 3. Você só precisa **colar a public key** no GitHub.

### Passo a passo

Quando você rodar `bash /tmp/install.sh --interactive` e chegar no **Passo 3 (Clone do repo)**, ele vai fazer 3 coisas:

1. Gerar `/root/.ssh/nexus_deploy_key` (ed25519, sem passphrase) se não existir.
2. Configurar `/root/.ssh/config` pra rotear `github.com` via essa chave.
3. Testar autenticação com `ssh -T git@github.com`.

Na primeira execução, o teste vai **falhar** (a chave ainda não está cadastrada). O script vai imprimir algo assim:

```
! deploy key NÃO está autorizada no repo. Cole esta public key em:
!   https://github.com/<owner>/<repo>/settings/keys → Add deploy key (read-only)

ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... deploy-key@nexus-backup-cliente

  Pressione Enter quando terminar de colar (ou Ctrl-C pra abortar):
```

**Não pressione Enter ainda.** Faça o seguinte primeiro:

1. **Selecione e copie** a linha `ssh-ed25519 AAAAC...` inteira (incluindo o `deploy-key@...` no final).
2. Abra no navegador: **https://github.com/vagnerss2011-spec/NEXUS-BETA/settings/keys**
3. Clica em **"Add deploy key"** (botão verde no canto superior direito).
4. Preenche:
   - **Title:** algo identificável, ex: `nexus-backup-cliente-X` ou o hostname da VM.
   - **Key:** cola a public key copiada.
   - **Allow write access:** ❌ **deixa desmarcado** (read-only é suficiente — o servidor só vai PUXAR código, nunca empurrar).
5. Clica **"Add key"**.
6. Volta no terminal da VM e **pressione Enter** pra o script continuar.

Pronto. A partir desse momento o servidor consegue:
- `git clone` no install inicial
- `git fetch && git checkout v2.X.Y` em updates futuros

### Como ficou no servidor

Pra conferir depois que tudo funcionou:

```bash
# A chave privada (NÃO COMPARTILHAR — protegida por chmod 600)
ls -la /root/.ssh/nexus_deploy_key

# A pública (essa é a que ficou no GitHub)
cat /root/.ssh/nexus_deploy_key.pub

# Config do SSH apontando github.com pra essa chave
cat /root/.ssh/config

# Teste manual
ssh -T git@github.com
# Esperado: "Hi vagnerss2011-spec/NEXUS-BETA! You've successfully authenticated, but GitHub does not provide shell access."
```

### Revogar acesso depois (se a VM for descomissionada)

Volta na mesma URL do GitHub:
**https://github.com/vagnerss2011-spec/NEXUS-BETA/settings/keys**

Acha a deploy key da VM pelo Title e clica **Delete**. A chave fica imediatamente sem acesso — qualquer `git fetch` futuro daquela VM vai dar `Permission denied`.

---

## Depois disso

Com SSH na 2288 ✅, agora precisa baixar o `install-nexus-backup.sh` pra dentro da VM. **Atenção:** como o repo é privado, `curl https://raw.githubusercontent.com/...` retorna **404 sem autenticação** — você precisa usar um dos 3 caminhos abaixo.

### Opção 1 — Curl com PAT temporário (mais rápido)

Cria um PAT (Personal Access Token) descartável no GitHub e usa direto no `curl`. Depois descarta o token.

1. Vai em **https://github.com/settings/tokens** → **Generate new token (classic)**.
2. Marca só o scope **`repo`** (read). Expiração: **7 days** já basta — você só precisa dele pra esse comando.
3. Copia o token (formato `ghp_xxxxxxxxxx...`).
4. Na VM, roda:

```bash
ssh -p 2288 root@<IP_DA_VM>

curl -fsSL \
  -H "Authorization: token ghp_xxxxxxxxxx" \
  -H "Accept: application/vnd.github.raw" \
  https://api.github.com/repos/vagnerss2011-spec/NEXUS-BETA/contents/scripts/install-nexus-backup.sh \
  -o /tmp/install.sh

bash /tmp/install.sh --interactive
```

5. Quando terminar a instalação, **revoga o PAT** em https://github.com/settings/tokens (clica "Delete" na linha dele). A partir daí o servidor só tem acesso via deploy key (que tem scope só do repo, mais restrito).

### Opção 2 — SCP do seu computador (sem PAT)

Se você já tem o repo clonado no seu computador, copia o script via SCP — sem precisar de token nenhum.

No **seu computador** (não na VM):

```bash
# Manda o script pro /tmp da VM
scp -P 2288 scripts/install-nexus-backup.sh root@<IP_DA_VM>:/tmp/install.sh

# Conecta e roda
ssh -p 2288 root@<IP_DA_VM>
bash /tmp/install.sh --interactive
```

### Opção 3 — Cola o script via heredoc (sem rede)

Útil quando a VM ainda não tem `curl` ou está num link com filtro pesado. No **seu computador**, vê o conteúdo do script:

```bash
cat scripts/install-nexus-backup.sh
```

Copia tudo, conecta na VM e cola dentro de:

```bash
ssh -p 2288 root@<IP_DA_VM>
cat > /tmp/install.sh <<'NEXUSEOF'
<COLA O CONTEÚDO INTEIRO AQUI>
NEXUSEOF
bash /tmp/install.sh --interactive
```

> **Importante:** use `<<'NEXUSEOF'` (com aspas simples) — sem isso o bash tenta expandir `$variáveis` dentro do script e corrompe ele.

---

## A partir daqui

O script rodando, segue o fluxo do passo 3 (deploy key) descrito acima. Quando ele perguntar a public key, você cola no GitHub e pressiona Enter.

Depois que o script terminar (9 passos), o resto está em [INSTALL.md a partir da §5](INSTALL.md#§5--editar-o-env): editar `.env`, certbot, `docker compose up`, criar admin.
