import { useEffect, useMemo, useState } from 'react'
import { Plus, Pencil, Trash2, Play, Router, X, Loader2, CheckCircle, XCircle, FileText, Search, Eye, EyeOff, AlertTriangle, Key, KeyRound } from 'lucide-react'
import api, { getCurrentEmpresa } from '../services/api'
import StatusBadge from '../components/StatusBadge'

const FABRICANTES = ['mikrotik', 'huawei', 'ubiquiti', 'intelbras', 'datacom', 'cisco', 'juniper', 'outro']
const TIPOS = [
  { value: 'roteador', label: 'Roteador' },
  { value: 'olt', label: 'OLT' },
  { value: 'switch', label: 'Switch' },
  { value: 'wireless', label: 'Wireless' },
]
const TIPO_LABEL = Object.fromEntries(TIPOS.map(t => [t.value, t.label]))
const BLANK = { nome: '', ip: '', porta: 22, fabricante: 'mikrotik', tipo: 'roteador', protocolo: 'ssh', usuario_ssh: '', senha_ssh: '', auth_method: 'password', chave_privada: '', chave_passphrase: '' }
const STATUS_FILTROS = [
  { value: 'todos', label: 'Todos os status' },
  { value: 'sucesso', label: 'Backup com sucesso' },
  { value: 'falha', label: 'Backup com falha' },
  { value: 'desconhecido', label: 'Status desconhecido' },
]
const DEFAULT_PORTS = { ssh: 22, telnet: 23 }
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
      // Monta payload limpo: telnet sempre força senha; ssh respeita auth_method
      const isTelnet = form.protocolo === 'telnet'
      const authMethod = isTelnet ? 'password' : (form.auth_method || 'password')
      const payload = {
        nome: form.nome,
        ip: form.ip,
        porta: form.porta,
        fabricante: form.fabricante,
        tipo: form.tipo,
        protocolo: form.protocolo,
        usuario_ssh: form.usuario_ssh,
        auth_method: authMethod,
        empresa_id: empresa?.id,
      }
      if (authMethod === 'ssh_key') {
        if (form.chave_privada && form.chave_privada.trim()) payload.chave_privada = form.chave_privada
        if (form.chave_passphrase) payload.chave_passphrase = form.chave_passphrase
      } else {
        if (form.senha_ssh) payload.senha_ssh = form.senha_ssh
      }
      if (modal === 'new') await api.post('/devices/', payload)
      else await api.put(`/devices/${modal}`, payload)
      fecharModal()
      load()
    } catch (err) {
      const detail = err?.response?.data?.detail || 'Erro ao salvar dispositivo'
      alert(detail)
    } finally { setLoading(false) }
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
          <button onClick={openNew}
            className="flex items-center gap-2 bg-sky-500 hover:bg-sky-400 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
            <Plus size={16} /> Novo Dispositivo
          </button>
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
            <option key={f} value={f} className="capitalize">{f}</option>
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
                  <span className="capitalize text-slate-300">{d.fabricante}</span>
                </td>
                <td className="px-5 py-3.5">
                  <span className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded font-medium ${
                    d.protocolo === 'telnet'
                      ? 'bg-orange-500/20 text-orange-400'
                      : 'bg-sky-500/20 text-sky-400'
                  }`}>
                    {d.protocolo?.toUpperCase() || 'SSH'}
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
                      <button onClick={() => runBackup(d.id)} disabled={runningId === d.id}
                        title="Executar backup agora"
                        className="p-1.5 text-emerald-400 hover:bg-emerald-500/20 rounded transition-colors">
                        {runningId === d.id ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />}
                      </button>
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

      {modal !== null && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-slate-800 rounded-2xl border border-slate-700 w-full max-w-md max-h-[90vh] flex flex-col">
            <div className="flex items-center justify-between p-5 border-b border-slate-700 shrink-0">
              <div className="flex items-center gap-3">
                <h2 className="font-semibold text-white">
                  {modal === 'new' ? 'Novo Dispositivo' : 'Editar Dispositivo'}
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
                { label: 'Porta SSH', key: 'porta', placeholder: '22', type: 'number' },
                { label: 'Usuário SSH', key: 'usuario_ssh', placeholder: 'admin' },
              ].map(({ label, key, placeholder, type = 'text' }) => (
                <div key={key}>
                  <label className="block text-sm text-slate-400 mb-1.5">{label}</label>
                  <input type={type} value={form[key]} onChange={e => setForm(f => ({ ...f, [key]: e.target.value }))}
                    placeholder={placeholder}
                    className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-sky-500 transition-colors" />
                </div>
              ))}
              {form.protocolo !== 'telnet' && (
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

              {(form.protocolo === 'telnet' || form.auth_method !== 'ssh_key') && (
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
                <label className="block text-sm text-slate-400 mb-1.5">Tipo de equipamento</label>
                <select value={form.tipo || 'roteador'} onChange={e => setForm(f => ({ ...f, tipo: e.target.value }))}
                  className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-sky-500">
                  {TIPOS.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-sm text-slate-400 mb-1.5">Fabricante</label>
                <select value={form.fabricante} onChange={e => setForm(f => ({ ...f, fabricante: e.target.value }))}
                  className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-sky-500">
                  {FABRICANTES.map(f => <option key={f} value={f} className="capitalize">{f}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-sm text-slate-400 mb-1.5">Protocolo de acesso</label>
                <div className="flex gap-3">
                  {['ssh', 'telnet'].map(p => (
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
                          // Telnet não suporta chave SSH — força senha
                          auth_method: p === 'telnet' ? 'password' : f.auth_method,
                        }))}
                        className="accent-sky-500"
                      />
                      <span className={`text-sm font-medium ${p === 'telnet' ? 'text-orange-400' : 'text-sky-400'}`}>
                        {p.toUpperCase()}
                      </span>
                      <span className="text-xs text-slate-500">(padrão {DEFAULT_PORTS[p]})</span>
                    </label>
                  ))}
                </div>
              </div>
            </div>
            {confirmandoFab ? (
              <div className="p-5 border-t border-slate-700 bg-amber-500/5 shrink-0">
                <div className="flex items-start gap-3 mb-4">
                  <AlertTriangle size={18} className="text-amber-400 shrink-0 mt-0.5" />
                  <div className="text-sm">
                    <p className="text-white font-medium">Confirme o fabricante</p>
                    <p className="text-slate-400 mt-1">
                      Você selecionou: <span className="capitalize text-amber-300 font-semibold">{form.fabricante}</span>
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
