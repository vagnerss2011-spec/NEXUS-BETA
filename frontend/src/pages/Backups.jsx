import { useEffect, useMemo, useState } from 'react'
import { Download, RefreshCw, Filter, Eye, X, CheckCircle, XCircle, Search, Folder, FolderOpen, ChevronRight, Router } from 'lucide-react'
import api from '../services/api'
import StatusBadge from '../components/StatusBadge'

export default function Backups() {
  const [backups, setBackups] = useState([])
  const [devices, setDevices] = useState([])
  const [filtro, setFiltro] = useState('')
  const [filtroStatus, setFiltroStatus] = useState('')
  const [busca, setBusca] = useState('')
  const [loading, setLoading] = useState(false)
  const [preview, setPreview] = useState(null)
  const [expandidos, setExpandidos] = useState(() => new Set())

  async function load() {
    setLoading(true)
    try {
      const [b, d] = await Promise.all([api.get('/backups/'), api.get('/devices/')])
      setBackups(b.data)
      setDevices(d.data)
    } finally { setLoading(false) }
  }

  useEffect(() => { load() }, [])

  async function download(b) {
    const token = localStorage.getItem('token')
    const res = await fetch(`/api/backups/${b.id}/download`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (!res.ok) return
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    const nome = b.device?.nome?.replace(/\s+/g, '_') ?? `device_${b.device_id}`
    const data = new Date(b.criado_em).toISOString().slice(0, 10)
    a.href = url
    a.download = `backup_${nome}_${data}.txt`
    a.click()
    URL.revokeObjectURL(url)
  }

  const filtered = useMemo(() => {
    const termo = busca.trim().toLowerCase()
    return backups
      .filter(b => filtro ? b.device_id === Number(filtro) : true)
      .filter(b => filtroStatus ? b.status === filtroStatus : true)
      .filter(b => {
        if (!termo) return true
        const alvo = `${b.device?.nome || ''} ${b.device?.ip || ''}`.toLowerCase()
        return alvo.includes(termo)
      })
  }, [backups, filtro, filtroStatus, busca])

  const filtroAtivo = !!(filtro || filtroStatus || busca.trim())

  const grupos = useMemo(() => {
    const map = new Map()
    for (const b of filtered) {
      const id = b.device_id
      if (!map.has(id)) {
        map.set(id, {
          device_id: id,
          device: b.device,
          backups: [],
        })
      }
      map.get(id).backups.push(b)
    }
    const arr = Array.from(map.values())
    for (const g of arr) {
      g.backups.sort((a, b) => new Date(b.criado_em) - new Date(a.criado_em))
      g.sucessos = g.backups.filter(b => b.status === 'sucesso').length
      g.falhas = g.backups.filter(b => b.status === 'falha').length
      g.ultimo = g.backups[0]
    }
    arr.sort((a, b) => {
      const da = a.ultimo ? new Date(a.ultimo.criado_em).getTime() : 0
      const db = b.ultimo ? new Date(b.ultimo.criado_em).getTime() : 0
      return db - da
    })
    return arr
  }, [filtered])

  function toggle(deviceId) {
    setExpandidos(prev => {
      const next = new Set(prev)
      if (next.has(deviceId)) next.delete(deviceId)
      else next.add(deviceId)
      return next
    })
  }

  function expandirTodos() {
    setExpandidos(new Set(grupos.map(g => g.device_id)))
  }

  function recolherTodos() {
    setExpandidos(new Set())
  }

  const todosExpandidos = grupos.length > 0 && expandidos.size === grupos.length

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Backups</h1>
          <p className="text-slate-400 text-sm mt-1">Histórico dos últimos 7 backups por dispositivo</p>
        </div>
        <div className="flex items-center gap-2">
          {grupos.length > 0 && (
            <button onClick={todosExpandidos ? recolherTodos : expandirTodos}
              className="text-slate-400 hover:text-white border border-slate-600 px-3 py-2 rounded-lg text-sm transition-colors">
              {todosExpandidos ? 'Recolher todos' : 'Expandir todos'}
            </button>
          )}
          <button onClick={load} disabled={loading}
            className="flex items-center gap-2 text-slate-400 hover:text-white border border-slate-600 px-3 py-2 rounded-lg text-sm transition-colors">
            <RefreshCw size={15} className={loading ? 'animate-spin' : ''} /> Atualizar
          </button>
        </div>
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <div className="relative flex-1 min-w-[220px]">
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            type="text"
            value={busca}
            onChange={e => setBusca(e.target.value)}
            placeholder="Buscar por nome ou endereço IP..."
            className="w-full bg-slate-800 border border-slate-600 rounded-lg pl-9 pr-3 py-2 text-white text-sm focus:outline-none focus:border-sky-500 transition-colors"
          />
        </div>
        <Filter size={16} className="text-slate-400" />
        <select value={filtro} onChange={e => setFiltro(e.target.value)}
          className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-sky-500">
          <option value="">Todos os dispositivos</option>
          {devices.map(d => <option key={d.id} value={d.id}>{d.nome} ({d.ip})</option>)}
        </select>
        <select value={filtroStatus} onChange={e => setFiltroStatus(e.target.value)}
          className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-sky-500">
          <option value="">Todos os status</option>
          <option value="sucesso">Sucesso</option>
          <option value="falha">Falha</option>
        </select>
        {filtroAtivo && (
          <button onClick={() => { setFiltro(''); setFiltroStatus(''); setBusca('') }}
            className="text-xs text-slate-400 hover:text-white flex items-center gap-1 transition-colors">
            <X size={12} /> Limpar filtros
          </button>
        )}
      </div>

      {grupos.length === 0 ? (
        <div className="bg-slate-800 border border-slate-700 rounded-xl py-12 text-center text-slate-400">
          Nenhum backup encontrado
        </div>
      ) : (
        <div className="space-y-3">
          {grupos.map(g => {
            const aberto = expandidos.has(g.device_id)
            return (
              <div key={g.device_id} className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
                <button
                  onClick={() => toggle(g.device_id)}
                  className="w-full flex items-center gap-3 px-5 py-4 hover:bg-slate-700/40 transition-colors text-left"
                >
                  <ChevronRight
                    size={16}
                    className={`text-slate-500 shrink-0 transition-transform ${aberto ? 'rotate-90' : ''}`}
                  />
                  {aberto
                    ? <FolderOpen size={18} className="text-sky-400 shrink-0" />
                    : <Folder size={18} className="text-sky-400 shrink-0" />}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-medium text-white">{g.device?.nome || `#${g.device_id}`}</span>
                      <span className="text-xs text-slate-400 font-mono">{g.device?.ip}</span>
                      {g.device?.fabricante && (
                        <span className="text-xs text-slate-500 capitalize">· {g.device.fabricante}</span>
                      )}
                    </div>
                    {g.ultimo && (
                      <p className="text-xs text-slate-500 mt-0.5">
                        Último: {new Date(g.ultimo.criado_em).toLocaleString('pt-BR')}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-3 shrink-0">
                    {g.sucessos > 0 && (
                      <span className="text-xs text-emerald-400 font-medium flex items-center gap-1">
                        <CheckCircle size={13} /> {g.sucessos}
                      </span>
                    )}
                    {g.falhas > 0 && (
                      <span className="text-xs text-red-400 font-medium flex items-center gap-1">
                        <XCircle size={13} /> {g.falhas}
                      </span>
                    )}
                    <span className="text-xs text-slate-400 bg-slate-700/60 px-2 py-0.5 rounded">
                      {g.backups.length} backup{g.backups.length !== 1 ? 's' : ''}
                    </span>
                  </div>
                </button>

                {aberto && (
                  <div className="border-t border-slate-700 bg-slate-900/30">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-slate-700/50 text-slate-500 text-left text-xs">
                          <th className="px-5 py-2 font-medium">Data/Hora</th>
                          <th className="px-5 py-2 font-medium">Status</th>
                          <th className="px-5 py-2 font-medium">Erro</th>
                          <th className="px-5 py-2 font-medium text-right">Ações</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-700/50">
                        {g.backups.map(b => (
                          <tr key={b.id} className="hover:bg-slate-700/30 transition-colors">
                            <td className="px-5 py-3 text-slate-300 text-xs font-mono whitespace-nowrap">
                              {new Date(b.criado_em).toLocaleString('pt-BR')}
                            </td>
                            <td className="px-5 py-3"><StatusBadge status={b.status} /></td>
                            <td className="px-5 py-3 text-xs text-red-400 max-w-md truncate">{b.erro || '—'}</td>
                            <td className="px-5 py-3 text-right">
                              <div className="flex items-center gap-1 justify-end">
                                {b.status === 'sucesso' && (
                                  <button onClick={() => setPreview(b)}
                                    className="p-1.5 text-amber-400 hover:bg-amber-500/20 rounded transition-colors" title="Visualizar conteúdo">
                                    <Eye size={15} />
                                  </button>
                                )}
                                {b.status === 'sucesso' && (
                                  <button onClick={() => download(b)}
                                    className="p-1.5 text-sky-400 hover:bg-sky-500/20 rounded transition-colors" title="Baixar backup">
                                    <Download size={15} />
                                  </button>
                                )}
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {preview && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-slate-800 rounded-2xl border border-slate-700 w-full max-w-3xl max-h-[85vh] flex flex-col">
            <div className="flex items-center justify-between p-5 border-b border-slate-700 shrink-0">
              <div className="flex items-center gap-3">
                <CheckCircle size={20} className="text-emerald-400" />
                <div>
                  <h2 className="font-semibold text-white">Conteúdo do Backup</h2>
                  <p className="text-xs text-slate-400">
                    {preview.device?.nome} — {preview.device?.fabricante} — {new Date(preview.criado_em).toLocaleString('pt-BR')}
                  </p>
                </div>
              </div>
              <button onClick={() => setPreview(null)} className="text-slate-400 hover:text-white"><X size={18} /></button>
            </div>
            <div className="p-5 flex-1 overflow-auto">
              <div className="flex items-center gap-2 mb-3">
                <span className="text-xs text-slate-400">{preview.conteudo?.length?.toLocaleString()} caracteres</span>
              </div>
              <pre className="bg-slate-900 rounded-lg p-4 text-xs text-slate-300 whitespace-pre-wrap break-all font-mono leading-relaxed">
                {preview.conteudo}
              </pre>
            </div>
            <div className="p-5 border-t border-slate-700 shrink-0 flex gap-3">
              <button onClick={() => download(preview)}
                className="flex items-center gap-2 bg-sky-500 hover:bg-sky-400 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
                <Download size={14} /> Baixar arquivo
              </button>
              <button onClick={() => setPreview(null)}
                className="bg-slate-700 hover:bg-slate-600 text-white px-4 py-2 rounded-lg text-sm transition-colors">
                Fechar
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
