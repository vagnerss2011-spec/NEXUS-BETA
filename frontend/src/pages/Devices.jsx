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
]
const TIPO_LABEL = Object.fromEntries(TIPOS.map(t => [t.value, t.label]))
const BLANK = { nome: '', ip: '', porta: 22, fabricante: 'mikrotik', tipo: 'roteador', protocolo: 'ssh', usuario_ssh: '', senha_ssh: '', auth_method: 'password', chave_privada: '', chave_passphrase: '', ftp_origem_cidr: '' }
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
const DEFAULT_PORTS = { ssh: 22, telnet: 23, ftp_push: 21, sftp_push: 22, tftp_push: 69 }
const PROTOCOL_LABEL = { ssh: 'SSH', telnet: 'Telnet', ftp_push: 'FTP push', sftp_push: 'SFTP push', tftp_push: 'TFTP push' }
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
    cmd: `# Salva config corrente em flash e envia via FTP.
# Em alguns equipamentos use 'tftp' ou 'sftp' se FTP estiver desabilitado.
save
backup configuration to ftp <SERVIDOR> <USUARIO> <SENHA> backup-huawei.cfg`,
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
  huawei:    `# Huawei VRP (MA5800/MA5680T) — credenciais SFTP são setadas
# SEPARADAMENTE em modo privilege ANTES do backup.
# Validado em 2026-04-27 com OLT MA5800 firmware Gaia_X2.

# 1) Saia do modo config (se estiver) e em modo privilege configure as creds:
quit
ssh sftp set <USUARIO> <SENHA>
display ssh sftp                         # confirma que salvou

# 2) Volte ao modo config e dispare o backup (porta 22 default — VRP não
#    aceita porta custom nesse comando, por isso liberamos a 22 aqui):
config
backup configuration sftp <SERVIDOR> backup-huawei.cfg`,
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
  zte:       `# ZTE ZXR10 (firmware recente):
copy running-config sftp://<USUARIO>:<SENHA>@<SERVIDOR>/backup-zte.cfg`,
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

function montarExemploPush(protocolo, fabricante, servidor, usuario, senha) {
  // protocolo: 'ftp_push' | 'sftp_push' | 'tftp_push'
  const proto = protocolo === 'sftp_push' ? 'sftp' : protocolo === 'tftp_push' ? 'tftp' : 'ftp'
  const examples = PUSH_EXAMPLES[proto] || PUSH_EXAMPLES.ftp
  const tpl = examples[fabricante] || examples.outro
  return tpl
    .replaceAll('<SERVIDOR>', servidor || '<SERVIDOR>')
    .replaceAll('<USUARIO>', usuario || '<USUARIO>')
    .replaceAll('<SENHA>', senha || '<SENHA>')
}

// Mantém compat para chamadas antigas que passavam só FTP
function montarExemploFtp(fabricante, servidor, usuario, senha) {
  return montarExemploPush('ftp_push', fabricante, servidor, usuario, senha)
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
  const user = JSON.parse(localStorage.getItem('user') || '{}')
  const empresa = getCurrentEmpresa()
  const canEdit = ['admin', 'admin_empresa', 'operador'].includes(user.role)
  const canDelete = ['admin', 'admin_empresa'].includes(user.role)

  const fabricantesDisponiveis = useMemo(() => {
    const set = new Set(devices.map(d => d.fabricante).filter(Boolean))
    return Array.from(set).sort()
  }, [devices])

  const devicesFiltrados = useMemo(() => {
    const termo = busca.trim().toLowerCase()
    return devices.filter(d => {
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
  }, [devices, busca, filtroFabricante, filtroTipo, filtroStatus])

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

  function openNew() { setForm(BLANK); setMostrarSenha(false); setConfirmandoFab(false); setModal('new') }
  function openNewFtp() {
    // SFTP é o default recomendado (criptografado). Admin troca se equipamento
    // não suportar SFTP, caindo pra FTP ou TFTP.
    setForm({ ...BLANK, protocolo: 'sftp_push', porta: DEFAULT_PORTS.sftp_push })
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
      const isPush = PUSH_PROTOCOLS.includes(form.protocolo)
      const authMethod = (isTelnet || isPush) ? 'password' : (form.auth_method || 'password')
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
      if (isPush) {
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
      // Criação de FTP push retorna ftp_senha em texto puro UMA vez
      if (resp?.data?.ftp_senha) {
        setCredencialFtp({
          nome: resp.data.nome,
          fabricante: resp.data.fabricante,
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
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Dispositivos</h1>
          <p className="text-slate-400 text-sm mt-1">
            {filtroAtivo
              ? `${devicesFiltrados.length} de ${devices.length} equipamento(s)`
              : `${devices.length} equipamento(s) cadastrado(s)`}
          </p>
        </div>
        {canEdit && (
          <div className="flex items-center gap-2">
            <button onClick={openNewFtp}
              title="Cadastrar dispositivo cujo backup chega via push (FTP, SFTP ou TFTP)"
              className="flex items-center gap-2 bg-violet-500 hover:bg-violet-400 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
              <Upload size={16} /> Novo via Upload
            </button>
            <button onClick={openNew}
              title="Cadastrar dispositivo coletado por SSH ou Telnet"
              className="flex items-center gap-2 bg-sky-500 hover:bg-sky-400 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
              <Plus size={16} /> Novo Dispositivo
            </button>
          </div>
        )}
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
          {TIPOS.map(t => (
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
        <table className="w-full text-sm">
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

      {backupResult && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-slate-800 rounded-2xl border border-slate-700 w-full max-w-2xl max-h-[85vh] flex flex-col">
            <div className="flex items-center justify-between p-5 border-b border-slate-700 shrink-0">
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
            <div className="p-5 flex-1 overflow-auto">
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
            <div className="p-5 border-t border-slate-700 shrink-0 flex gap-3">
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
            <div className="flex items-center justify-between p-5 border-b border-slate-700 shrink-0">
              <div className="flex items-center gap-3">
                <Key size={20} className="text-violet-300" />
                <div>
                  <h2 className="font-semibold text-white">Credencial FTP gerada</h2>
                  <p className="text-xs text-slate-400">{credencialFtp.nome}</p>
                </div>
              </div>
              <button onClick={() => setCredencialFtp(null)} className="text-slate-400 hover:text-white"><X size={18} /></button>
            </div>
            <div className="p-5 space-y-4 flex-1 overflow-y-auto">
              <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg p-3 text-xs text-amber-200 flex items-start gap-2">
                <AlertTriangle size={14} className="shrink-0 mt-0.5" />
                <span><strong>Anote a senha agora.</strong> Por segurança, ela não será mostrada novamente. Se perder, é só regenerar pelo botão na linha do dispositivo.</span>
              </div>
              {[
                { label: 'Servidor', value: window.location.hostname, mono: true },
                { label: 'Porta', value: '21' },
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
                      Comando para configurar no equipamento — <span className="capitalize text-violet-300">{FTP_EXAMPLES[credencialFtp.fabricante]?.titulo || credencialFtp.fabricante}</span>
                    </label>
                    <button
                      type="button"
                      onClick={() => navigator.clipboard?.writeText(montarExemploFtp(
                        credencialFtp.fabricante,
                        window.location.hostname,
                        credencialFtp.ftp_user,
                        credencialFtp.ftp_senha,
                      ))}
                      title="Copiar comando completo"
                      className="text-xs text-slate-400 hover:text-white flex items-center gap-1 px-2 py-0.5 border border-slate-600 rounded transition-colors"
                    >
                      <Copy size={12} /> Copiar
                    </button>
                  </div>
                  <pre className="bg-slate-900 border border-slate-700 rounded-lg p-3 text-[11px] font-mono text-slate-300 whitespace-pre-wrap leading-relaxed max-h-56 overflow-auto">
                    {montarExemploFtp(
                      credencialFtp.fabricante,
                      window.location.hostname,
                      credencialFtp.ftp_user,
                      credencialFtp.ftp_senha,
                    )}
                  </pre>
                </div>
              )}
            </div>
            <div className="p-5 border-t border-slate-700 shrink-0">
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
            <div className="flex items-center justify-between p-5 border-b border-slate-700 shrink-0">
              <div className="flex items-center gap-3">
                <h2 className="font-semibold text-white">
                  {modal === 'new' ? 'Novo Dispositivo'
                    : modal === 'new-ftp' ? 'Novo Dispositivo via Upload'
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
            <div className="p-5 space-y-4 flex-1 overflow-y-auto">
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
                    <div className="grid grid-cols-3 gap-2">
                      {PUSH_PROTOCOLS.map(p => {
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
                        onClick={() => navigator.clipboard?.writeText(montarExemploPush(form.protocolo, form.fabricante, window.location.hostname))}
                        title="Copiar comando"
                        className="text-xs text-slate-400 hover:text-white flex items-center gap-1 px-2 py-0.5 border border-slate-600 rounded transition-colors"
                      >
                        <Copy size={12} /> Copiar
                      </button>
                    </div>
                    <pre className="bg-slate-900 border border-slate-700 rounded-lg p-3 text-[11px] font-mono text-slate-300 whitespace-pre-wrap leading-relaxed max-h-48 overflow-auto">
                      {montarExemploPush(form.protocolo, form.fabricante, window.location.hostname)}
                    </pre>
                    <p className="text-xs text-slate-500 mt-1">
                      {form.protocolo === 'tftp_push'
                        ? 'TFTP não usa user/senha — só o IP de origem identifica o device.'
                        : 'Após salvar, você verá o mesmo comando já com usuário e senha gerados. Adapte ao firmware específico se necessário.'}
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
                  disabled={!!FABRICANTE_TIPO_FIXO[form.fabricante]}
                  className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-sky-500 disabled:opacity-60 disabled:cursor-not-allowed"
                >
                  {TIPOS.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-sm text-slate-400 mb-1.5">Fabricante</label>
                <select
                  value={form.fabricante}
                  onChange={e => {
                    const fab = e.target.value
                    setForm(f => ({
                      ...f,
                      fabricante: fab,
                      // Auto-ajusta o tipo quando o fabricante restringe
                      tipo: FABRICANTE_TIPO_FIXO[fab] || f.tipo,
                    }))
                  }}
                  className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-sky-500"
                >
                  {FABRICANTES.map(f => <option key={f} value={f} className="capitalize">{labelFabricante(f)}</option>)}
                </select>
              </div>
              {modal !== 'new-ftp' && (
              <div>
                <label className="block text-sm text-slate-400 mb-1.5">Protocolo de coleta</label>
                <div className="flex flex-col gap-2">
                  {[
                    { p: 'ssh',       cls: 'text-sky-400',     desc: 'servidor conecta via SSH' },
                    { p: 'telnet',    cls: 'text-orange-400',  desc: 'servidor conecta via Telnet (legado)' },
                    { p: 'sftp_push', cls: 'text-emerald-300', desc: 'equipamento envia via SFTP (criptografado)' },
                    { p: 'ftp_push',  cls: 'text-violet-300',  desc: 'equipamento envia via FTP (plano)' },
                    { p: 'tftp_push', cls: 'text-orange-300',  desc: 'equipamento envia via TFTP (sem auth)' },
                  // Push só aparece em modo edição (já tem botão dedicado pro novo)
                  ].filter(({ p }) => modal === 'new' ? !PUSH_PROTOCOLS.includes(p) : true).map(({ p, cls, desc }) => (
                    <label key={p} className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="radio"
                        name="protocolo"
                        value={p}
                        checked={form.protocolo === p}
                        onChange={() => setForm(f => ({
                          ...f,
                          protocolo: p,
                          porta: f.porta === DEFAULT_PORTS[f.protocolo] ? DEFAULT_PORTS[p] : f.porta,
                          // Telnet/Push não usam chave SSH — força senha
                          auth_method: (p === 'telnet' || PUSH_PROTOCOLS.includes(p)) ? 'password' : f.auth_method,
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
              </div>
              )}
            </div>
            {confirmandoFab ? (
              <div className="p-5 border-t border-slate-700 bg-amber-500/5 shrink-0">
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
              <div className="flex gap-3 p-5 border-t border-slate-700 shrink-0">
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
