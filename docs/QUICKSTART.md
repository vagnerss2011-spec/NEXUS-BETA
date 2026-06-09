# Quickstart — Preparar a VM antes do install

> **Mudou na v2.5.1:** o repositório agora é **público**. Não precisa mais de
> deploy key nem de PAT pra baixar o código — o clone e o `curl` do script
> funcionam **sem credencial nenhuma**. Este doc ficou bem mais curto.

Sobrou **1 passo manual** que você precisa fazer **antes** de rodar
`install-nexus-backup.sh`:

1. **Mover o SSH do host pra porta 2288** (a porta 22 vai ficar pro container SFTP do app)

Pra contexto completo (provisionamento da VM, DNS, certbot, criar admin, etc.) ver [INSTALL.md](INSTALL.md). Este doc cobre só a preparação mínima.

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

## 2. Baixar o `install-nexus-backup.sh`

Com o repo público, basta um `curl` anônimo direto no `raw.githubusercontent.com` — **sem token, sem deploy key**.

```bash
ssh -p 2288 root@<IP_DA_VM>

curl -fsSL https://raw.githubusercontent.com/vagnerss2011-spec/NEXUS-BETA/backup/scripts/install-nexus-backup.sh -o /tmp/install.sh

bash /tmp/install.sh --interactive
```

O script clona o repo em `/root/NEXUS-BETA` (HTTPS público, clone anônimo) e faz checkout na última tag. Não pergunta credencial nenhuma.

### Fallbacks (rede restrita)

Se a VM estiver num link que bloqueia `raw.githubusercontent.com` (filtro de proxy, etc.), use um destes — nenhum precisa de credencial:

**SCP do seu computador** (se você já tem o repo clonado localmente):

```bash
# No SEU computador:
scp -P 2288 scripts/install-nexus-backup.sh root@<IP_DA_VM>:/tmp/install.sh
ssh -p 2288 root@<IP_DA_VM>
bash /tmp/install.sh --interactive
```

**Colar via heredoc** (VM sem `curl` ou link muito filtrado):

```bash
# No SEU computador, copia o conteúdo:
cat scripts/install-nexus-backup.sh

# Na VM, cola dentro de:
ssh -p 2288 root@<IP_DA_VM>
cat > /tmp/install.sh <<'NEXUSEOF'
<COLA O CONTEÚDO INTEIRO AQUI>
NEXUSEOF
bash /tmp/install.sh --interactive
```

> **Importante:** use `<<'NEXUSEOF'` (com aspas simples) — sem isso o bash tenta expandir `$variáveis` dentro do script e corrompe ele.

---

## (Legado) Fork privado com deploy key

Só relevante se você mantém um **fork privado** do projeto. O repo oficial é público, então pule esta seção.

Nesse caso, rode o install apontando pro fork via SSH e o script cuida da deploy key automaticamente:

```bash
REPO_URL=git@github.com:SEU-USER/SEU-FORK.git bash /tmp/install.sh --interactive
```

No passo 3 (Clone do repo) ele gera `/root/.ssh/nexus_deploy_key`, configura o `~/.ssh/config` e, se a chave ainda não estiver cadastrada, imprime a public key pra você colar em **Settings → Deploy Keys → Add deploy key** (read-only) do seu fork. Depois pressione Enter pra continuar.

---

## A partir daqui

Com SSH na 2288 ✅ e o script baixado, é só rodar `bash /tmp/install.sh --interactive` e seguir os 9 passos. Depois que o script terminar, o resto está em [INSTALL.md a partir da §5](INSTALL.md#§5--editar-o-env): editar `.env`, certbot, `docker compose up`, criar admin.
