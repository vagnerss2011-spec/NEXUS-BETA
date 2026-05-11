import { useEffect, useMemo, useState } from 'react'
import { Plus, Pencil, Trash2, Play, Router, X, Loader2, CheckCircle, XCircle, FileText, Search, Eye, EyeOff, AlertTriangle, Key, KeyRound, Upload, Copy, RefreshCw } from 'lucide-react'
import api, { getCurrentEmpresa } from '../services/api'
import StatusBadge from '../components/StatusBadge'

const FABRICANTES = ['mikrotik', 'mikrotik_v7', 'huawei', 'ubiquiti', 'intelbras', 'datacom', 'cisco', 'juniper', 'zte', 'nokia', 'fiberhome', 'vsolutions', 'outro']

// Label customizado pra fabricantes cujo nome interno (snake_case) ficaria
// estranho ao só capitalizar. Sem entrada → cai no capitalize CSS.
const FABRICANTE_LABEL = {
  mikrotik_v7: 'Mikrotik V7',
  vsolutions: 'VSolutions',
  // ZTE C3XX: família C300/C320/C600 (firmware ZXA10). C6XX Titan é outra
  // família e ainda não tem suporte — quando entrar, vira fabricante próprio.
  zte: 'ZTE C3XX',
}
function labelFabricante(f) {
  return FABRICANTE_LABEL[f] || f
}

// Fabricantes que só fazem sentido com um tipo específico (lock).
// Ex.: VSolutions só fabrica OLT GPON — sem roteador/switch/wireless.
const FABRICANTE_TIPO_FIXO = {
  vsolutions: 'olt',
}
const TIPOS = [
  { value: 'roteador', label: 'Roteador' },
  { value: 'olt', label: 'OLT' },
  { value: 'switch', label: 'Switch' },
  { value: 'wireless', label: 'Wireless' },
  // UNM2000: NMS Fiberhome — recebe push do EMS Set Backup Server. Aparece só
  // na aba dedicada "UNM2000"; filtrado fora da listagem padrão de OLT/equip.
  { value: 'unm2000', label: 'UNM2000 (NMS)' },
]
const TIPO_LABEL = Object.fromEntries(TIPOS.map(t => [t.value, t.label]))
const BLANK = { nome: '', ip: '', porta: 22, fabricante: 'mikrotik', tipo: 'roteador', protocolo: 'ssh', usuario_ssh: '', senha_ssh: '', auth_method: 'password', chave_privada: '', chave_passphrase: '', ftp_origem_cidr: '', api_tls: false }
const STATUS_FILTROS = [
  { value: 'todos', label: 'Todos os status' },
  { value: 'sucesso', label: 'Backup com sucesso' },
  { value: 'falha', label: 'Backup com falha' },
  { value: 'desconhecido', label: 'Status desconhecido' },
]
// SFTP usa porta 22 (default do protocolo) desde 2026-04-27 — SSH do host migrou
// pra 2288 pra liberar a 22 ao container, porque a maioria dos equipamentos
// (OLTs Huawei VRP, ZTE, switches genéricos) não aceita porta SFTP custom no
// comando de backup. Padronizamos em 22 para todos os fabricantes.
// `api` é RouterOS API binária — porta 8728 plain ou 8729 TLS. A escolha entre
// uma e outra é por device.api_tls; o DEFAULT_PORTS aqui serve só pro select
// inicial (sem TLS). Quando o usuário marca a checkbox TLS, a porta troca pra 8729.
const DEFAULT_PORTS = { ssh: 22, telnet: 23, ftp_push: 21, sftp_push: 22, tftp_push: 69, api: 8728 }
const API_PORT_TLS = 8729

// O IP do servidor que aparece nos exemplos vem do backend (/api/info/server),
// que retorna o FTP_MASQUERADE_ADDRESS configurado no .env desta instância.
// Por que dinâmico: cada instância (multi-tenant em redes diferentes) tem IP
// próprio — antes (até v1.2.0) era hardcoded, então uma instância nova
// mostrava o IP da instância antiga nos exemplos. Fallback `<SERVIDOR>` se
// o fetch falhar / .env não tiver FTP_MASQUERADE_ADDRESS preenchido.
const PROTOCOL_LABEL = { ssh: 'SSH', telnet: 'Telnet', api: 'API Mikrotik', ftp_push: 'FTP push', sftp_push: 'SFTP push', tftp_push: 'TFTP push' }
const PUSH_PROTOCOLS = ['sftp_push', 'ftp_push', 'tftp_push']  // ordem do select (SFTP recomendado)
const PUSH_PROTO_INFO = {
  sftp_push: { label: 'SFTP', desc: 'criptografado (recomendado)', porta: 22, cor: 'text-emerald-300' },
  ftp_push:  { label: 'FTP',  desc: 'plano, com user e senha',     porta: 21,   cor: 'text-violet-300' },
  tftp_push: { label: 'TFTP', desc: 'sem auth, identifica por IP', porta: 69,   cor: 'text-orange-300' },
}

// Snippets antigos só de FTP (mantidos pra compatibilidade — agora também
// referenciados pelo PUSH_EXAMPLES.ftp). Placeholders <SERVIDOR>, <USUARIO>,
// <SENHA> são substituídos no modal pós-save.
const FTP_EXAMPLES = {
  mikrotik: {
    titulo: 'Mikrotik RouterOS v6',
    cmd: `# RouterOS v6 — /export já inclui senhas/PSKs por padrão.
/system scheduler add name=backup-nexus interval=1d \\
  on-event="/export file=cfg-backup; \\
            /tool fetch upload=yes mode=ftp \\
              address=<SERVIDOR> port=21 \\
              user=<USUARIO> password=<SENHA> \\
              src-path=cfg-backup.rsc \\
              dst-path=backup-mikrotik.rsc"`,
  },
  mikrotik_v7: {
    titulo: 'Mikrotik RouterOS v7',
    cmd: `# RouterOS v7 — /export mascara senhas por padrão. Use show-sensitive
# para incluir secrets/PSKs/credenciais no arquivo exportado.
/system scheduler add name=backup-nexus interval=1d \\
  on-event="/export show-sensitive file=cfg-backup; \\
            /tool fetch upload=yes mode=ftp \\
              address=<SERVIDOR> port=21 \\
              user=<USUARIO> password=<SENHA> \\
              src-path=cfg-backup.rsc \\
              dst-path=backup-mikrotik.rsc"`,
  },
  huawei: {
    titulo: 'Huawei VRP',
    cmd: `# Huawei VRP (MA5800/MA5680T) — backup automático recorrente via FTP.
# Validado em 2026-04-27 com OLT MA5800 firmware Gaia_X2.
# Em modo config:

# 1) Agendar quando rodar o auto-backup (horários ajustáveis ao seu gosto):
auto-backup period data interval 1 time 05:00
auto-backup period configuration interval 1 time 03:30
auto-backup period data enable
auto-backup period configuration enable

# 2) Definir o destino — onde a config será exportada via FTP.
# Sintaxe: file-server auto-backup configuration primary <IP> <PROTO> <USER> <SENHA> [<PORTA>]
# Porta opcional — default 21 pra FTP.
file-server auto-backup configuration primary <SERVIDOR> FTP <USUARIO> <SENHA>

# (Opcional) Destino do backup de data (eventos/PMs). Se não usar, comente
# a linha 'auto-backup period data enable' acima.
# file-server auto-backup data primary <SERVIDOR> FTP <USUARIO> <SENHA>`,
  },
  ubiquiti: {
    titulo: 'Ubiquiti EdgeOS',
    cmd: `configure
set system config-management commit-archive location \\
    "ftp://<USUARIO>:<SENHA>@<SERVIDOR>/"
commit ; save ; exit`,
  },
  intelbras: {
    titulo: 'Intelbras (CLI tipo Cisco)',
    cmd: `# Pode variar pelo modelo. Em equipamentos Cisco-like:
copy running-config ftp://<USUARIO>:<SENHA>@<SERVIDOR>/backup-intelbras.cfg`,
  },
  datacom: {
    titulo: 'Datacom DmOS',
    cmd: `copy running-config ftp://<USUARIO>:<SENHA>@<SERVIDOR>/backup-datacom.cfg`,
  },
  cisco: {
    titulo: 'Cisco IOS / IOS-XE',
    cmd: `# Modo simples (manual ou via EEM applet diário):
copy running-config ftp://<USUARIO>:<SENHA>@<SERVIDOR>/backup-cisco.cfg

# Modo automático com archive:
configure terminal
 archive
  path ftp://<USUARIO>:<SENHA>@<SERVIDOR>/backup-cisco
  write-memory
  time-period 1440
end`,
  },
  juniper: {
    titulo: 'Juniper JunOS',
    cmd: `set system archival configuration archive-sites \\
    "ftp://<USUARIO>:<SENHA>@<SERVIDOR>" transfer-on-commit
commit`,
  },
  zte: {
    titulo: 'ZTE (ZXR10 / ZXA10)',
    cmd: `# Sintaxe varia entre ZXR10 (switch/router) e ZXA10 (OLT GPON).
# ZXR10 (modo Cisco-like):
copy running-config ftp://<USUARIO>:<SENHA>@<SERVIDOR>/backup-zte.cfg

# ZXA10 OLT (modo legado):
write
upload running-configuration ftp <SERVIDOR> <USUARIO> <SENHA> backup-zte.cfg`,
  },
  nokia: {
    titulo: 'Nokia (SR OS / ISAM)',
    cmd: `# Nokia SR OS (7750/7250):
admin save ftp://<USUARIO>:<SENHA>@<SERVIDOR>/backup-nokia.cfg

# Nokia ISAM/7360 (GPON):
admin save
file upload running-config \\
    ftp://<USUARIO>:<SENHA>@<SERVIDOR>/backup-nokia.cfg`,
  },
  fiberhome: {
    titulo: 'Fiberhome (AN5516 / AN6000)',
    cmd: `# Fiberhome OLT — comandos variam por firmware. Padrão AN5516:
upload startupcfg ftp <SERVIDOR> <USUARIO> <SENHA> backup-fiberhome.cfg

# Algumas builds usam:
cd config
backup ftp <SERVIDOR> <USUARIO> <SENHA>`,
  },
  vsolutions: {
    titulo: 'VSolutions (V-SOL OLT)',
    cmd: `# V-SOL / VSolutions OLT — sintaxe varia por modelo (V1600/V1610/V2724).
# Modelo padrão (modo enable):
enable
upload running-config ftp <SERVIDOR> <USUARIO> <SENHA> backup-vsolutions.cfg

# Em algumas firmwares mais novas:
copy running-config ftp://<USUARIO>:<SENHA>@<SERVIDOR>/backup-vsolutions.cfg

# Salvar antes de exportar é boa prática:
write`,
  },
  outro: {
    titulo: 'Outro fabricante',
    cmd: `# Comando genérico — adapte ao manual do equipamento:
# Conectar, autenticar e enviar arquivo de config para:
#   ftp://<USUARIO>:<SENHA>@<SERVIDOR>:21/<nome-do-backup>.cfg`,
  },
}

// Exemplos por protocolo. SFTP/TFTP variam bastante por equipamento;
// um ❌ no início da string sinaliza que o equipamento não suporta nativamente
// e o admin deve usar outro protocolo.
const SFTP_EXAMPLES = {
  mikrotik:   '# Mikrotik não tem SFTP nativo no /tool fetch. Use FTP ou TFTP.',
  mikrotik_v7:'# Mikrotik não tem SFTP nativo no /tool fetch. Use FTP ou TFTP.',
  huawei:    `# Huawei VRP (MA5800/MA5680T) — backup automático recorrente via SFTP.
# Validado em 2026-04-27 com OLT MA5800 firmware Gaia_X2.
# Em modo config:

# 1) Agendar quando rodar o auto-backup (horários ajustáveis ao seu gosto):
auto-backup period data interval 1 time 05:00
auto-backup period configuration interval 1 time 03:30
auto-backup period data enable
auto-backup period configuration enable

# 2) Definir o destino — onde a config será exportada via SFTP.
# Sintaxe: file-server auto-backup configuration primary <IP> <PROTO> <USER> <SENHA> [<PORTA>]
# Porta opcional — default 22 pra SFTP. NÃO informe porta se SSH host estiver
# em 22 padrão (o nosso está, justamente porque VRP não aceita porta custom
# em comandos antigos como 'backup configuration sftp').
file-server auto-backup configuration primary <SERVIDOR> SFTP <USUARIO> <SENHA>

# (Opcional) Destino do backup de data (eventos/PMs). Se não usar, comente
# a linha 'auto-backup period data enable' acima.
# file-server auto-backup data primary <SERVIDOR> SFTP <USUARIO> <SENHA>`,
  cisco:     `# Cisco IOS-XE suporta SCP/SFTP via porta 22 default.
configure terminal
 ip ssh client algorithm encryption aes128-ctr aes192-ctr aes256-ctr
end
copy running-config scp://<USUARIO>:<SENHA>@<SERVIDOR>/backup-cisco.cfg`,
  juniper:   `set system archival configuration archive-sites \\
    "scp://<USUARIO>:<SENHA>@<SERVIDOR>" transfer-on-commit
commit`,
  datacom:   `# Datacom DmOS — SFTP em firmwares recentes:
copy running-config sftp://<USUARIO>:<SENHA>@<SERVIDOR>/backup-datacom.cfg`,
  intelbras: `# Cisco-like: copy via scp:// na maioria das builds.
copy running-config scp://<USUARIO>:<SENHA>@<SERVIDOR>/backup-intelbras.cfg`,
  zte:       `# ZTE ZXR10 (switch/router — firmware recente):
copy running-config sftp://<USUARIO>:<SENHA>@<SERVIDOR>/backup-zte.cfg

# ZTE C3XX (ZXA10 família C300/C320 etc.) — sintaxe file-server:
# Validado em 2026-05-10 com C320. Importante: o "path" é o NOME do arquivo
# destino (não diretório). NEXUS BACKUP só permite escrita no homedir do user.
file-server manual-backup cfg server-index 1 ipaddress <SERVIDOR> sftp \\
  path zte-c320 user <USUARIO> password <SENHA>

# Acompanhar progresso:
show auto-backup progress manual`,
  nokia:     `admin save sftp://<USUARIO>:<SENHA>@<SERVIDOR>/backup-nokia.cfg`,
  ubiquiti:  `# Ubiquiti EdgeOS:
configure
set system config-management commit-archive location \\
    "scp://<USUARIO>:<SENHA>@<SERVIDOR>/"
commit ; save ; exit`,
  fiberhome: '# Fiberhome OLT (AN5516/AN6000) raramente suporta SFTP. Use FTP ou TFTP.',
  vsolutions:'# V-SOL OLT raramente suporta SFTP. Use FTP ou TFTP.',
  outro:     '# Comando genérico — adapte ao manual:\nsftp://<USUARIO>:<SENHA>@<SERVIDOR>/<arquivo>.cfg',
}

const TFTP_EXAMPLES = {
  mikrotik:   `# RouterOS v6 — TFTP é simples (sem auth):
/system scheduler add name=backup-nexus interval=1d \\
  on-event="/export file=cfg-backup; \\
            /tool fetch upload=yes mode=tftp \\
              address=<SERVIDOR> port=69 \\
              src-path=cfg-backup.rsc \\
              dst-path=backup-mikrotik.rsc"`,
  mikrotik_v7:`# RouterOS v7 — adicione show-sensitive pra incluir senhas:
/system scheduler add name=backup-nexus interval=1d \\
  on-event="/export show-sensitive file=cfg-backup; \\
            /tool fetch upload=yes mode=tftp \\
              address=<SERVIDOR> port=69 \\
              src-path=cfg-backup.rsc \\
              dst-path=backup-mikrotik.rsc"`,
  huawei:    `save
backup configuration to tftp <SERVIDOR> backup-huawei.cfg`,
  cisco:     `copy running-config tftp:
# (responde: Address? <SERVIDOR>  Filename? backup-cisco.cfg)`,
  intelbras: `copy running-config tftp://<SERVIDOR>/backup-intelbras.cfg`,
  datacom:   `copy running-config tftp://<SERVIDOR>/backup-datacom.cfg`,
  juniper:   `# Juniper não tem upload TFTP nativo do JunOS — use FTP ou SFTP.`,
  zte:       `# ZTE ZXR10:
copy running-config tftp://<SERVIDOR>/backup-zte.cfg

# ZTE ZXA10 OLT:
write
upload running-configuration tftp <SERVIDOR> backup-zte.cfg`,
  nokia:     `# Nokia ISAM/7360:
file upload running-config tftp://<SERVIDOR>/backup-nokia.cfg`,
  fiberhome: `# Fiberhome AN5516:
upload startupcfg tftp <SERVIDOR> backup-fiberhome.cfg`,
  vsolutions:`enable
upload running-config tftp <SERVIDOR> backup-vsolutions.cfg`,
  ubiquiti:  `# EdgeOS — exporta config + manual scp/tftp depois.`,
  outro:     `# Comando genérico — adapte ao manual:
# upload running-config tftp <SERVIDOR> <nome-do-arquivo>.cfg`,
}

const PUSH_EXAMPLES = {
  ftp:  Object.fromEntries(Object.entries(FTP_EXAMPLES).map(([k, v]) => [k, v.cmd])),
  sftp: SFTP_EXAMPLES,
  tftp: TFTP_EXAMPLES,
}

// UNM2000 (NMS Fiberhome): São DOIS fluxos distintos no UNM2000, ambos podem
// usar a MESMA credencial FTP gerada aqui. Validado contra os manuais oficiais
// Fiberhome (Apr/2019, V2R7 era):
//
//  - DOC "Backup OLT": agenda backup das OLTs gerenciadas. Cliente Java →
//    System → Parameter Settings → XFTP Server Setting + Policy Task
//    Management → Configuration Export Task.
//
//  - DOC "Backup UNM": backup do banco do próprio UNM2000. Cliente Java →
//    System → UNM Management Tool → abre browser :52302 → EMS Control &
//    Monitor Tools → Import/Export → set backup server + set timer.
//
// O UNM2000 vai pushar então: 1 .zip/dia (banco do UNM) + N .cfg/dia (uma
// por OLT). Backend preserva nome_arquivo pra distinguir (vide processar_upload).
//
// Os docs oficiais V2R7 mostram só FTP (porta 21) — SFTP pode existir em V3R20+
// mas não confirmado. Default aqui = FTP. Senha limitada a 20 chars no campo
// do EMS (backend gera 20 chars quando tipo=unm2000).
const UNM2000_EXAMPLES = {
  ftp: `# ========================================================================
# UNM2000 (NMS Fiberhome) - Configuracao para enviar backups ao NEXUS
# Usa a MESMA credencial FTP nos dois fluxos abaixo.
# ========================================================================

# ----- FLUXO 1: Backup das OLTs gerenciadas (config das OLTs) -----
# Cliente Java do UNM2000:

# 1.1) Cadastrar o servidor FTP destino:
#      System -> Parameter Settings -> Service Configuration ->
#      XFTP Server Setting -> Add
Host Name:        NEXUS-BACKUP
Host IP:          <SERVIDOR>
Protocol Type:    FTP
Username:         <USUARIO>
Password:         <SENHA>      # max 20 caracteres
Port Number:      21
Path:             ./
# -> clicar "Test XFTP" para validar; depois "Apply"

# 1.2) Agendar a tarefa de export:
#      System -> Policy Task Management ->
#      Configuration Export Task -> Create
Task name:        BACKUP-DIARIO-OLT
Enable:           [x]
Task Type:        Every 1 day(s)
Execution time:   03:00:00
# Aba "Object source": marcar as OLTs a serem incluidas
# Aba "Extend information": XFTP Server = NEXUS-BACKUP
Repeated Time:    1
# -> "OK". Para testar agora: selecione a tarefa e "Execute Now".


# ----- FLUXO 2: Backup do banco do proprio UNM2000 (.zip) -----
# Acesso pelo browser na maquina UNM2000:

# 2.1) Abrir UNM Management Tool (porta 52302 do servidor UNM):
#      Cliente Java -> System -> UNM Management Tool
#      OU direto:    http://<IP_DO_UNM2000>:52302/nmtool
#      Login:        Admin / Admin (default - troque!)

# 2.2) EMS Control & Monitor Tools -> Import/Export -> set backup server
backup type:                       [x] local backup  [x] FTP backup
local backup folder:               D:/unm2000/emsback   (default Windows)
maximum number of local backup:    20
ftp ip:                            <SERVIDOR>
port:                              21
ftp username:                      <USUARIO>
ftp password:                      <SENHA>      # mesmas credenciais
# -> "OK"

# 2.3) (opcional) Ajustar horario: set timer
enable timer:    yes
interval time:   1 day
execution time:  3 h 0 min        (03:00 padrao)

# 2.4) Testar: clicar "export" -> "export success"
# Arquivo gerado: YYYYMMDD_HHMMSS_allback.zip (~3 MB, formato binario)
# O NEXUS armazena .zip em base64 no campo conteudo, com nome_arquivo
# preservado para download/auditoria.`,

  sftp: `# UNM2000 SFTP - Atencao: docs oficiais V2R7 (2019) so mostram FTP.
# SFTP pode estar disponivel em V3R20+ no campo Protocol Type do XFTP
# Server Setting. Se sua versao aceitar, troque "FTP" por "SFTP" e a
# porta 21 por 22. O resto do fluxo e identico ao FTP — siga o mesmo
# passo-a-passo do XFTP Server Setting + Configuration Export Task,
# e tambem do Import/Export -> set backup server (se a interface web
# da sua versao expuser opcao SFTP).

Host IP:    <SERVIDOR>
Protocolo:  SFTP
Porta:      22
Username:   <USUARIO>
Password:   <SENHA>             # max 20 caracteres

# Se o campo do EMS nao tiver opcao SFTP, use FTP (porta 21).`,
}

function montarExemploPush(protocolo, fabricante, servidor, usuario, senha, tipo) {
  // protocolo: 'ftp_push' | 'sftp_push' | 'tftp_push'
  // tipo: opcional — quando 'unm2000', usa UNM2000_EXAMPLES (instrucoes do EMS,
  //                  nao snippet de CLI). Demais tipos seguem por fabricante.
  const proto = protocolo === 'sftp_push' ? 'sftp' : protocolo === 'tftp_push' ? 'tftp' : 'ftp'
  let tpl
  if (tipo === 'unm2000') {
    tpl = UNM2000_EXAMPLES[proto] || UNM2000_EXAMPLES.sftp
  } else {
    const examples = PUSH_EXAMPLES[proto] || PUSH_EXAMPLES.ftp
    tpl = examples[fabricante] || examples.outro
  }
  return tpl
    .replaceAll('<SERVIDOR>', servidor || '<SERVIDOR>')
    .replaceAll('<USUARIO>', usuario || '<USUARIO>')
    .replaceAll('<SENHA>', senha || '<SENHA>')
}

// === NTP client ===
// Equipamentos sem hora correta geram timestamps errados nos logs e backups.
// Cada instância nexus-backup roda chrony e aceita conexões NTP do RFC1918 +
// RFC6598 + faixas custom configuradas no install (env NEXUS_EXTRA_CIDRS).
// O placeholder `<SERVIDOR>` é substituído pelo IP da instância atual em runtime.
const NTP_EXAMPLES = {
  mikrotik:   `# RouterOS v6: cliente NTP simples
/system ntp client set enabled=yes primary-ntp=<SERVIDOR>
/system clock set time-zone-name=America/Sao_Paulo`,
  mikrotik_v7:`# RouterOS v7: a sintaxe mudou — usa 'servers' (plural) em vez de 'primary-ntp'
/system ntp client set enabled=yes servers=<SERVIDOR>
/system clock set time-zone-name=America/Sao_Paulo`,
  huawei:    `# Huawei VRP (MA5800/MA5680T) — NTP client recomendado.
# Validado em 2026-04-27 com OLT MA5800 firmware Gaia_X2.
# Em modo config:

# Desabilita o NTP server (a OLT é só cliente, não serve hora pra ninguém)
ntp-service server disable
ntp-service ipv6 server disable

# Permite receber respostas em qualquer interface IPv4; bloqueia IPv6
ntp-service server source-interface all enable
ntp-service ipv6 server source-interface all disable

# NEXUS BACKUP como server primário (preferred)
ntp-service unicast-server <SERVIDOR> preference

# Fallback público — a.ntp.br (NIC.br stratum-1, IP fixo: 200.160.0.8)
ntp-service unicast-server 200.160.0.8

# Timezone Brasília
clock timezone BRT minus 03:00:00`,
  cisco:     `configure terminal
 ntp server <SERVIDOR>
 clock timezone BRT -3 0
end
write memory`,
  juniper:   `set system ntp server <SERVIDOR>
set system time-zone America/Sao_Paulo
commit`,
  datacom:   `# Datacom DmOS:
config
ntp server <SERVIDOR>
clock timezone America/Sao_Paulo
commit`,
  intelbras: `# Intelbras (Cisco-like):
configure terminal
 ntp server <SERVIDOR>
 clock timezone BRT -3
end`,
  zte:       `# ZTE ZXR10/ZXA10:
configure terminal
 ntp server <SERVIDOR>
 clock timezone BRT -3 0
end`,
  nokia:     `# Nokia ISAM/7360:
configure system time ntp server <SERVIDOR>
configure system time zone BRT offset -3
admin save`,
  fiberhome: `# Fiberhome AN5516/AN6000:
set ntp 1 ip <SERVIDOR>
set timezone -3`,
  vsolutions:`# V-SOL OLT:
enable
configure terminal
 ntp server <SERVIDOR>
 clock timezone BRT -3
end`,
  ubiquiti:  `# Ubiquiti EdgeOS:
configure
set system ntp server <SERVIDOR>
set system time-zone America/Sao_Paulo
commit ; save ; exit`,
  outro:     `# Comando genérico — adapte ao manual do fabricante:
ntp server <SERVIDOR>`,
}

function montarExemploNtp(fabricante, servidor) {
  const tpl = NTP_EXAMPLES[fabricante] || NTP_EXAMPLES.outro
  return tpl.replaceAll('<SERVIDOR>', servidor || '<SERVIDOR>')
}

// Extrai mensagem útil do err do axios. Cobre 3 formatos:
// - string (HTTPException simples)
// - array de objetos (Pydantic validation: [{loc, msg, ...}])
// - objeto (HTTPException com payload estruturado)
function extrairErro(err, fallback) {
  const detail = err?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && detail.length > 0) {
    return detail.map(e => e?.msg || JSON.stringify(e)).join('; ')
  }
  if (detail && typeof detail === 'object') {
    return detail.message || detail.detail || JSON.stringify(detail)
  }
  return fallback
}
const formatHostPort = (ip, porta) => {
  if (!ip) return ''
  return ip.includes(':') ? `[${ip}]:${porta}` : `${ip}:${porta}`
}
const formatDeviceId = (id) => String(id).padStart(5, '0')

export default function Devices() {
  const [devices, setDevices] = useState([])
  const [modal, setModal] = useState(null)
  const [form, setForm] = useState(BLANK)
  const [loading, setLoading] = useState(false)
  const [runningId, setRunningId] = useState(null)
  const [backupResult, setBackupResult] = useState(null)
  const [mostrarSenha, setMostrarSenha] = useState(false)
  const [confirmandoFab, setConfirmandoFab] = useState(false)
  const [credencialFtp, setCredencialFtp] = useState(null)
  const [busca, setBusca] = useState('')
  const [filtroFabricante, setFiltroFabricante] = useState('todos')
  const [filtroTipo, setFiltroTipo] = useState('todos')
  const [filtroStatus, setFiltroStatus] = useState('todos')
  // Separa visualmente OLT/equipamentos de NMS UNM2000 — mesmos models, fluxos
  // diferentes (UNM2000 é receptor, sempre push, senha 20 chars). 'olt' é default.
  const [aba, setAba] = useState('olt')
  // IP da instância atual — busca do backend pra dinamizar exemplos por fabricante.
  // Fallback string vazia → aplicar_template/JSX caem em '<SERVIDOR>' visível.
  const [serverIp, setServerIp] = useState('')
  const user = JSON.parse(localStorage.getItem('user') || '{}')
  const empresa = getCurrentEmpresa()
  const canEdit = ['admin', 'admin_empresa', 'operador'].includes(user.role)
  const canDelete = ['admin', 'admin_empresa'].includes(user.role)

  const fabricantesDisponiveis = useMemo(() => {
    const set = new Set(devices.map(d => d.fabricante).filter(Boolean))
    return Array.from(set).sort()
  }, [devices])

  // Particiona por aba antes dos outros filtros — UNM2000 fica isolado da
  // listagem de OLT/equipamentos pra não poluir contadores nem misturar UX.
  const devicesPorAba = useMemo(() => {
    if (aba === 'unm2000') return devices.filter(d => d.tipo === 'unm2000')
    return devices.filter(d => d.tipo !== 'unm2000')
  }, [devices, aba])

  const devicesFiltrados = useMemo(() => {
    const termo = busca.trim().toLowerCase()
    return devicesPorAba.filter(d => {
      if (termo) {
        const alvo = `${d.nome || ''} ${d.ip || ''} ${formatDeviceId(d.id)}`.toLowerCase()
        if (!alvo.includes(termo)) return false
      }
      if (filtroFabricante !== 'todos' && d.fabricante !== filtroFabricante) return false
      if (filtroTipo !== 'todos' && (d.tipo || 'roteador') !== filtroTipo) return false
      if (filtroStatus !== 'todos') {
        const status = d.ultimo_backup_status || 'desconhecido'
        if (status !== filtroStatus) return false
      }
      return true
    })
  }, [devicesPorAba, busca, filtroFabricante, filtroTipo, filtroStatus])

  const filtroAtivo = busca.trim() !== '' || filtroFabricante !== 'todos' || filtroTipo !== 'todos' || filtroStatus !== 'todos'

  function limparFiltros() {
    setBusca('')
    setFiltroFabricante('todos')
    setFiltroTipo('todos')
    setFiltroStatus('todos')
  }

  async function load() {
    const { data } = await api.get('/devices/')
    setDevices(data)
  }

  useEffect(() => { load() }, [])

  // Busca o IP desta instância pra dinamizar exemplos. Falha = fica string
  // vazia, exemplos mostram o placeholder `<SERVIDOR>` (visível pro admin
  // editar). Cacheado em memória do componente — reinicia em F5.
  useEffect(() => {
    api.get('/info/server')
      .then(({ data }) => { if (data?.ftp_endpoint) setServerIp(data.ftp_endpoint) })
      .catch(() => {})
  }, [])

  function openNew() { setForm(BLANK); setMostrarSenha(false); setConfirmandoFab(false); setModal('new') }
  function openNewFtp() {
    // SFTP é o default recomendado (criptografado). Admin troca se equipamento
    // não suportar SFTP, caindo pra FTP ou TFTP.
    setForm({ ...BLANK, protocolo: 'sftp_push', porta: DEFAULT_PORTS.sftp_push })
    setMostrarSenha(false); setConfirmandoFab(false); setModal('new-ftp')
  }
  function openNewUnm2000() {
    // UNM2000 sempre Fiberhome, sempre push. FTP (porta 21) é o default
    // porque os docs oficiais V2R7 (2019) só expõem FTP nos diálogos
    // XFTP Server Setting e Set Backup Server. SFTP pode existir em
    // versões mais recentes — se a UI do EMS aceitar, admin troca.
    // TFTP fica bloqueado (EMS nunca usou) e SSH não faz sentido (NMS
    // não é polado, é receptor).
    setForm({
      ...BLANK,
      tipo: 'unm2000',
      fabricante: 'fiberhome',
      protocolo: 'ftp_push',
      porta: DEFAULT_PORTS.ftp_push,
    })
    setMostrarSenha(false); setConfirmandoFab(false); setModal('new-ftp')
  }
  function openEdit(d) {
    setForm({
      ...d,
      protocolo: d.protocolo || 'ssh',
      auth_method: d.auth_method || 'password',
      senha_ssh: '',
      chave_privada: '',
      chave_passphrase: '',
    })
    setMostrarSenha(false); setConfirmandoFab(false); setModal(d.id)
  }
  function fecharModal() { setModal(null); setConfirmandoFab(false) }

  async function save() {
    // Em "novo dispositivo", força confirmação do fabricante antes de salvar.
    // O comando de coleta de backup depende desse valor — escolha errada
    // gera falha ou backup incorreto.
    if (modal === 'new' && !confirmandoFab) {
      setConfirmandoFab(true)
      return
    }
    setLoading(true)
    try {
      const isTelnet = form.protocolo === 'telnet'
      const isApi = form.protocolo === 'api'
      const isPush = PUSH_PROTOCOLS.includes(form.protocolo)
      const authMethod = (isTelnet || isApi || isPush) ? 'password' : (form.auth_method || 'password')
      const payload = {
        nome: form.nome,
        ip: form.ip,
        porta: form.porta,
        fabricante: form.fabricante,
        tipo: form.tipo,
        protocolo: form.protocolo,
        usuario_ssh: form.usuario_ssh || null,
        auth_method: authMethod,
        empresa_id: empresa?.id,
      }
      if (isApi) {
        payload.api_tls = !!form.api_tls
        if (form.senha_ssh) payload.senha_ssh = form.senha_ssh
      } else if (isPush) {
        payload.ftp_origem_cidr = form.ftp_origem_cidr
      } else if (authMethod === 'ssh_key') {
        if (form.chave_privada && form.chave_privada.trim()) payload.chave_privada = form.chave_privada
        if (form.chave_passphrase) payload.chave_passphrase = form.chave_passphrase
      } else {
        if (form.senha_ssh) payload.senha_ssh = form.senha_ssh
      }
      let resp
      // 'new' (SSH/Telnet) e 'new-ftp' são ambos criação. 'modal' numérico é edição.
      const ehCriacao = modal === 'new' || modal === 'new-ftp'
      if (ehCriacao) resp = await api.post('/devices/', payload)
      else resp = await api.put(`/devices/${modal}`, payload)
      fecharModal()
      // Criação de FTP/SFTP push retorna ftp_senha em texto puro UMA vez —
      // admin precisa anotar pra configurar no equipamento.
      // Device API também gera cred SFTP internamente (pro Plano C de coleta),
      // mas é detalhe interno do NEXUS — backend usa direto, admin não toca.
      // Esconde o modal nesse caso pra não confundir.
      if (resp?.data?.ftp_senha && resp.data.protocolo !== 'api') {
        setCredencialFtp({
          nome: resp.data.nome,
          fabricante: resp.data.fabricante,
          tipo: resp.data.tipo,  // necessário pra UNM2000 mostrar exemplo do EMS no modal
          protocolo: resp.data.protocolo,
          ftp_user: resp.data.ftp_user,
          ftp_senha: resp.data.ftp_senha,
          ftp_origem_cidr: resp.data.ftp_origem_cidr,
        })
      }
      load()
    } catch (err) {
      alert(extrairErro(err, 'Erro ao salvar dispositivo'))
    } finally { setLoading(false) }
  }

  async function regenerarCredencialFtp(d) {
    if (!confirm(`Regenerar a senha FTP de "${d.nome}"?\n\nA senha atual será invalidada e você precisará atualizar a configuração no equipamento.`)) return
    try {
      const { data } = await api.post(`/devices/${d.id}/ftp-credentials/regenerate`)
      setCredencialFtp({
        nome: d.nome,
        fabricante: d.fabricante,
        tipo: d.tipo,  // necessário pra UNM2000 mostrar exemplo do EMS no modal
        protocolo: d.protocolo,
        ftp_user: data.ftp_user,
        ftp_senha: data.ftp_senha,
        ftp_origem_cidr: data.ftp_origem_cidr,
      })
    } catch (err) {
      alert(extrairErro(err, 'Falha ao regenerar credencial'))
    }
  }

  async function del(id) {
    if (!confirm('Remover este dispositivo?')) return
    await api.delete(`/devices/${id}`)
    load()
  }

  async function runBackup(id) {
    setRunningId(id)
    const device = devices.find(d => d.id === id)
    try {
      const { data } = await api.post(`/backups/run/${id}`)
      setBackupResult({ device, backup: data })
      load()
    } catch (err) {
      setBackupResult({ device, backup: { status: 'falha', erro: err?.response?.data?.detail || 'Erro desconhecido' } })
    } finally { setRunningId(null) }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white">
            {aba === 'unm2000' ? 'UNM2000 (NMS Fiberhome)' : 'Dispositivos'}
          </h1>
          <p className="text-slate-400 text-sm mt-1">
            {filtroAtivo
              ? `${devicesFiltrados.length} de ${devicesPorAba.length} ${aba === 'unm2000' ? 'NMS' : 'equipamento(s)'}`
              : `${devicesPorAba.length} ${aba === 'unm2000' ? 'NMS cadastrado(s)' : 'equipamento(s) cadastrado(s)'}`}
          </p>
        </div>
        {canEdit && (
          <div className="flex items-center gap-2 flex-wrap">
            {aba === 'olt' ? (
              <>
                <button onClick={openNewFtp}
                  title="Cadastrar dispositivo cujo backup chega via push (FTP, SFTP ou TFTP)"
                  className="flex items-center gap-2 bg-violet-500 hover:bg-violet-400 text-white px-3 sm:px-4 py-2 rounded-lg text-sm font-medium transition-colors flex-1 sm:flex-none justify-center">
                  <Upload size={16} /> Novo via Upload
                </button>
                <button onClick={openNew}
                  title="Cadastrar dispositivo coletado por SSH ou Telnet"
                  className="flex items-center gap-2 bg-sky-500 hover:bg-sky-400 text-white px-3 sm:px-4 py-2 rounded-lg text-sm font-medium transition-colors flex-1 sm:flex-none justify-center">
                  <Plus size={16} /> Novo Dispositivo
                </button>
              </>
            ) : (
              <button onClick={openNewUnm2000}
                title="Cadastrar instância UNM2000 — gera credencial pra colar no EMS Set Backup Server"
                className="flex items-center gap-2 bg-emerald-500 hover:bg-emerald-400 text-white px-3 sm:px-4 py-2 rounded-lg text-sm font-medium transition-colors flex-1 sm:flex-none justify-center">
                <Plus size={16} /> Novo UNM2000
              </button>
            )}
          </div>
        )}
      </div>

      {/* Tabs OLT vs UNM2000 — separa fluxos completamente diferentes na UX */}
      <div className="flex border-b border-slate-700 -mb-2">
        <button
          onClick={() => setAba('olt')}
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
            aba === 'olt'
              ? 'text-sky-300 border-sky-400'
              : 'text-slate-400 border-transparent hover:text-slate-200'
          }`}
        >
          OLT / Equipamentos
        </button>
        <button
          onClick={() => setAba('unm2000')}
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
            aba === 'unm2000'
              ? 'text-emerald-300 border-emerald-400'
              : 'text-slate-400 border-transparent hover:text-slate-200'
          }`}
        >
          UNM2000 (NMS)
        </button>
      </div>

      <div className="bg-slate-800 border border-slate-700 rounded-xl p-4 flex flex-col md:flex-row md:items-center gap-3">
        <div className="relative flex-1 min-w-[220px]">
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            type="text"
            value={busca}
            onChange={e => setBusca(e.target.value)}
            placeholder="Buscar por ID, nome ou IP..."
            className="w-full bg-slate-900 border border-slate-600 rounded-lg pl-9 pr-3 py-2 text-white text-sm focus:outline-none focus:border-sky-500 transition-colors"
          />
        </div>
        <select
          value={filtroTipo}
          onChange={e => setFiltroTipo(e.target.value)}
          className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-sky-500"
        >
          <option value="todos">Todos os tipos</option>
          {TIPOS
            // Aba OLT esconde UNM2000 do filtro (devicesPorAba já exclui),
            // aba UNM2000 esconde os tipos de equipamento.
            .filter(t => aba === 'unm2000' ? t.value === 'unm2000' : t.value !== 'unm2000')
            .map(t => (
              <option key={t.value} value={t.value}>{t.label}</option>
            ))}
        </select>
        <select
          value={filtroFabricante}
          onChange={e => setFiltroFabricante(e.target.value)}
          className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-sky-500 capitalize"
        >
          <option value="todos">Todos os fabricantes</option>
          {fabricantesDisponiveis.map(f => (
            <option key={f} value={f} className="capitalize">{labelFabricante(f)}</option>
          ))}
        </select>
        <select
          value={filtroStatus}
          onChange={e => setFiltroStatus(e.target.value)}
          className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-sky-500"
        >
          {STATUS_FILTROS.map(s => (
            <option key={s.value} value={s.value}>{s.label}</option>
          ))}
        </select>
        {filtroAtivo && (
          <button
            onClick={limparFiltros}
            className="text-slate-400 hover:text-white text-sm px-3 py-2 border border-slate-600 hover:border-slate-500 rounded-lg transition-colors"
          >
            Limpar
          </button>
        )}
      </div>

      <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
        {/* overflow-x-auto + min-w-[640px] na table: em telas estreitas (< sm)
            o usuário rola horizontalmente em vez de quebrar layout. */}
        <div className="overflow-x-auto">
        <table className="w-full text-sm min-w-[640px]">
          <thead>
            <tr className="border-b border-slate-700 text-slate-400 text-left">
              <th className="px-5 py-3 font-medium">ID</th>
              <th className="px-5 py-3 font-medium">Nome</th>
              <th className="px-5 py-3 font-medium">IP</th>
              <th className="px-5 py-3 font-medium">Tipo</th>
              <th className="px-5 py-3 font-medium">Fabricante</th>
              <th className="px-5 py-3 font-medium">Protocolo</th>
              <th className="px-5 py-3 font-medium">Ativo</th>
              <th className="px-5 py-3 font-medium">Último backup</th>
              {canEdit && <th className="px-5 py-3 font-medium text-right">Ações</th>}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-700">
            {devices.length === 0 && (
              <tr><td colSpan={canEdit ? 9 : 8} className="text-center text-slate-400 py-10">Nenhum dispositivo cadastrado</td></tr>
            )}
            {devices.length > 0 && devicesFiltrados.length === 0 && (
              <tr><td colSpan={canEdit ? 9 : 8} className="text-center text-slate-400 py-10">Nenhum dispositivo corresponde aos filtros</td></tr>
            )}
            {devicesFiltrados.map(d => (
              <tr key={d.id} className="hover:bg-slate-700/40 transition-colors">
                <td className="px-5 py-3.5 text-xs font-mono text-slate-400">{formatDeviceId(d.id)}</td>
                <td className="px-5 py-3.5 font-medium text-white">
                  <span className="flex items-center gap-2">
                    <Router size={16} className="text-sky-400" />{d.nome}
                  </span>
                </td>
                <td className="px-5 py-3.5 text-slate-300 font-mono">{formatHostPort(d.ip, d.porta)}</td>
                <td className="px-5 py-3.5">
                  <span className="inline-flex items-center text-xs px-2 py-0.5 rounded font-medium bg-slate-700 text-slate-200">
                    {TIPO_LABEL[d.tipo] || 'Roteador'}
                  </span>
                </td>
                <td className="px-5 py-3.5">
                  <span className="capitalize text-slate-300">{labelFabricante(d.fabricante)}</span>
                </td>
                <td className="px-5 py-3.5">
                  <span className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded font-medium ${
                    d.protocolo === 'telnet'
                      ? 'bg-orange-500/20 text-orange-400'
                      : d.protocolo === 'api'
                        ? 'bg-pink-500/20 text-pink-400'
                        : d.protocolo === 'ftp_push'
                          ? 'bg-violet-500/20 text-violet-300'
                          : d.protocolo === 'sftp_push'
                            ? 'bg-emerald-500/20 text-emerald-300'
                            : d.protocolo === 'tftp_push'
                              ? 'bg-orange-500/20 text-orange-300'
                              : 'bg-sky-500/20 text-sky-400'
                  }`}>
                    {PUSH_PROTOCOLS.includes(d.protocolo) && <Upload size={11} />}
                    {PROTOCOL_LABEL[d.protocolo] || 'SSH'}
                    {d.protocolo === 'api' && d.api_tls && <span className="text-[10px] opacity-70">TLS</span>}
                  </span>
                </td>
                <td className="px-5 py-3.5">
                  <span className={`text-xs ${d.ativo ? 'text-emerald-400' : 'text-red-400'}`}>
                    {d.ativo ? 'Ativo' : 'Inativo'}
                  </span>
                </td>
                <td className="px-5 py-3.5">
                  {d.ultimo_backup_status ? (
                    <div className="flex flex-col gap-0.5">
                      <StatusBadge status={d.ultimo_backup_status} />
                      {d.ultimo_backup_em && (
                        <span className="text-[10px] text-slate-500 font-mono">
                          {new Date(d.ultimo_backup_em).toLocaleString('pt-BR')}
                        </span>
                      )}
                    </div>
                  ) : (
                    <span className="text-xs text-slate-500 italic">sem backup</span>
                  )}
                </td>
                {canEdit && (
                  <td className="px-5 py-3.5">
                    <div className="flex items-center gap-2 justify-end">
                      {!PUSH_PROTOCOLS.includes(d.protocolo) && (
                        <button onClick={() => runBackup(d.id)} disabled={runningId === d.id}
                          title="Executar backup agora"
                          className="p-1.5 text-emerald-400 hover:bg-emerald-500/20 rounded transition-colors">
                          {runningId === d.id ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />}
                        </button>
                      )}
                      {(d.protocolo === 'ftp_push' || d.protocolo === 'sftp_push') && (
                        <button onClick={() => regenerarCredencialFtp(d)}
                          title={`Regenerar credencial ${d.protocolo === 'sftp_push' ? 'SFTP' : 'FTP'}`}
                          className="p-1.5 text-violet-300 hover:bg-violet-500/20 rounded transition-colors">
                          <RefreshCw size={15} />
                        </button>
                      )}
                      <button onClick={() => openEdit(d)} title="Editar"
                        className="p-1.5 text-sky-400 hover:bg-sky-500/20 rounded transition-colors">
                        <Pencil size={15} />
                      </button>
                      {canDelete && (
                        <button onClick={() => del(d.id)} title="Remover"
                          className="p-1.5 text-red-400 hover:bg-red-500/20 rounded transition-colors">
                          <Trash2 size={15} />
                        </button>
                      )}
                    </div>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      </div>

      {backupResult && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-slate-800 rounded-2xl border border-slate-700 w-full max-w-2xl max-h-[85vh] flex flex-col">
            <div className="flex items-center justify-between p-4 sm:p-5 border-b border-slate-700 shrink-0">
              <div className="flex items-center gap-3">
                {backupResult.backup.status === 'sucesso'
                  ? <CheckCircle size={20} className="text-emerald-400" />
                  : <XCircle size={20} className="text-red-400" />}
                <div>
                  <h2 className="font-semibold text-white">Resultado do Backup Manual</h2>
                  <p className="text-xs text-slate-400">{backupResult.device?.nome} — {backupResult.device?.ip}</p>
                </div>
              </div>
              <button onClick={() => setBackupResult(null)} className="text-slate-400 hover:text-white"><X size={18} /></button>
            </div>
            <div className="p-4 sm:p-5 flex-1 overflow-auto">
              {backupResult.backup.status === 'sucesso' ? (
                <>
                  <div className="flex items-center gap-2 mb-3">
                    <span className="text-xs bg-emerald-500/20 text-emerald-400 px-2 py-0.5 rounded font-medium">SUCESSO</span>
                    <span className="text-xs text-slate-400">{backupResult.backup.conteudo?.length?.toLocaleString()} caracteres exportados</span>
                  </div>
                  <pre className="bg-slate-900 rounded-lg p-4 text-xs text-slate-300 whitespace-pre-wrap break-all font-mono leading-relaxed overflow-auto max-h-96">
                    {backupResult.backup.conteudo}
                  </pre>
                </>
              ) : (
                <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-4">
                  <p className="text-xs text-red-400 font-mono whitespace-pre-wrap">{backupResult.backup.erro || 'Erro desconhecido'}</p>
                </div>
              )}
            </div>
            <div className="p-4 sm:p-5 border-t border-slate-700 shrink-0 flex gap-3">
              {backupResult.backup.status === 'sucesso' && (
                <button
                  onClick={() => {
                    const blob = new Blob([backupResult.backup.conteudo], { type: 'text/plain' })
                    const a = document.createElement('a')
                    a.href = URL.createObjectURL(blob)
                    a.download = `backup_${backupResult.device?.nome}_${new Date().toISOString().slice(0,19).replace(/:/g,'-')}.txt`
                    a.click()
                  }}
                  className="flex items-center gap-2 bg-sky-500 hover:bg-sky-400 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
                  <FileText size={14} /> Baixar arquivo
                </button>
              )}
              <button onClick={() => setBackupResult(null)}
                className="bg-slate-700 hover:bg-slate-600 text-white px-4 py-2 rounded-lg text-sm transition-colors">
                Fechar
              </button>
            </div>
          </div>
        </div>
      )}

      {credencialFtp && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
          <div className="bg-slate-800 rounded-2xl border border-violet-500/40 w-full max-w-lg max-h-[90vh] flex flex-col">
            <div className="flex items-center justify-between p-4 sm:p-5 border-b border-slate-700 shrink-0">
              <div className="flex items-center gap-3">
                <Key size={20} className="text-violet-300" />
                <div>
                  <h2 className="font-semibold text-white">Credencial {PROTOCOL_LABEL[credencialFtp.protocolo] || 'FTP push'} gerada</h2>
                  <p className="text-xs text-slate-400">{credencialFtp.nome}</p>
                </div>
              </div>
              <button onClick={() => setCredencialFtp(null)} className="text-slate-400 hover:text-white"><X size={18} /></button>
            </div>
            <div className="p-4 sm:p-5 space-y-4 flex-1 overflow-y-auto">
              <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg p-3 text-xs text-amber-200 flex items-start gap-2">
                <AlertTriangle size={14} className="shrink-0 mt-0.5" />
                <span><strong>Anote a senha agora.</strong> Por segurança, ela não será mostrada novamente. Se perder, é só regenerar pelo botão na linha do dispositivo.</span>
              </div>
              {[
                { label: 'Servidor', value: serverIp || '<configure FTP_MASQUERADE_ADDRESS no .env>', mono: true },
                { label: 'Porta', value: String(DEFAULT_PORTS[credencialFtp.protocolo] ?? 21) },
                { label: 'Usuário', value: credencialFtp.ftp_user, mono: true },
                { label: 'Senha', value: credencialFtp.ftp_senha, mono: true },
                { label: 'IP de origem permitido', value: credencialFtp.ftp_origem_cidr, mono: true },
              ].map(({ label, value, mono }) => (
                <div key={label}>
                  <label className="block text-xs text-slate-400 mb-1">{label}</label>
                  <div className="flex gap-2">
                    <input
                      readOnly
                      value={value || ''}
                      className={`flex-1 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm ${mono ? 'font-mono' : ''}`}
                    />
                    {value && (
                      <button
                        onClick={() => navigator.clipboard?.writeText(value)}
                        title="Copiar"
                        className="p-2 text-slate-400 hover:text-white border border-slate-600 rounded-lg transition-colors"
                      >
                        <Copy size={14} />
                      </button>
                    )}
                  </div>
                </div>
              ))}
              {credencialFtp.fabricante && (
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="block text-xs text-slate-400">
                      Comando para configurar no equipamento — <span className="capitalize text-violet-300">{labelFabricante(credencialFtp.fabricante)}</span>
                    </label>
                    <button
                      type="button"
                      onClick={() => navigator.clipboard?.writeText(montarExemploPush(
                        credencialFtp.protocolo,
                        credencialFtp.fabricante,
                        serverIp,
                        credencialFtp.ftp_user,
                        credencialFtp.ftp_senha,
                        credencialFtp.tipo,
                      ))}
                      title="Copiar comando completo"
                      className="text-xs text-slate-400 hover:text-white flex items-center gap-1 px-2 py-0.5 border border-slate-600 rounded transition-colors"
                    >
                      <Copy size={12} /> Copiar
                    </button>
                  </div>
                  <pre className="bg-slate-900 border border-slate-700 rounded-lg p-3 text-[11px] font-mono text-slate-300 whitespace-pre-wrap leading-relaxed max-h-56 overflow-auto">
                    {montarExemploPush(
                      credencialFtp.protocolo,
                      credencialFtp.fabricante,
                      serverIp,
                      credencialFtp.ftp_user,
                      credencialFtp.ftp_senha,
                      credencialFtp.tipo,
                    )}
                  </pre>
                </div>
              )}
              {credencialFtp.fabricante && (
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="block text-xs text-slate-400">
                      Configurar NTP client (data/hora correta nos backups) — <span className="capitalize text-emerald-300">{labelFabricante(credencialFtp.fabricante)}</span>
                    </label>
                    <button
                      type="button"
                      onClick={() => navigator.clipboard?.writeText(montarExemploNtp(
                        credencialFtp.fabricante,
                        serverIp,
                      ))}
                      title="Copiar comando NTP"
                      className="text-xs text-slate-400 hover:text-white flex items-center gap-1 px-2 py-0.5 border border-slate-600 rounded transition-colors"
                    >
                      <Copy size={12} /> Copiar
                    </button>
                  </div>
                  <pre className="bg-slate-900 border border-slate-700 rounded-lg p-3 text-[11px] font-mono text-slate-300 whitespace-pre-wrap leading-relaxed max-h-56 overflow-auto">
                    {montarExemploNtp(credencialFtp.fabricante, serverIp)}
                  </pre>
                </div>
              )}
            </div>
            <div className="p-4 sm:p-5 border-t border-slate-700 shrink-0">
              <button onClick={() => setCredencialFtp(null)}
                className="w-full bg-violet-500 hover:bg-violet-400 text-white py-2 rounded-lg text-sm font-medium transition-colors">
                Já anotei, fechar
              </button>
            </div>
          </div>
        </div>
      )}

      {modal !== null && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-slate-800 rounded-2xl border border-slate-700 w-full max-w-md max-h-[90vh] flex flex-col">
            <div className="flex items-center justify-between p-4 sm:p-5 border-b border-slate-700 shrink-0">
              <div className="flex items-center gap-3">
                <h2 className="font-semibold text-white">
                  {modal === 'new' ? 'Novo Dispositivo'
                    : modal === 'new-ftp' && form.tipo === 'unm2000' ? 'Novo UNM2000 (NMS Fiberhome)'
                    : modal === 'new-ftp' ? 'Novo Dispositivo via Upload'
                    : form.tipo === 'unm2000' ? 'Editar UNM2000'
                    : 'Editar Dispositivo'}
                </h2>
                {modal !== 'new' && typeof modal === 'number' && (
                  <span className="text-xs font-mono text-slate-400 bg-slate-900 border border-slate-600 px-2 py-0.5 rounded">
                    {formatDeviceId(modal)}
                  </span>
                )}
              </div>
              <button onClick={fecharModal} className="text-slate-400 hover:text-white"><X size={18} /></button>
            </div>
            <div className="p-4 sm:p-5 space-y-4 flex-1 overflow-y-auto">
              {[
                { label: 'Nome', key: 'nome', placeholder: 'Router-Core-SP' },
                { label: 'IP (IPv4 ou IPv6)', key: 'ip', placeholder: '192.168.1.1 ou 2001:db8::1' },
                {
                  label: PUSH_PROTOCOLS.includes(form.protocolo)
                    ? `Porta ${PUSH_PROTO_INFO[form.protocolo]?.label || 'Push'}`
                    : 'Porta SSH',
                  key: 'porta',
                  placeholder: String(DEFAULT_PORTS[form.protocolo] || 22),
                  type: 'number',
                },
                ...(PUSH_PROTOCOLS.includes(form.protocolo) ? [] : [
                  { label: 'Usuário SSH', key: 'usuario_ssh', placeholder: 'admin' },
                ]),
              ].map(({ label, key, placeholder, type = 'text' }) => (
                <div key={key}>
                  <label className="block text-sm text-slate-400 mb-1.5">{label}</label>
                  <input type={type} value={form[key] || ''} onChange={e => setForm(f => ({ ...f, [key]: e.target.value }))}
                    placeholder={placeholder}
                    className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-sky-500 transition-colors" />
                </div>
              ))}
              {PUSH_PROTOCOLS.includes(form.protocolo) && (
                <>
                  <div className="bg-violet-500/5 border border-violet-500/30 rounded-lg p-3 text-xs text-slate-300">
                    <div className="flex items-start gap-2">
                      <Upload size={14} className="text-violet-300 shrink-0 mt-0.5" />
                      <div>
                        <p className="font-medium text-white">Modo recebimento</p>
                        <p className="mt-1">O servidor não conecta no equipamento — o equipamento envia o backup pra cá. Escolha o protocolo conforme o suporte do equipamento.</p>
                      </div>
                    </div>
                  </div>
                  <div>
                    <label className="block text-sm text-slate-400 mb-1.5">Protocolo de upload</label>
                    <div className={`grid gap-2 ${form.tipo === 'unm2000' ? 'grid-cols-2' : 'grid-cols-3'}`}>
                      {PUSH_PROTOCOLS
                        // UNM2000 não usa TFTP — EMS Set Backup Server só FTP/SFTP
                        .filter(p => form.tipo !== 'unm2000' || p !== 'tftp_push')
                        .map(p => {
                        const info = PUSH_PROTO_INFO[p]
                        const ativo = form.protocolo === p
                        return (
                          <button
                            type="button"
                            key={p}
                            onClick={() => setForm(f => ({
                              ...f,
                              protocolo: p,
                              porta: DEFAULT_PORTS[p],
                              // TFTP exige /32 — limpa CIDR /24 se admin trocou pra TFTP
                              ftp_origem_cidr: p === 'tftp_push' && f.ftp_origem_cidr?.endsWith('/24')
                                ? ''
                                : f.ftp_origem_cidr,
                            }))}
                            className={`flex flex-col items-center gap-0.5 py-2 rounded-lg text-sm font-medium border transition-colors ${
                              ativo
                                ? 'bg-slate-700 border-violet-500 text-white'
                                : 'bg-slate-900 border-slate-600 text-slate-400 hover:border-slate-500'
                            }`}
                          >
                            <span className={info.cor}>{info.label}</span>
                            <span className="text-[10px] text-slate-500">porta {info.porta}</span>
                          </button>
                        )
                      })}
                    </div>
                    <p className="text-xs text-slate-500 mt-1.5">
                      {PUSH_PROTO_INFO[form.protocolo]?.desc}
                    </p>
                    {form.tipo === 'unm2000' && (
                      <p className="text-xs text-emerald-300/80 mt-1.5 flex items-start gap-1">
                        <span>ℹ</span>
                        <span>UNM2000: a senha gerada terá <strong>20 caracteres</strong> (limite do campo no EMS Set Backup Server). TFTP não é suportado pelo EMS.</span>
                      </p>
                    )}
                  </div>
                  <div>
                    <label className="block text-sm text-slate-400 mb-1.5">
                      IP de origem permitido (CIDR IPv4)
                      {form.protocolo === 'tftp_push' && (
                        <span className="text-xs text-orange-300 ml-1">
                          — obrigatoriamente /32 para TFTP
                        </span>
                      )}
                    </label>
                    <input
                      type="text"
                      value={form.ftp_origem_cidr || ''}
                      onChange={e => setForm(f => ({ ...f, ftp_origem_cidr: e.target.value }))}
                      placeholder={form.protocolo === 'tftp_push' ? '187.123.45.10/32' : '187.123.45.10/32 ou 187.123.45.0/24'}
                      className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm font-mono focus:outline-none focus:border-violet-500 transition-colors"
                    />
                    <p className="text-xs text-slate-500 mt-1">
                      {form.protocolo === 'tftp_push'
                        ? 'TFTP é anonymous — o IP é a única identificação do device. /24 daria ambiguidade entre vários devices na mesma faixa.'
                        : 'Apenas conexões originadas deste range serão aceitas. Use /32 para um IP fixo, /24 para uma faixa.'}
                    </p>
                  </div>
                  <div>
                    <div className="flex items-center justify-between mb-1.5">
                      <label className="block text-sm text-slate-400">
                        Exemplo {PUSH_PROTO_INFO[form.protocolo]?.label} — <span className="capitalize text-violet-300">{labelFabricante(form.fabricante)}</span>
                      </label>
                      <button
                        type="button"
                        onClick={() => navigator.clipboard?.writeText(montarExemploPush(form.protocolo, form.fabricante, serverIp, undefined, undefined, form.tipo))}
                        title="Copiar comando"
                        className="text-xs text-slate-400 hover:text-white flex items-center gap-1 px-2 py-0.5 border border-slate-600 rounded transition-colors"
                      >
                        <Copy size={12} /> Copiar
                      </button>
                    </div>
                    <pre className="bg-slate-900 border border-slate-700 rounded-lg p-3 text-[11px] font-mono text-slate-300 whitespace-pre-wrap leading-relaxed max-h-48 overflow-auto">
                      {montarExemploPush(form.protocolo, form.fabricante, serverIp, undefined, undefined, form.tipo)}
                    </pre>
                    <p className="text-xs text-slate-500 mt-1">
                      {form.protocolo === 'tftp_push'
                        ? 'TFTP não usa user/senha — só o IP de origem identifica o device.'
                        : 'Após salvar, você verá o mesmo comando já com usuário e senha gerados. Adapte ao firmware específico se necessário.'}
                    </p>
                  </div>
                  <div>
                    <div className="flex items-center justify-between mb-1.5">
                      <label className="block text-sm text-slate-400">
                        Configurar NTP client (data/hora correta nos backups) — <span className="capitalize text-emerald-300">{labelFabricante(form.fabricante)}</span>
                      </label>
                      <button
                        type="button"
                        onClick={() => navigator.clipboard?.writeText(montarExemploNtp(form.fabricante, serverIp))}
                        title="Copiar comando NTP"
                        className="text-xs text-slate-400 hover:text-white flex items-center gap-1 px-2 py-0.5 border border-slate-600 rounded transition-colors"
                      >
                        <Copy size={12} /> Copiar
                      </button>
                    </div>
                    <pre className="bg-slate-900 border border-slate-700 rounded-lg p-3 text-[11px] font-mono text-slate-300 whitespace-pre-wrap leading-relaxed max-h-48 overflow-auto">
                      {montarExemploNtp(form.fabricante, serverIp)}
                    </pre>
                    <p className="text-xs text-slate-500 mt-1">
                      Equipamentos sem NTP geram timestamps errados. O servidor aceita NTP em UDP/123 dos CIDRs RFC1918 + RFC6598 + 45.5.16.0/22.
                    </p>
                  </div>
                </>
              )}

              {form.protocolo !== 'telnet' && !PUSH_PROTOCOLS.includes(form.protocolo) && (
                <div>
                  <label className="block text-sm text-slate-400 mb-1.5">Método de autenticação</label>
                  <div className="grid grid-cols-2 gap-2">
                    {[
                      { value: 'password', label: 'Senha', icon: KeyRound },
                      { value: 'ssh_key', label: 'Chave SSH', icon: Key },
                    ].map(({ value, label, icon: Icon }) => (
                      <button
                        type="button"
                        key={value}
                        onClick={() => setForm(f => ({ ...f, auth_method: value }))}
                        className={`flex items-center justify-center gap-2 py-2 rounded-lg text-sm font-medium border transition-colors ${
                          form.auth_method === value
                            ? 'bg-sky-500/15 border-sky-500 text-sky-300'
                            : 'bg-slate-900 border-slate-600 text-slate-400 hover:border-slate-500'
                        }`}
                      >
                        <Icon size={14} /> {label}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {!PUSH_PROTOCOLS.includes(form.protocolo) && (form.protocolo === 'telnet' || form.auth_method !== 'ssh_key') && (
                <div>
                  <label className="block text-sm text-slate-400 mb-1.5">
                    Senha {form.protocolo !== 'telnet' && form.auth_method === 'password' ? 'SSH' : ''}
                    {modal !== 'new' && (
                      <span className="text-xs text-slate-500 ml-1">(deixe em branco para manter)</span>
                    )}
                  </label>
                  <div className="relative">
                    <input
                      type={mostrarSenha ? 'text' : 'password'}
                      value={form.senha_ssh}
                      onChange={e => setForm(f => ({ ...f, senha_ssh: e.target.value }))}
                      placeholder="••••••••"
                      className="w-full bg-slate-900 border border-slate-600 rounded-lg pl-3 pr-10 py-2 text-white text-sm focus:outline-none focus:border-sky-500 transition-colors"
                    />
                    <button
                      type="button"
                      onClick={() => setMostrarSenha(v => !v)}
                      title={mostrarSenha ? 'Ocultar senha' : 'Mostrar senha'}
                      className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-slate-400 hover:text-sky-400 transition-colors"
                    >
                      {mostrarSenha ? <EyeOff size={16} /> : <Eye size={16} />}
                    </button>
                  </div>
                  <label className="mt-2 flex items-center gap-2 text-xs text-slate-400 cursor-pointer select-none">
                    <input
                      type="checkbox"
                      checked={mostrarSenha}
                      onChange={e => setMostrarSenha(e.target.checked)}
                      className="accent-sky-500"
                    />
                    Mostrar senha
                  </label>
                </div>
              )}

              {form.protocolo !== 'telnet' && form.auth_method === 'ssh_key' && (
                <>
                  <div>
                    <label className="block text-sm text-slate-400 mb-1.5">
                      Chave privada (PEM ou OpenSSH)
                      {modal !== 'new' && (
                        <span className="text-xs text-slate-500 ml-1">(deixe em branco para manter)</span>
                      )}
                    </label>
                    <textarea
                      value={form.chave_privada}
                      onChange={e => setForm(f => ({ ...f, chave_privada: e.target.value }))}
                      placeholder={"-----BEGIN OPENSSH PRIVATE KEY-----\n...\n-----END OPENSSH PRIVATE KEY-----"}
                      rows={6}
                      className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-white text-xs font-mono focus:outline-none focus:border-sky-500 transition-colors resize-y"
                    />
                    <p className="text-xs text-slate-500 mt-1">
                      Cole o conteúdo do arquivo da chave privada. A chave pública correspondente precisa estar cadastrada no equipamento.
                    </p>
                  </div>
                  <div>
                    <label className="block text-sm text-slate-400 mb-1.5">
                      Passphrase da chave <span className="text-xs text-slate-500">(opcional)</span>
                    </label>
                    <input
                      type="password"
                      value={form.chave_passphrase}
                      onChange={e => setForm(f => ({ ...f, chave_passphrase: e.target.value }))}
                      placeholder="Deixe vazio se a chave não for protegida"
                      className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-sky-500 transition-colors"
                    />
                  </div>
                </>
              )}
              <div>
                <label className="block text-sm text-slate-400 mb-1.5">
                  Tipo de equipamento
                  {FABRICANTE_TIPO_FIXO[form.fabricante] && (
                    <span className="text-xs text-slate-500 ml-1">
                      (fixo para {labelFabricante(form.fabricante)})
                    </span>
                  )}
                </label>
                <select
                  value={form.tipo || 'roteador'}
                  onChange={e => setForm(f => ({ ...f, tipo: e.target.value }))}
                  disabled={!!FABRICANTE_TIPO_FIXO[form.fabricante] || form.tipo === 'unm2000'}
                  className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-sky-500 disabled:opacity-60 disabled:cursor-not-allowed"
                >
                  {TIPOS
                    // UNM2000 é cadastrado pela aba dedicada — não aparece como
                    // opção no fluxo de OLT/equip, evitando configuração mista
                    // (ex.: OLT marcada como tipo=unm2000 sem querer).
                    .filter(t => form.tipo === 'unm2000' ? t.value === 'unm2000' : t.value !== 'unm2000')
                    .map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-sm text-slate-400 mb-1.5">Fabricante</label>
                {form.tipo === 'unm2000' ? (
                  // UNM2000 só existe no ecossistema Fiberhome — não tem por que
                  // mostrar select. Quando entrar suporte a outros NMS (Nokia 5520
                  // AMS, Huawei iMaster NCE, etc.) vira um select filtrado pelos
                  // fabricantes que têm produto NMS.
                  <div className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm flex items-center justify-between">
                    <span className="capitalize">Fiberhome</span>
                    <span className="text-[10px] text-slate-500 uppercase tracking-wider">UNM2000 só existe na Fiberhome</span>
                  </div>
                ) : (
                  <select
                    value={form.fabricante}
                    onChange={e => {
                      const fab = e.target.value
                      setForm(f => {
                        // ZTE C3XX só funciona via Telnet (SSH tem rate-limit
                        // interno da OLT, validado em v1.2.6/v1.2.9 — coleta
                        // sempre trunca). Força telnet ao escolher ZTE.
                        const forcaTelnet = fab === 'zte' && !PUSH_PROTOCOLS.includes(f.protocolo)
                        return {
                          ...f,
                          fabricante: fab,
                          // Auto-ajusta o tipo quando o fabricante restringe
                          tipo: FABRICANTE_TIPO_FIXO[fab] || f.tipo,
                          protocolo: forcaTelnet ? 'telnet' : f.protocolo,
                          porta: forcaTelnet ? DEFAULT_PORTS.telnet : f.porta,
                          auth_method: forcaTelnet ? 'password' : f.auth_method,
                        }
                      })
                    }}
                    className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-sky-500"
                  >
                    {FABRICANTES.map(f => <option key={f} value={f} className="capitalize">{labelFabricante(f)}</option>)}
                  </select>
                )}
              </div>
              {modal !== 'new-ftp' && !PUSH_PROTOCOLS.includes(form.protocolo) && (
              <div>
                <label className="block text-sm text-slate-400 mb-1.5">Protocolo de coleta</label>
                <div className="flex flex-col gap-2">
                  {[
                    { p: 'ssh',       cls: 'text-sky-400',     desc: 'servidor conecta via SSH' },
                    { p: 'telnet',    cls: 'text-orange-400',  desc: 'servidor conecta via Telnet (legado)' },
                    { p: 'api',       cls: 'text-pink-400',    desc: 'RouterOS API binária (Mikrotik v6/v7)' },
                    { p: 'sftp_push', cls: 'text-emerald-300', desc: 'equipamento envia via SFTP (criptografado)' },
                    { p: 'ftp_push',  cls: 'text-violet-300',  desc: 'equipamento envia via FTP (plano)' },
                    { p: 'tftp_push', cls: 'text-orange-300',  desc: 'equipamento envia via TFTP (sem auth)' },
                  ].filter(({ p }) => {
                    // Push só aparece via fluxo dedicado "Novo via Upload" — esconde
                    // de criar/editar SSH/Telnet pra evitar mudança acidental do
                    // modo de coleta. Quem precisa converter, recria o device.
                    if (PUSH_PROTOCOLS.includes(p)) return false
                    // ZTE C3XX: SSH bloqueado (rate-limit interno trunca coleta).
                    if (form.fabricante === 'zte' && p === 'ssh') return false
                    // RouterOS API binária: só faz sentido em Mikrotik v6/v7.
                    if (p === 'api' && !['mikrotik', 'mikrotik_v7'].includes(form.fabricante)) return false
                    return true
                  }).map(({ p, cls, desc }) => (
                    <label key={p} className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="radio"
                        name="protocolo"
                        value={p}
                        checked={form.protocolo === p}
                        onChange={() => setForm(f => ({
                          ...f,
                          protocolo: p,
                          // Sempre reseta pra porta default do novo protocolo. Antes preservava
                          // porta custom se o usuário tinha mudado, mas isso fazia ele esquecer
                          // de ajustar ao trocar protocolo — ex.: SSH custom em 2399 → API ficava
                          // em 2399 e quebrava com 'Unknown control byte 0xff' (validado em prod).
                          // Se quiser porta custom no novo protocolo, edita o campo Porta depois.
                          porta: p === 'api' ? (f.api_tls ? API_PORT_TLS : DEFAULT_PORTS.api) : DEFAULT_PORTS[p],
                          // Telnet / Push / API não usam chave SSH — força senha
                          auth_method: (p === 'telnet' || p === 'api' || PUSH_PROTOCOLS.includes(p)) ? 'password' : f.auth_method,
                        }))}
                        className="accent-sky-500"
                      />
                      <span className={`text-sm font-medium ${cls}`}>
                        {PROTOCOL_LABEL[p]}
                      </span>
                      <span className="text-xs text-slate-500">{desc} (porta {DEFAULT_PORTS[p]})</span>
                    </label>
                  ))}
                </div>
                {/* Checkbox TLS — só faz sentido em protocolo=api (RouterOS API
                    binária). Toggle muda a porta entre 8728 (plain) e 8729 (TLS)
                    automaticamente, exceto se o usuário já mudou a porta manual. */}
                {form.protocolo === 'api' && (
                  <label className="flex items-center gap-2 mt-3 cursor-pointer text-sm text-slate-300">
                    <input
                      type="checkbox"
                      checked={!!form.api_tls}
                      onChange={e => setForm(f => ({
                        ...f,
                        api_tls: e.target.checked,
                        // Sempre reset pra default do modo (plain/TLS). Se quiser porta custom,
                        // edita o campo Porta depois. Manter porta antiga ao toggle confunde
                        // mais que ajuda (validado em prod com erro 0xff em v1.4.0).
                        porta: e.target.checked ? API_PORT_TLS : DEFAULT_PORTS.api,
                      }))}
                      className="accent-pink-500"
                    />
                    <span>Conexão TLS (porta {API_PORT_TLS})</span>
                    <span className="text-xs text-slate-500">— exige cert configurado no Mikrotik</span>
                  </label>
                )}
              </div>
              )}
            </div>
            {confirmandoFab ? (
              <div className="p-4 sm:p-5 border-t border-slate-700 bg-amber-500/5 shrink-0">
                <div className="flex items-start gap-3 mb-4">
                  <AlertTriangle size={18} className="text-amber-400 shrink-0 mt-0.5" />
                  <div className="text-sm">
                    <p className="text-white font-medium">Confirme o fabricante</p>
                    <p className="text-slate-400 mt-1">
                      Você selecionou: <span className="capitalize text-amber-300 font-semibold">{labelFabricante(form.fabricante)}</span>
                    </p>
                    <p className="text-xs text-slate-500 mt-2">
                      O comando de coleta de backup depende deste valor — escolha errada gera falha ou backup incorreto.
                    </p>
                  </div>
                </div>
                <div className="flex gap-3">
                  <button onClick={() => setConfirmandoFab(false)}
                    className="flex-1 bg-slate-700 hover:bg-slate-600 text-white py-2 rounded-lg text-sm transition-colors">
                    Voltar e revisar
                  </button>
                  <button onClick={save} disabled={loading}
                    className="flex-1 bg-emerald-500 hover:bg-emerald-400 disabled:opacity-60 text-white py-2 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2">
                    {loading && <Loader2 size={14} className="animate-spin" />}
                    Confirmar e salvar
                  </button>
                </div>
              </div>
            ) : (
              <div className="flex gap-3 p-4 sm:p-5 border-t border-slate-700 shrink-0">
                <button onClick={fecharModal}
                  className="flex-1 bg-slate-700 hover:bg-slate-600 text-white py-2 rounded-lg text-sm transition-colors">
                  Cancelar
                </button>
                <button onClick={save} disabled={loading}
                  className="flex-1 bg-sky-500 hover:bg-sky-400 disabled:opacity-60 text-white py-2 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2">
                  {loading && <Loader2 size={14} className="animate-spin" />}
                  Salvar
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
